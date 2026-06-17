"""检索器基类: Runnable 协议 + ScoredDocument 构造。

``VectorRetriever`` 与 ``FulltextRetriever`` 共享的 ``ainvoke``/``invoke``
协议实现与 ``ScoredDocument`` 字段映射, 提取到此模块避免重复。
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import Runnable, RunnableConfig

from rag.domain.document import ScoredDocument
from rag.infra.pg.runnable_sync import run_coroutine_sync


def to_scored_documents(
    rows: list[tuple[Any, float]],
    *,
    source: str,
) -> list[ScoredDocument]:
    """``(ChunkModel, score)`` 列表 → ``ScoredDocument`` 列表。

    Args:
        rows: ``search_by_vector`` 或 ``search_by_fulltext`` 的返回，
            每项为 ``(ChunkModel, float_score)``。
        source: ``"vector"`` 或 ``"fulltext"``, 填入 ``ScoredDocument.source``。

    Returns:
        按 ``rank 0..N-1`` 编号的 ``ScoredDocument`` 列表。
    """
    return [
        ScoredDocument(
            chunk_id=chunk.id,
            dataset_id=chunk.dataset_id,
            document_id=chunk.document_id,
            text=chunk.text,
            score=score,
            rank=i,
            source=source,  # type: ignore[arg-type]
            modality=chunk.modality,
            image_path=chunk.image_path,
            metadata=chunk.metadata,
            embedding=chunk.embedding,
        )
        for i, (chunk, score) in enumerate(rows)
    ]


class BaseRetriever(Runnable):
    """检索器公共基类, 封装 ``Runnable.ainvoke`` / ``invoke`` 协议。

    Args:
        dataset_id: 数据集 ID, 用于过滤检索范围。
    """

    def __init__(self, dataset_id: uuid.UUID) -> None:
        self.dataset_id = dataset_id

    async def ainvoke(
        self,
        input: dict[str, object],
        config: RunnableConfig | None = None,
        **kwargs: object,
    ) -> list[ScoredDocument]:
        """从 input dict 中提取 ``query`` / ``top_k``, 委派给 ``search``。"""
        query = str(input["query"])
        raw_top_k = input.get("top_k", 10)
        top_k = raw_top_k if isinstance(raw_top_k, int) else 10
        return await self.search(query, top_k)

    def invoke(
        self,
        input: dict[str, object],
        config: RunnableConfig | None = None,
        **kwargs: object,
    ) -> list[ScoredDocument]:
        """同步入口, 通过事件循环桥接调用 ``ainvoke``。"""
        return run_coroutine_sync(lambda: self.ainvoke(input, config, **kwargs))

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        """子类需实现: 对 ``query`` 执行具体检索并返回 ``ScoredDocument`` 列表。"""
        raise NotImplementedError
