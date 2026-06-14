"""build_full_pipeline per Contract 3.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 3:

Typed I/O contract:
    PipelineDeps (Pydantic frozen) → typed dependency injection
    Pipeline (Protocol) → exposes .ainvoke(SearchRequest) -> SearchResult
    build_full_pipeline(deps) → Pipeline

Wires 5a-5e components:
- SearchSubgraph (5d1) per dataset_id, using VectorRetriever + FulltextRetriever
- PipelineOrchestrator (5d2) with all stage callbacks
- RerankStageAdapter (5d3) if rerank_client provided
- SimpleCite (5d4) for citation list construction
- NoOpParentDoc (5d5) — real ParentDocExpander requires session_factory
  refactor (deferred to a follow-up since the orchestrator-level session
  pool design is not yet finalized)
- LLM-based gen with citation instruction prompt
- AuditTap (5e) writing NDJSON when ``req.audit=True``

Public API:
    PipelineDeps: typed Pydantic deps
    build_full_pipeline(deps) -> Pipeline
    make_llm_gen(llm) -> GenFn: helper to build gen callback from ChatModel
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.infra.pg.fulltext_store import FulltextRetriever
from rag.infra.pg.vector_store import VectorRetriever
from rag.pipeline.cite import SimpleCite
from rag.pipeline.filter import DEFAULT_TOKEN_BUDGET
from rag.pipeline.fusion import DEFAULT_RRF_K
from rag.pipeline.orchestrator import (
    GenFn,
    PipelineOrchestrator,
)
from rag.pipeline.parent_doc import NoOpParentDoc
from rag.pipeline.rerank import NoOpRerankStage, RerankStageAdapter
from rag.pipeline.subgraph import SearchSubgraph
from rag.retrieval.audit import AuditRecord

logger = logging.getLogger(__name__)


# ---------- Protocol contracts ----------


class LLMClientLike(Protocol):
    """Minimal LLM interface — any LangChain BaseChatModel works.

    Not a Pydantic field type (Protocols aren't valid Pydantic types);
    use ``Any`` in PipelineDeps and rely on duck typing.
    """

    async def ainvoke(self, input: object) -> object: ...


# ---------- PipelineDeps ----------


class PipelineDeps(BaseModel):
    """Typed dependency injection for the pipeline (Contract 3).

    All fields are explicit and required-or-defaulted; no dict-bag.
    Frozen Pydantic model → safe to share across threads / coroutines.

    Note: embedder / llm / rerank_client / audit_tap are typed as ``Any``
    because Pydantic v2 can't generate schemas for LangChain classes or
    runtime Protocols. The Protocol classes above document the contract;
    callers must satisfy them via duck typing.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    embedder: Any  # Embeddings (LangChain)
    llm: Any  # LLMClientLike (duck-typed)
    rerank_client: Any | None = None  # Reranker
    audit_tap: Any | None = None  # AuditTap

    # Tunable weights (with sensible defaults)
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    rrf_k: int = DEFAULT_RRF_K
    rerank_weight: float = 0.7
    top_k: int = 10
    token_budget: int = DEFAULT_TOKEN_BUDGET


# ---------- Pipeline ----------


class Pipeline(Protocol):
    """Typed pipeline per Contract 3. ``ainvoke(SearchRequest) -> SearchResult``."""

    async def ainvoke(self, req: SearchRequest) -> SearchResult: ...


# ---------- Default LLM gen callback ----------


CITE_SYSTEM_PROMPT: str = (
    "你是一个知识库问答助手。请严格基于提供的参考资料回答问题。\n"
    "规则:\n"
    "1. 只使用参考资料中的事实, 不要引入外部知识。\n"
    "2. 在引用了具体事实的位置插入 [id](CITE) 标记, id 是参考资料的 1-based 编号。\n"
    "3. 不要捏造未在参考资料中出现的引用 id。\n"
    "4. 如果参考资料不足以回答问题, 请如实说明。\n"
    "5. 回答语言与用户提问语言保持一致。\n"
)


def make_llm_gen(llm: LLMClientLike) -> GenFn:
    """Build a GenFn that calls LLM with citation instruction.

    System prompt instructs the LLM to insert ``[id](CITE)`` markers at
    cited positions. User prompt contains the formatted context
    (``[1] content\n[2] content\n...``) plus the original query.

    Returns a function suitable for ``PipelineOrchestrator(gen=...)``.
    """

    async def gen(
        docs: list,  # list[ScoredDocument] — kept loose to avoid import cycles
        citations: list[Citation],
        req: SearchRequest,
    ) -> str:
        if not docs:
            return "no relevant content found"
        # Format context as [N] content per citation
        context_lines = [
            f"[{i + 1}] {c.content}" for i, c in enumerate(citations)
        ]
        context = "\n\n".join(context_lines) if context_lines else "(no citations)"
        user_prompt = (
            f"参考资料:\n{context}\n\n"
            f"问题: {req.query}\n\n"
            f"回答:"
        )
        try:
            response = await llm.ainvoke(
                [
                    {"role": "system", "content": CITE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
            )
        except Exception as e:
            logger.warning("LLM gen failed for query=%r: %r", req.query, e)
            return f"(LLM generation failed: {e})"
        # LangChain ChatModel 返回 AIMessage; 取 content
        content = getattr(response, "content", None)
        if content is None:
            content = str(response)
        return str(content)

    return gen


# ---------- build_full_pipeline ----------


@dataclass
class _FullPipeline:
    """Internal Pipeline implementation."""

    deps: PipelineDeps

    def _build_orchestrator(self, req: SearchRequest) -> PipelineOrchestrator:
        """Construct per-request orchestrator (subgraphs depend on request)."""
        subgraphs: dict[uuid.UUID, SearchSubgraph] = {}
        for ds_id in req.dataset_ids:
            subgraphs[ds_id] = SearchSubgraph(
                dataset_id=ds_id,
                vector_retriever=VectorRetriever(ds_id, self.deps.embedder),
                fulltext_retriever=FulltextRetriever(ds_id),
                top_k=self.deps.top_k,
            )

        rerank_cb = (
            RerankStageAdapter(
                reranker=self.deps.rerank_client,
                rerank_weight=self.deps.rerank_weight,
            )
            if self.deps.rerank_client is not None
            else NoOpRerankStage()
        )

        gen_cb = make_llm_gen(self.deps.llm)

        return PipelineOrchestrator(
            subgraphs=subgraphs,
            filter_score_threshold=None,
            token_budget=self.deps.token_budget,
            rerank=rerank_cb,
            parent_doc=NoOpParentDoc(),  # 5d5 session_factory refactor pending
            cite=SimpleCite(),
            gen=gen_cb,
            rrf_k=self.deps.rrf_k,
        )

    async def ainvoke(self, req: SearchRequest) -> SearchResult:
        orchestrator = self._build_orchestrator(req)
        result = await orchestrator.ainvoke(req)

        # Audit: only when req.audit=True AND audit_tap configured
        if req.audit and self.deps.audit_tap is not None:
            rec = AuditRecord.from_search_result(req, result)
            await self.deps.audit_tap.record(rec)

        return result


def build_full_pipeline(deps: PipelineDeps) -> Pipeline:
    """Build a typed Pipeline per Contract 3.

    Wires all stages from 5a-5e:
    - Stage 2: SearchSubgraph per dataset (vector + fulltext retriever)
    - Stage 3/6: intra_fusion via PipelineOrchestrator
    - Stage 4-5: RerankStageAdapter if rerank_client set, else NoOpRerankStage
    - Stage 7: filter (via orchestrator, default thresholds)
    - Stage 8: NoOpParentDoc (real ParentDocExpander pending session_factory refactor)
    - Stage 9: SimpleCite (1-based numbering)
    - Stage 10: make_llm_gen (LLM call with citation instruction)
    - Audit: AuditTap.record when req.audit=True

    Args:
        deps: PipelineDeps with embedder, llm, optional rerank_client
            and audit_tap, plus tunable weights.

    Returns:
        Pipeline object with ``ainvoke(SearchRequest) -> SearchResult``.
    """
    return _FullPipeline(deps=deps)