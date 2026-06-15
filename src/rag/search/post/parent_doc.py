"""ParentDoc 阶段: 将命中 chunk 扩展到父窗口。

对每个命中 chunk, 扩展区间 ``[chunk_index - window_size, chunk_index + window_size]``,
通过 ``ChunkRepository.get_siblings`` 取出兄弟 chunk。命中 chunk 保留原 score;
兄弟 chunk score 按 ``sibling_decay`` (默认 0.5) 衰减。``image_caption`` 模态的命中
跳过扩展 (其父上下文是图像本身)。窗口大小由 ``req.context.parent_doc_window``
(默认 0 = 禁用) 决定。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest
from rag.infra.pg.repositories.chunk_repo import ChunkRepository

logger = logging.getLogger(__name__)


class ParentDocStage(Protocol):
    """parent_doc 阶段回调, 将命中 chunk 扩展到父窗口。"""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


class NoOpParentDoc:
    """恒等透传, 用于 ``parent_doc_window=0`` 的情况。"""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        return list(docs)


class ParentDocExpander:
    """通过 ``ChunkRepository`` 将命中 chunk 扩展到父窗口。

    - 命中 chunk 保留原 score。
    - 兄弟 chunk score 乘以 ``sibling_decay`` (默认 0.5)。
    - ``image_caption`` 模态的命中跳过扩展。
    - 按 ``chunk_id`` 去重, 避免重叠窗口的兄弟重复。

    Args:
        chunk_repo: chunk 仓储, 用于查询兄弟 chunk。
        default_window: 默认窗口大小, ``req.context.parent_doc_window``
            为空时使用。
        sibling_decay: 兄弟 chunk 分数衰减系数, 范围 ``[0, 1]``。
        on_error: 扩展失败时的异步回调, 用于日志或告警。

    Raises:
        ValueError: ``default_window < 0`` 或 ``sibling_decay`` 越界。
    """

    DEFAULT_SIBLING_DECAY: float = 0.5

    def __init__(
        self,
        *,
        chunk_repo: ChunkRepository,
        default_window: int = 0,
        sibling_decay: float = DEFAULT_SIBLING_DECAY,
        on_error: Callable[[list[ScoredDocument], BaseException], Awaitable[None]]
        | None = None,
    ) -> None:
        if default_window < 0:
            msg = f"default_window must be >= 0, got {default_window}"
            raise ValueError(msg)
        if sibling_decay < 0 or sibling_decay > 1:
            msg = f"sibling_decay must be in [0, 1], got {sibling_decay}"
            raise ValueError(msg)
        self.chunk_repo = chunk_repo
        self.default_window = default_window
        self.sibling_decay = sibling_decay
        self.on_error = on_error

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        window = req.context.parent_doc_window or self.default_window
        if window == 0 or not docs:
            return list(docs)

        expanded: list[ScoredDocument] = []
        seen_ids: set = set()
        matched_ids: set = {d.chunk_id for d in docs}

        try:
            for doc in docs:
                if doc.modality == "image_caption":
                    if doc.chunk_id not in seen_ids:
                        seen_ids.add(doc.chunk_id)
                        expanded.append(doc)
                    continue

                parent_title = doc.metadata.parent_title
                if not parent_title:
                    if doc.chunk_id not in seen_ids:
                        seen_ids.add(doc.chunk_id)
                        expanded.append(doc)
                    continue

                lo = max(0, doc.metadata.chunk_index - window)
                hi = doc.metadata.chunk_index + window

                siblings = await self.chunk_repo.get_siblings(
                    dataset_id=doc.dataset_id,
                    parent_title=parent_title,
                    lo=lo,
                    hi=hi,
                )

                for sib in siblings:
                    if sib.id in seen_ids:
                        continue
                    seen_ids.add(sib.id)

                    if sib.id in matched_ids:
                        orig = next((d for d in docs if d.chunk_id == sib.id), None)
                        expanded.append(
                            orig if orig is not None else _to_scored(sib, doc.score)
                        )
                    else:
                        expanded.append(_to_scored(sib, doc.score * self.sibling_decay))
        except Exception as e:
            logger.warning(
                "ParentDoc expansion failed, falling back to original order: %r",
                e,
            )
            if self.on_error is not None:
                await self.on_error(list(docs), e)
            return list(docs)

        return expanded


def _to_scored(chunk: DomainChunk, score: float) -> ScoredDocument:
    """将 ``ChunkRepository`` 的领域 ``Chunk`` 转换为带指定 score 的 ``ScoredDocument``。"""
    return ScoredDocument(
        chunk_id=chunk.id,
        dataset_id=chunk.dataset_id,
        text=chunk.text,
        score=score,
        rank=0,
        source="vector",
        modality=chunk.modality,
        image_path=chunk.image_path,
        metadata=ChunkMetadata(
            dataset_id=chunk.dataset_id,
            datasource=chunk.metadata.datasource,
            filename=chunk.metadata.filename,
            parent_title=chunk.metadata.parent_title,
            chunk_index=chunk.metadata.chunk_index,
        ),
    )
