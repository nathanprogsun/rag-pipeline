"""Rerank 阶段: 重排 + 重新融合。

- Rerank 仅作用于文本模态, ``image_caption`` 命中跳过 rerank, 在重融
  合时以权重 1.0 合并。
- ``score_breakdown["rerank"]`` 写入原始 rerank 分数, ``rerank_score``
  字段同步填充, 供下游消费。
- ``intra_fusion`` 重融合时对 ``score_breakdown`` 做 per-source max 合并。
- Rerank 失败时记录 warning 并回退到原始顺序, 不中断 pipeline。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.document import ScoredDocument
from rag.domain.search import SearchRequest
from rag.search.retrieve.fusion import intra_fusion

logger = logging.getLogger(__name__)


class RerankStageProtocol(Protocol):
    """Rerank 阶段回调, 接收融合后的 hits, 返回重融合结果。"""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


class _TextReranker(Protocol):
    """纯文本 reranker 契约, 返回 ``(idx, score)`` 对 (按相关性降序)。"""

    async def rerank(
        self, query: str, documents: list[str], top_k: int
    ) -> list[tuple[int, float]]: ...


class NoOpRerankStage:
    """恒等透传, 用于禁用 rerank 的场景 (无 API key 或显式关闭)。"""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        return list(docs)


class RerankStageAdapter:
    """Rerank 阶段适配器: 重排文本 hits + 与原始顺序重融合。

    1. 按 modality 拆分为 text 与 image_caption。
    2. 对 text 部分调用 ``self.reranker`` 重排。
    3. 在重排后的副本上填充 ``score_breakdown["rerank"]`` 与 ``rerank_score``。
    4. ``intra_fusion([reranked, original_text], weights=[rerank_weight, 1-rerank_weight])``。
    5. 用 ``intra_fusion([..., image], weights=[1, 1])`` 合并 image 命中 (跳过 rerank)。

    Args:
        reranker: 纯文本 reranker, 需实现 ``rerank(query, docs, top_k) -> list[(idx, score)]``。
        rerank_weight: 重融时 reranked 列表的权重, 范围 ``[0, 1]``,
            1.0 = 完全信任 rerank, 0.0 = 完全信任原始 RRF。
        on_error: 可选异步错误回调, 接收原始 docs 与异常。

    Raises:
        ValueError: ``rerank_weight`` 越界。
        永不因 reranker 异常抛出: 异常被捕获、记录, 并返回原始顺序。
    """

    DEFAULT_RERANK_WEIGHT: float = 0.7

    def __init__(
        self,
        *,
        reranker: _TextReranker,
        rerank_weight: float = DEFAULT_RERANK_WEIGHT,
        on_error: Callable[[list[ScoredDocument], BaseException], Awaitable[None]]
        | None = None,
    ) -> None:
        if rerank_weight < 0 or rerank_weight > 1:
            msg = f"rerank_weight must be in [0, 1], got {rerank_weight}"
            raise ValueError(msg)
        self.reranker = reranker
        self.rerank_weight = rerank_weight
        self.on_error = on_error

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        if not docs:
            return []

        text_docs = [d for d in docs if d.modality == "text"]
        image_docs = [d for d in docs if d.modality == "image_caption"]

        if not text_docs:
            return list(image_docs)

        try:
            results = await self.reranker.rerank(
                req.query,
                [d.text for d in text_docs],
                top_k=len(text_docs),
            )
        except Exception as e:
            logger.warning(
                "Rerank failed for query=%r, falling back to original order: %r",
                req.query,
                e,
            )
            if self.on_error is not None:
                await self.on_error(list(docs), e)
            return list(docs)

        reranked_text: list[ScoredDocument] = []
        for orig_idx, score in results:
            if orig_idx < 0 or orig_idx >= len(text_docs):
                logger.warning(
                    "Reranker returned out-of-range index %d (text_docs len=%d)",
                    orig_idx,
                    len(text_docs),
                )
                continue
            original = text_docs[orig_idx]
            reranked_text.append(
                original.model_copy(
                    update={
                        "rerank_score": score,
                        "score_breakdown": {
                            **original.score_breakdown,
                            "rerank": score,
                        },
                    }
                )
            )

        if self.rerank_weight >= 1.0:
            text_fused: list[ScoredDocument] = reranked_text
        elif self.rerank_weight <= 0.0:
            text_fused = text_docs
        else:
            text_fused = intra_fusion(
                [reranked_text, text_docs],
                weights=[self.rerank_weight, 1.0 - self.rerank_weight],
            )

        if not image_docs:
            return text_fused
        return intra_fusion(
            [text_fused, image_docs],
            weights=[1.0, 1.0],
        )
