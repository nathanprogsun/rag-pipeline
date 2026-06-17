"""``build_search_pipeline`` 工厂函数与类型化依赖注入。

公共 API:
    SearchPipelineDeps: 类型化 Pydantic 依赖
    Pipeline: 公共接口协议
    build_search_pipeline(deps) -> Pipeline
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from rag.config import settings
from rag.domain.search import SearchRequest, SearchResult
from rag.infra.observability.audit import AuditRecord
from rag.infra.pg.fulltext_store import FulltextRetriever
from rag.infra.pg.vector_store import VectorRetriever
from rag.search.extension.query_ext import QueryExtensionRunnable
from rag.search.generate.answer import make_llm_gen
from rag.search.orchestrator import SearchPipeline
from rag.search.post.cite import SimpleCite
from rag.search.post.filter import DEFAULT_TOKEN_BUDGET
from rag.search.post.parent_doc import NoOpParentDoc
from rag.search.retrieve.fusion import DEFAULT_RRF_K
from rag.search.retrieve.rerank import NoOpRerankStage, RerankStageAdapter
from rag.search.retrieve.subgraph import SearchSubgraph

logger = logging.getLogger(__name__)


# ---------- Protocol 契约 ----------


class Pipeline(Protocol):
    """类型化 pipeline: ``ainvoke(SearchRequest) -> SearchResult``。"""

    async def ainvoke(self, req: SearchRequest) -> SearchResult: ...


# ---------- SearchPipelineDeps ----------


class SearchPipelineDeps(BaseModel):
    """类型化依赖注入 (frozen Pydantic)。

    所有字段显式声明, 无 dict-bag, 跨线程 / 协程安全。
    ``embedder`` / ``llm`` / ``rerank_client`` / ``audit_tap`` 标注为 ``Any``,
    因为 Pydantic v2 无法为 LangChain 类或 runtime Protocol 生成 schema;
    实际契约由各 stage 的 Protocol 类定义, 调用方以 duck typing 满足。
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    embedder: Any
    llm: Any
    rerank_client: Any | None = None
    audit_tap: Any | None = None

    # 可调权重 (带合理默认值)
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    rrf_k: int = DEFAULT_RRF_K
    rerank_weight: float = 0.7
    top_k: int = 10
    token_budget: int = DEFAULT_TOKEN_BUDGET


# ---------- build_search_pipeline ----------


@dataclass
class _SearchPipelineImpl:
    """内部 Pipeline 实现。"""

    deps: SearchPipelineDeps

    def _build_search_pipeline(self, req: SearchRequest) -> SearchPipeline:
        """按请求构造 SearchPipeline (subgraph 依赖请求中的 dataset_ids)。"""
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

        query_ext = QueryExtensionRunnable(
            model=settings.openai_model,
            k=req.context.max_query_variants,
        )

        return SearchPipeline(
            subgraphs=subgraphs,
            query_ext=query_ext,
            filter_score_threshold=req.retrieval.score_threshold,
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
    """装配类型化 Pipeline。

    组装各 stage: SearchSubgraph (per dataset), RerankStageAdapter
    (或 NoOp), filter, NoOpParentDoc, SimpleCite, make_llm_gen,
    以及 ``req.audit=True`` 时的 AuditTap 写入。

    Args:
        deps: 类型化依赖, 含 embedder / llm / 可选 rerank_client
            与 audit_tap 及可调权重。

    Returns:
        Pipeline 对象, 提供 ``ainvoke(SearchRequest) -> SearchResult``。
    """
    return _SearchPipelineImpl(deps=deps)
