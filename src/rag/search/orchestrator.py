"""多 dataset 检索 + 生成编排器。

组合 query 改写、向量/全文召回、融合、重排、过滤、引用与生成等阶段。

公共 API:
    SearchPipeline: ainvoke(SearchRequest) -> SearchResult

填充 ``SearchResult._intermediate_hits`` (Pydantic 排除字段), 供
``AuditTap`` 与 ``EvalRunner`` 使用, 但不出现在 ``model_dump_json()`` 输出中。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.search.extension.query_ext import QueryExtensionRunnable
from rag.search.post.filter import (
    DEFAULT_TOKEN_BUDGET,
    filter_by_score,
    filter_by_token_budget,
)
from rag.search.retrieve.fusion import DEFAULT_RRF_K, intra_fusion
from rag.search.retrieve.subgraph import SearchSubgraph

logger = logging.getLogger(__name__)


# ---------- 可选 stage 回调 ----------


class RerankStage(Protocol):
    """可选 rerank + re-fuse 阶段回调。"""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


class ParentDocStage(Protocol):
    """可选 parent_doc 窗口扩展阶段回调。"""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


class CiteStage(Protocol):
    """可选 cite 格式化阶段回调。

    接收最终 hits 与 SearchRequest, 返回 1-based 编号的 citations 列表
    (与 ``response`` 中的 ``[id](CITE)`` 标记对应)。
    """

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]: ...


# GenStage 在 search.generate.answer 中定义 (与 LLM gen 实现共同维护)。


# 函数式别名 (callable 也可接受, 便于测试)
RerankFn = Callable[
    [list[ScoredDocument], SearchRequest], Awaitable[list[ScoredDocument]]
]
ParentDocFn = Callable[
    [list[ScoredDocument], SearchRequest], Awaitable[list[ScoredDocument]]
]
CiteFn = Callable[[list[ScoredDocument], SearchRequest], list[Citation]]


# ---------- Orchestrator ----------


class SearchPipeline:
    """多 dataset 检索 + 生成编排器。

    Args:
        subgraphs: ``dataset_id -> SearchSubgraph`` 映射, 每个 subgraph
            拥有各自的 vector + fulltext retriever。
        query_ext: 可选 query 改写组件, None = identity (仅使用原 query)。
        filter_score_threshold: 可选 per-source raw score 阈值, 传入
            ``filter_by_score``; 读取 ``score_breakdown`` 而非 RRF ``.score``。
        token_budget: 最终 hits 的最大 token 数。
        rerank: 可选 rerank 回调, None = 不做 rerank。
        parent_doc: 可选 parent_doc 扩展回调, None = 不做扩展。
        cite: 可选 cite 回调, None = 空 citations。
        gen: 可选生成回调, None = 空 response。
        rrf_k: intra_fusion 的 RRF k 常数。

    Raises:
        ValueError: ``subgraphs`` 为空时。
    """

    def __init__(
        self,
        *,
        subgraphs: dict[uuid.UUID, SearchSubgraph],
        query_ext: QueryExtensionRunnable | None = None,
        filter_score_threshold: float | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        rerank: RerankFn | None = None,
        parent_doc: ParentDocFn | None = None,
        cite: CiteFn | None = None,
        gen: Callable | None = None,  # GenFn, loose type to avoid cycle
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        if not subgraphs:
            msg = "subgraphs must be a non-empty dict"
            raise ValueError(msg)
        self.subgraphs = subgraphs
        self.query_ext = query_ext
        self.filter_score_threshold = filter_score_threshold
        self.token_budget = token_budget
        self.rerank = rerank
        self.parent_doc = parent_doc
        self.cite = cite
        self.gen = gen
        self.rrf_k = rrf_k

    async def ainvoke(self, req: SearchRequest) -> SearchResult:
        """执行完整编排, 返回填充好 ``_intermediate_hits`` 的 ``SearchResult``。"""
        internal_warnings: list[str] = []

        # 阶段 1: query 改写 (None → identity, 单 variant 即原 query)
        variants = self._extend_query(req, internal_warnings)

        # 阶段 2-3: per-variant per-dataset 检索 + 跨 variant 融合
        variant_hits: list[list[ScoredDocument]] = await asyncio.gather(
            *(self._recall_one_variant(v, req, internal_warnings) for v in variants)
        )
        fused: list[ScoredDocument] = (
            intra_fusion(variant_hits, rrf_k=self.rrf_k) if variant_hits else []
        )

        # 阶段 4-5: rerank + re-fuse (可选)
        if self.rerank is not None:
            fused = await self.rerank(fused, req)

        # 阶段 7: 过滤 (chunk_id 去重 → 阈值 → token 预算)
        fused = _dedup_by_chunk_id(fused)
        if self.filter_score_threshold is not None and fused:
            fused, _ = filter_by_score(
                fused,
                threshold=self.filter_score_threshold,
                search_mode="mixed",
            )
        if fused:
            fused = filter_by_token_budget(fused, max_tokens=self.token_budget)

        # 阶段 8: parent_doc 扩展 (可选)
        if self.parent_doc is not None and fused:
            fused = await self.parent_doc(fused, req)

        # 阶段 9: cite (可选)
        citations: list[Citation] = (
            list(self.cite(fused, req)) if self.cite is not None else []
        )

        # 阶段 10: 生成 (可选)
        response: str = (
            await self.gen(fused, citations, req) if self.gen is not None else ""
        )

        failed_dataset_ids = [d for d in req.dataset_ids if d not in self.subgraphs]

        result = SearchResult(
            response=response,
            citations=citations,
            failed_dataset_ids=failed_dataset_ids,
            warnings=internal_warnings,
        )
        result._intermediate_hits = list(fused)
        return result

    # ---- Stage 辅助方法 ----

    def _extend_query(self, req: SearchRequest, warnings: list[str]) -> list[str]:
        """阶段 1: 产出 query variants。None → [req.query] identity。"""
        if self.query_ext is None:
            return [req.query]
        try:
            ext = self.query_ext(
                req.query,
                chat_bg=req.history.chat_bg,
                histories=[h.get("content", "") for h in req.history.histories],
            )
        except Exception as e:
            warnings.append(f"query_ext_failed: {e!r}")
            logger.warning(
                "query_ext failed for query=%r, falling back to original: %r",
                req.query,
                e,
            )
            return [req.query]
        return ext.deduped_variants if ext.deduped_variants else [req.query]

    async def _recall_one_variant(
        self,
        variant: str,
        req: SearchRequest,
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """阶段 2 (per-variant): 并行执行各 dataset subgraph 检索。"""
        per_dataset_results = await asyncio.gather(
            *(
                self._safe_subgraph(ds_id, variant, warnings)
                for ds_id in req.dataset_ids
                if ds_id in self.subgraphs
            )
        )
        return (
            intra_fusion(per_dataset_results, rrf_k=self.rrf_k)
            if per_dataset_results
            else []
        )

    async def _safe_subgraph(
        self,
        ds_id: uuid.UUID,
        query: str,
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """调用 subgraph 并做安全错误处理, 单个 dataset 失败不中断整体 pipeline。"""
        sg = self.subgraphs[ds_id]
        try:
            return list(await sg.ainvoke(query))
        except Exception as e:
            warnings.append(f"subgraph_failed:{ds_id}: {e!r}")
            logger.warning(
                "Subgraph for dataset %s failed on query=%r: %r",
                ds_id,
                query,
                e,
            )
            return []


# ---------- 纯辅助函数 ----------


def _dedup_by_chunk_id(docs: list[ScoredDocument]) -> list[ScoredDocument]:
    """按 chunk_id 稳定去重, 保留首次出现顺序。

    不用 ``rag.infra.observability.trace.remove_duplicates``: 它需要
    ``RetrievalTrace`` 的并行 (q, a) 数组, 而编排器在融合后并不持有
    该结构。此处只需丢弃跨 variant + dataset RRF 合并后的意外重复。
    """
    seen: set[uuid.UUID] = set()
    out: list[ScoredDocument] = []
    for d in docs:
        if d.chunk_id in seen:
            continue
        seen.add(d.chunk_id)
        out.append(d)
    return out
