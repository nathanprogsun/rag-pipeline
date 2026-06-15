"""build_search_pipeline per Contract 3.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 3:

Typed I/O contract:
    SearchPipelineDeps (Pydantic frozen) → typed dependency injection
    Pipeline (Protocol) → exposes .ainvoke(SearchRequest) -> SearchResult
    build_search_pipeline(deps) → Pipeline

Wires components across search subpackages:
- SearchSubgraph (per dataset_id) using VectorRetriever + FulltextRetriever
- SearchPipeline with all stage callbacks
- RerankStageAdapter if rerank_client provided
- SimpleCite for citation list construction
- NoOpParentDoc (real ParentDocExpander requires session_factory refactor)
- LLM-based gen via make_llm_gen
- AuditTap writing NDJSON when ``req.audit=True``

Public API:
    SearchPipelineDeps: typed Pydantic deps
    Pipeline: Protocol for the public surface
    build_search_pipeline(deps) -> Pipeline
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from rag.domain.search import SearchRequest, SearchResult
from rag.infra.observability.audit import AuditRecord
from rag.infra.pg.fulltext_store import FulltextRetriever
from rag.infra.pg.vector_store import VectorRetriever
from rag.search.generate.answer import make_llm_gen
from rag.search.orchestrator import SearchPipeline
from rag.search.post.cite import SimpleCite
from rag.search.post.filter import DEFAULT_TOKEN_BUDGET
from rag.search.post.parent_doc import NoOpParentDoc
from rag.search.retrieve.fusion import DEFAULT_RRF_K
from rag.search.retrieve.rerank import NoOpRerankStage, RerankStageAdapter
from rag.search.retrieve.subgraph import SearchSubgraph

logger = logging.getLogger(__name__)


# ---------- Protocol contracts ----------


class Pipeline(Protocol):
    """Typed pipeline per Contract 3. ``ainvoke(SearchRequest) -> SearchResult``."""

    async def ainvoke(self, req: SearchRequest) -> SearchResult: ...


# ---------- SearchPipelineDeps ----------


class SearchPipelineDeps(BaseModel):
    """Typed dependency injection for the search pipeline (Contract 3).

    All fields are explicit and required-or-defaulted; no dict-bag.
    Frozen Pydantic model → safe to share across threads / coroutines.

    Note: embedder / llm / rerank_client / audit_tap are typed as ``Any``
    because Pydantic v2 can't generate schemas for LangChain classes or
    runtime Protocols. The Protocol classes in generate/answer.py
    document the contract; callers must satisfy them via duck typing.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    embedder: Any
    llm: Any
    rerank_client: Any | None = None
    audit_tap: Any | None = None

    # Tunable weights (with sensible defaults)
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    rrf_k: int = DEFAULT_RRF_K
    rerank_weight: float = 0.7
    top_k: int = 10
    token_budget: int = DEFAULT_TOKEN_BUDGET


# ---------- build_search_pipeline ----------


@dataclass
class _SearchPipelineImpl:
    """Internal Pipeline implementation."""

    deps: SearchPipelineDeps

    def _build_search_pipeline(self, req: SearchRequest) -> SearchPipeline:
        """Construct per-request SearchPipeline (subgraphs depend on request)."""
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

        return SearchPipeline(
            subgraphs=subgraphs,
            filter_score_threshold=None,
            token_budget=self.deps.token_budget,
            rerank=rerank_cb,
            parent_doc=NoOpParentDoc(),
            cite=SimpleCite(),
            gen=gen_cb,
            rrf_k=self.deps.rrf_k,
        )

    async def ainvoke(self, req: SearchRequest) -> SearchResult:
        pipeline = self._build_search_pipeline(req)
        result = await pipeline.ainvoke(req)

        if req.audit and self.deps.audit_tap is not None:
            rec = AuditRecord.from_search_result(req, result)
            await self.deps.audit_tap.record(rec)

        return result


def build_search_pipeline(deps: SearchPipelineDeps) -> Pipeline:
    """Build a typed Pipeline per Contract 3.

    Wires all stages:
    - Stage 2: SearchSubgraph per dataset (vector + fulltext retriever)
    - Stage 3/6: intra_fusion via SearchPipeline
    - Stage 4-5: RerankStageAdapter if rerank_client set, else NoOpRerankStage
    - Stage 7: filter (via orchestrator, default thresholds)
    - Stage 8: NoOpParentDoc
    - Stage 9: SimpleCite (1-based numbering)
    - Stage 10: make_llm_gen (LLM call with citation instruction)
    - Audit: AuditTap.record when req.audit=True

    Args:
        deps: SearchPipelineDeps with embedder, llm, optional rerank_client
            and audit_tap, plus tunable weights.

    Returns:
        Pipeline object with ``ainvoke(SearchRequest) -> SearchResult``.
    """
    return _SearchPipelineImpl(deps=deps)
