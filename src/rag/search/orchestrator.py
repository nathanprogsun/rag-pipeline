"""多 dataset 检索 + 生成编排器。

组合 query 改写、向量/全文召回、融合、重排、过滤、引用与生成等阶段。

公共 API:
    SearchPipeline(embedder=..., llm=..., ...).ainvoke(SearchRequest) -> SearchResult

低层测试可注入 ``subgraphs`` + stage 回调, 跳过 embedder/llm 装配。

填充 ``SearchResult._intermediate_hits`` (Pydantic 排除字段), 供
``AuditTap`` 与 ``EvalRunner`` 使用, 但不出现在 ``model_dump_json()`` 输出中。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from langchain_core.embeddings import Embeddings

from rag.config import settings
from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.infra.observability.audit import AuditRecord, AuditTap
from rag.infra.pg.fulltext_store import FulltextRetriever
from rag.infra.pg.vector_store import VectorRetriever
from rag.search.extension.query_ext import QueryExtensionRunnable
from rag.search.generate.answer import make_llm_gen
from rag.search.post.cite import SimpleCite
from rag.search.post.filter import (
    DEFAULT_TOKEN_BUDGET,
    filter_by_score,
    filter_by_token_budget,
)
from rag.search.retrieve.fusion import DEFAULT_RRF_K, intra_fusion
from rag.search.retrieve.rerank import RerankStageAdapter
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
    """可选 cite 格式化阶段回调。"""

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]: ...


RerankFn = Callable[
    [list[ScoredDocument], SearchRequest], Awaitable[list[ScoredDocument]]
]
ParentDocFn = Callable[
    [list[ScoredDocument], SearchRequest], Awaitable[list[ScoredDocument]]
]
CiteFn = Callable[[list[ScoredDocument], SearchRequest], list[Citation]]
GenFn = Callable[[list[ScoredDocument], list[Citation], SearchRequest], Awaitable[str]]


# ---------- Orchestrator ----------


class SearchPipeline:
    """多 dataset 检索 + 生成编排器 (Contract 8)。

    **生产模式** (``subgraphs`` 未注入):
        必填 ``embedder`` + ``llm``; 每次 ``ainvoke`` 按 ``req.dataset_ids`` 装配
        subgraph, 默认启用 ``SimpleCite`` + ``make_llm_gen``。

    **低层测试模式** (``subgraphs`` 非空):
        不要求 ``embedder`` / ``llm``; 可选 inject ``rerank`` / ``cite`` / ``gen`` /
        ``parent_doc`` / ``query_ext``。未 inject 的 stage 按 skip 语义处理。

    Args:
        embedder: LangChain Embeddings; 生产模式必填。
        llm: LangChain chat 模型; 生产模式必填。
        rerank_client: 可选 rerank API 客户端; ``None`` 时跳过 rerank。
        audit_tap: 可选 audit sink; ``req.audit=True`` 且非空时写入。
        vector_weight: subgraph 内向量路 RRF 权重。
        fulltext_weight: subgraph 内全文路 RRF 权重。
        rrf_k: intra_fusion RRF 常数。
        rerank_weight: rerank 重融合权重。
        token_budget: filter 阶段 token 上限。
        subgraphs: 低层测试用 per-dataset 子图; 非空时进入测试模式。
        query_ext: 覆盖阶段 1 改写组件 (测试)。
        rerank: 覆盖阶段 4 rerank (测试)。
        parent_doc: 覆盖阶段 6 parent_doc (测试)。
        cite: 覆盖阶段 7 cite (测试); 生产默认 ``SimpleCite``。
        gen: 覆盖阶段 8 生成 (测试); 生产默认 ``make_llm_gen(llm)``。

    Raises:
        ValueError: 生产模式缺 embedder/llm, 或测试模式 ``subgraphs`` 为空。
    """

    def __init__(
        self,
        *,
        embedder: Embeddings | None = None,
        llm: object | None = None,
        rerank_client: object | None = None,
        audit_tap: AuditTap | None = None,
        vector_weight: float = 0.7,
        fulltext_weight: float = 0.3,
        rrf_k: int = DEFAULT_RRF_K,
        rerank_weight: float = 0.7,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        subgraphs: dict[uuid.UUID, SearchSubgraph] | None = None,
        query_ext: QueryExtensionRunnable | None = None,
        rerank: RerankFn | None = None,
        parent_doc: ParentDocFn | None = None,
        cite: CiteFn | None = None,
        gen: GenFn | None = None,
    ) -> None:
        if subgraphs is not None:
            if not subgraphs:
                msg = "subgraphs must be a non-empty dict in test mode"
                raise ValueError(msg)
            self._test_mode = True
            self._fixed_subgraphs: dict[uuid.UUID, SearchSubgraph] | None = subgraphs
        else:
            if embedder is None or llm is None:
                msg = "production mode requires embedder and llm"
                raise ValueError(msg)
            self._test_mode = False
            self._fixed_subgraphs = None

        self._embedder = embedder
        self._llm = llm
        self._rerank_client = rerank_client
        self._audit_tap = audit_tap
        self._vector_weight = vector_weight
        self._fulltext_weight = fulltext_weight
        self._rrf_k = rrf_k
        self._rerank_weight = rerank_weight
        self._token_budget = token_budget
        self._query_ext_inject = query_ext
        self._rerank_inject = rerank
        self._parent_doc_inject = parent_doc
        self._cite_inject = cite
        self._gen_inject = gen

    async def ainvoke(self, req: SearchRequest) -> SearchResult:
        """执行完整 RAG 检索 + 生成编排 (Contract 8)。

        阶段顺序:
            0. resolve_subgraphs
            1. extend_query
            2. recall (per-variant × per-dataset, 并行)
            3. fuse_variants
            4. rerank (可选)
            5. filter (dedup → score → doc dedup → token budget)
            6. parent_doc (可选)
            7. cite
            8. generate
            9. build_result
            (+ audit 旁路)

        Args:
            req: 单次搜索请求; ``dataset_ids`` 决定 subgraph 范围,
                ``retrieval.score_threshold`` 控制 filter。

        Returns:
            ``SearchResult``; ``_intermediate_hits`` 为 filter 后、gen 前的 hits。
        """
        warnings: list[str] = []
        subgraphs = self._resolve_subgraphs(req, warnings)

        variants = self._stage_extend_query(req, warnings)
        variant_hits = await self._stage_recall_variants(
            variants, req, subgraphs, warnings
        )
        fused = self._stage_fuse_variants(variant_hits)
        fused = await self._stage_rerank(fused, req, warnings)
        fused = self._stage_filter(fused, req)
        fused = await self._stage_parent_doc(fused, req, warnings)
        citations = self._stage_cite(fused, req)
        response = await self._stage_generate(fused, citations, req, warnings)
        result = self._build_result(
            req, fused, citations, response, warnings, subgraphs
        )
        await self._maybe_record_audit(req, result)
        return result

    # ---- Stage 0: subgraph 解析 ----

    def _resolve_subgraphs(
        self,
        req: SearchRequest,
        warnings: list[str],
    ) -> dict[uuid.UUID, SearchSubgraph]:
        """阶段 0: 解析本次请求可用的 per-dataset 检索子图。

        Args:
            req: 含 ``dataset_ids``。
            warnings: 可变 warning 列表 (当前未追加, 预留)。

        Returns:
            ``dataset_id -> SearchSubgraph``; 测试模式返回构造时注入的固定子图,
            生产模式按 ``req.dataset_ids`` 动态构建。

        Note:
            ``warnings`` 参数保留以便将来记录未注册的 dataset_id。
        """
        _ = warnings
        if self._fixed_subgraphs is not None:
            return self._fixed_subgraphs
        return self._build_subgraphs(req)

    def _build_subgraphs(self, req: SearchRequest) -> dict[uuid.UUID, SearchSubgraph]:
        """生产模式: 为 ``req.dataset_ids`` 装配 vector + fulltext 子图。

        Args:
            req: ``retrieval.top_k`` 作为每路召回 top-k。

        Returns:
            非空 dict (当 ``dataset_ids`` 非空时)。
        """
        assert self._embedder is not None
        top_k = req.retrieval.top_k
        built: dict[uuid.UUID, SearchSubgraph] = {}
        for ds_id in req.dataset_ids:
            built[ds_id] = SearchSubgraph(
                dataset_id=ds_id,
                vector_retriever=VectorRetriever(ds_id, self._embedder),
                fulltext_retriever=FulltextRetriever(ds_id),
                top_k=top_k,
                vector_weight=self._vector_weight,
                fulltext_weight=self._fulltext_weight,
                rrf_k=self._rrf_k,
            )
        return built

    # ---- Stage 1: query extension ----

    def _stage_extend_query(self, req: SearchRequest, warnings: list[str]) -> list[str]:
        """阶段 1: Query Extension — 将用户 query 扩展为 N 个检索 variant。

        Args:
            req: 含 ``query``、``context.query_extension``、``history``。
            warnings: LLM 改写失败时 append ``query_ext_failed:...``。

        Returns:
            非空字符串列表; ``query_extension=False`` 时返回 ``[req.query]``。

        Fail-open:
            改写异常不抛出, 回退 ``[req.query]`` 并记录 warning。
        """
        if not req.context.query_extension:
            return [req.query]

        query_ext = self._resolve_query_ext(req)
        if query_ext is None:
            return [req.query]

        try:
            ext = query_ext(
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

    def _resolve_query_ext(self, req: SearchRequest) -> QueryExtensionRunnable | None:
        """解析阶段 1 组件: 测试 inject 优先, 生产用 ``llm`` 构建。"""
        if self._query_ext_inject is not None:
            return self._query_ext_inject
        if self._test_mode or self._llm is None:
            return None
        return QueryExtensionRunnable(
            model=settings.openai_model,
            k=req.context.max_query_variants,
            llm=self._llm,  # type: ignore[arg-type]  # LangChain chat model
        )

    # ---- Stage 2–3: recall + fuse ----

    async def _stage_recall_variants(
        self,
        variants: list[str],
        req: SearchRequest,
        subgraphs: dict[uuid.UUID, SearchSubgraph],
        warnings: list[str],
    ) -> list[list[ScoredDocument]]:
        """阶段 2: 对每个 query variant 并行召回 (variant 间 ``asyncio.gather``)。

        Args:
            variants: 阶段 1 输出的检索词列表。
            req: 含 ``dataset_ids``。
            subgraphs: 阶段 0 解析的子图。
            warnings: 透传给 ``_recall_one_variant`` / ``_safe_subgraph``。

        Returns:
            ``variant_hits[i]`` 为第 i 个 variant 融合后的 per-dataset hits。
        """
        if not variants:
            return []
        return list(
            await asyncio.gather(
                *(
                    self._recall_one_variant(v, req, subgraphs, warnings)
                    for v in variants
                )
            )
        )

    async def _recall_one_variant(
        self,
        variant: str,
        req: SearchRequest,
        subgraphs: dict[uuid.UUID, SearchSubgraph],
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """阶段 2b: 单 variant 下多 dataset 并行检索 + per-dataset RRF 融合。

        Args:
            variant: 检索 query 文本。
            req: 含 ``dataset_ids``。
            subgraphs: 可用子图映射。
            warnings: 单 dataset 失败时 append warning。

        Returns:
            该 variant 下跨 dataset 融合后的 ``ScoredDocument`` 列表。
        """
        per_dataset_results = await asyncio.gather(
            *(
                self._safe_subgraph(ds_id, variant, subgraphs, warnings)
                for ds_id in req.dataset_ids
                if ds_id in subgraphs
            )
        )
        return (
            intra_fusion(per_dataset_results, rrf_k=self._rrf_k)
            if per_dataset_results
            else []
        )

    async def _safe_subgraph(
        self,
        ds_id: uuid.UUID,
        query: str,
        subgraphs: dict[uuid.UUID, SearchSubgraph],
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """阶段 2c: 调用单 dataset subgraph, 失败时 fail-open 返回空列表。

        Args:
            ds_id: 目标 dataset UUID。
            query: 检索 query。
            subgraphs: 子图映射; ``ds_id`` 不在其中时返回 ``[]``。
            warnings: 失败时 append ``subgraph_failed:...``。

        Returns:
            该 dataset 的召回 hits, 或 ``[]``。
        """
        sg = subgraphs.get(ds_id)
        if sg is None:
            return []
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

    def _stage_fuse_variants(
        self, variant_hits: list[list[ScoredDocument]]
    ) -> list[ScoredDocument]:
        """阶段 3: 跨 query variant RRF 融合。

        Args:
            variant_hits: 阶段 2 每个 variant 的 hits 列表。

        Returns:
            按 RRF score 降序的融合 hits; 空输入 → ``[]``。
        """
        if not variant_hits:
            return []
        return intra_fusion(variant_hits, rrf_k=self._rrf_k)

    # ---- Stage 4: rerank ----

    async def _stage_rerank(
        self,
        docs: list[ScoredDocument],
        req: SearchRequest,
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """阶段 4: Rerank + re-fuse (可选)。

        Args:
            docs: 阶段 3 融合 hits。
            req: ``retrieval.use_rerank`` 为 False 时跳过。
            warnings: rerank 失败时由 adapter 记录 (不追加到 result.warnings)。

        Returns:
            重排后的 hits; skip 时原样返回。
        """
        _ = warnings
        rerank_fn = self._resolve_rerank(req)
        if rerank_fn is None or not docs:
            return list(docs)
        return list(await rerank_fn(docs, req))

    def _resolve_rerank(self, req: SearchRequest) -> RerankFn | None:
        """解析阶段 4: 测试 inject 优先; 生产需 client + ``use_rerank``。"""
        if self._rerank_inject is not None:
            return self._rerank_inject
        if self._test_mode:
            return None
        if not req.retrieval.use_rerank or self._rerank_client is None:
            return None
        return RerankStageAdapter(
            reranker=self._rerank_client,  # type: ignore[arg-type]  # rerank API client
            rerank_weight=self._rerank_weight,
        )

    # ---- Stage 5: filter ----

    def _stage_filter(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        """阶段 5: 过滤链 — dedup → score → doc dedup → token budget。

        Args:
            docs: 阶段 4 输出。
            req: ``retrieval.score_threshold``; ``None`` 跳过 score 过滤。

        Returns:
            过滤后的 hits; 空输入 → ``[]``。
        """
        if not docs:
            return []
        docs = self._filter_dedup_chunks(docs)
        docs = self._filter_by_score(docs, req)
        docs = self._filter_dedup_documents(docs)
        return self._filter_by_token_budget(docs)

    def _filter_dedup_chunks(self, docs: list[ScoredDocument]) -> list[ScoredDocument]:
        """阶段 5a: 按 ``chunk_id`` 稳定去重。"""
        return _dedup_by_chunk_id(docs)

    def _filter_by_score(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        """阶段 5b: ``max(vector, fulltext)`` 阈值过滤 (读 ``score_breakdown``)。

        Args:
            docs: 待过滤 hits。
            req: ``retrieval.score_threshold``; ``None`` → no-op。

        Returns:
            保留的 hits。
        """
        threshold = req.retrieval.score_threshold
        if threshold is None or not docs:
            return list(docs)
        kept, _ = filter_by_score(
            docs,
            threshold=threshold,
            search_mode="mixed",
        )
        return kept

    def _filter_dedup_documents(
        self, docs: list[ScoredDocument]
    ) -> list[ScoredDocument]:
        """阶段 5c: 同一 ``document_id`` 只保留得分最高的一条。"""
        return _dedup_by_document_id(docs)

    def _filter_by_token_budget(
        self, docs: list[ScoredDocument]
    ) -> list[ScoredDocument]:
        """阶段 5d: 贪心 token 预算截断。"""
        if not docs:
            return []
        return filter_by_token_budget(docs, max_tokens=self._token_budget)

    # ---- Stage 6: parent_doc ----

    async def _stage_parent_doc(
        self,
        docs: list[ScoredDocument],
        req: SearchRequest,
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """阶段 6: Parent document 窗口扩展 (可选)。

        Args:
            docs: 阶段 5 输出。
            req: ``context.parent_doc_window``; 0 且无 inject 时 skip。
            warnings: 扩展失败时由 expander 回调 (预留)。

        Returns:
            扩展后的 hits; skip 时原样返回。
        """
        _ = warnings
        if not docs:
            return []
        if self._parent_doc_inject is not None:
            return list(await self._parent_doc_inject(docs, req))
        if req.context.parent_doc_window <= 0:
            return list(docs)
        return list(docs)

    # ---- Stage 7–8: cite + generate ----

    def _stage_cite(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]:
        """阶段 7: 构造 ``Citation`` DTO 列表 (1-based 编号)。

        Args:
            docs: 最终 hits。
            req: 透传 (cite 实现可读 ``SearchRequest``)。

        Returns:
            citations; 无 cite 组件或空 docs → ``[]``。
        """
        _ = req
        cite_fn = self._resolve_cite()
        if cite_fn is None or not docs:
            return []
        return list(cite_fn(docs, req))

    def _resolve_cite(self) -> CiteFn | None:
        """解析阶段 7: 测试 inject 或生产 ``SimpleCite``。"""
        if self._cite_inject is not None:
            return self._cite_inject
        if self._test_mode:
            return None
        return SimpleCite()

    async def _stage_generate(
        self,
        docs: list[ScoredDocument],
        citations: list[Citation],
        req: SearchRequest,
        warnings: list[str],
    ) -> str:
        """阶段 8: LLM 生成含 ``[id](CITE)`` 的回答。

        Args:
            docs: 上下文 hits。
            citations: 阶段 7 输出。
            req: 用户 query 等。
            warnings: gen 失败时由 ``make_llm_gen`` 内部处理 (不追加)。

        Returns:
            LLM 回答字符串; 无 gen 组件 → ``""``; 空 docs → 拒答文案。
        """
        _ = warnings
        gen_fn = self._resolve_gen()
        if gen_fn is None:
            return ""
        return await gen_fn(docs, citations, req)

    def _resolve_gen(self) -> GenFn | None:
        """解析阶段 8: 测试 inject 或生产 ``make_llm_gen``。"""
        if self._gen_inject is not None:
            return self._gen_inject
        if self._test_mode or self._llm is None:
            return None
        return make_llm_gen(self._llm)  # type: ignore[arg-type]  # LangChain chat model

    # ---- Stage 9 + audit ----

    def _build_result(
        self,
        req: SearchRequest,
        fused: list[ScoredDocument],
        citations: list[Citation],
        response: str,
        warnings: list[str],
        subgraphs: dict[uuid.UUID, SearchSubgraph],
    ) -> SearchResult:
        """阶段 9: 组装 ``SearchResult`` 并填充 ``_intermediate_hits``。

        Args:
            req: 原始请求。
            fused: filter 后 hits。
            citations: 阶段 7 输出。
            response: 阶段 8 输出。
            warnings: 累积的内部 warnings。
            subgraphs: 用于计算 ``failed_dataset_ids``。

        Returns:
            完整 ``SearchResult``。
        """
        failed_dataset_ids = [d for d in req.dataset_ids if d not in subgraphs]
        result = SearchResult(
            response=response,
            citations=citations,
            failed_dataset_ids=failed_dataset_ids,
            warnings=warnings,
        )
        result._intermediate_hits = list(fused)
        return result

    async def _maybe_record_audit(
        self, req: SearchRequest, result: SearchResult
    ) -> None:
        """旁路: ``req.audit=True`` 且 ``audit_tap`` 已配置时写入 NDJSON。

        Args:
            req: ``audit`` 标志位。
            result: 完整搜索结果。

        Note:
            audit 失败不抛出, 不修改 ``result``。
        """
        if not req.audit or self._audit_tap is None:
            return
        rec = AuditRecord.from_search_result(req, result)
        await self._audit_tap.record(rec)


# ---------- 纯辅助函数 ----------


def _doc_rank_key(doc: ScoredDocument) -> tuple[float, float, float]:
    """document 去重时的排序键: rerank → vector → RRF。"""
    bd = doc.score_breakdown
    rerank = (
        doc.rerank_score if doc.rerank_score is not None else bd.get("rerank", -1.0)
    )
    return (rerank, bd.get("vector", -1.0), doc.score)


def _dedup_by_document_id(docs: list[ScoredDocument]) -> list[ScoredDocument]:
    """同一 document_id 只保留得分最高的一条; 无 document_id 的 doc 原样保留。"""
    if not docs:
        return []

    best: dict[uuid.UUID, ScoredDocument] = {}
    for d in docs:
        if d.document_id is None:
            continue
        existing = best.get(d.document_id)
        if existing is None or _doc_rank_key(d) > _doc_rank_key(existing):
            best[d.document_id] = d

    seen_doc: set[uuid.UUID] = set()
    out: list[ScoredDocument] = []
    for d in docs:
        if d.document_id is None:
            out.append(d)
            continue
        if d.document_id in seen_doc:
            continue
        winner = best[d.document_id]
        if winner.chunk_id != d.chunk_id:
            continue
        out.append(d)
        seen_doc.add(d.document_id)
    return out


def _dedup_by_chunk_id(docs: list[ScoredDocument]) -> list[ScoredDocument]:
    """按 chunk_id 稳定去重, 保留首次出现顺序。"""
    seen: set[uuid.UUID] = set()
    out: list[ScoredDocument] = []
    for d in docs:
        if d.chunk_id in seen:
            continue
        seen.add(d.chunk_id)
        out.append(d)
    return out
