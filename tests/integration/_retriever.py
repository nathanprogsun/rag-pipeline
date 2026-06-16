"""集成测试共享 helper: RepoRetriever + make_subgraph。

5 个 ``test_*_live.py`` 各自复制了一份 ``_RepoRetriever`` 类与
``_make_subgraph`` helper, 集中到本模块后, 各 test 文件改
``from tests.integration._retriever import RepoRetriever, make_subgraph``。

设计点:
- 显式继承 ``Runnable`` 而非鸭子类型, mypy 友好
- ``session_factory`` 由调用方注入, 不依赖模块级 ``AsyncSessionLocal``
  (后者在跨 event loop 时会报错)
- 同步 ``invoke`` 走 ``run_coroutine_sync`` 桥接, 与 ``VectorRetriever``
  行为对齐
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings
from langchain_core.runnables import Runnable
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.runnable_sync import run_coroutine_sync
from rag.search.retrieve.subgraph import SearchSubgraph

if TYPE_CHECKING:
    from rag.config import Settings  # noqa: F401  (无运行时依赖)


class RepoRetriever(Runnable):
    """``ChunkRepository`` 的 Runnable 包装, 用于集成测试中桥接真实 PG。

    Args:
        session_factory: 外部注入的 ``async_sessionmaker``, 避免跨 event loop
            复用 module-level ``AsyncSessionLocal``。
        dataset_id: 目标 dataset UUID。
        mode: ``"vector"`` (走 embedding 余弦) 或 ``"fulltext"`` (走 tsvector)。
        embed_model: 文本向量化模型; ``mode="vector"`` 时必填。

    Returns:
        ``ainvoke(input)`` -> ``list[ScoredDocument]``, 字段对齐
        ``rag.infra.pg.vector_store.VectorRetriever``。
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        dataset_id: uuid.UUID,
        mode: str,
        embed_model: Embeddings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.dataset_id = dataset_id
        self.mode = mode
        self.embed_model = embed_model

    async def ainvoke(
        self,
        input: dict[str, object],
        config: object = None,
        **kwargs: object,
    ) -> list[ScoredDocument]:
        query = str(input["query"])
        raw_top_k = input.get("top_k", 10)
        top_k = raw_top_k if isinstance(raw_top_k, int) else 10

        async with self.session_factory() as session:
            repo = ChunkRepository(session)
            if self.mode == "vector":
                assert self.embed_model is not None
                query_emb = await self.embed_model.aembed_query(query)
                rows = await repo.search_by_vector(query_emb, self.dataset_id, top_k)
            elif self.mode == "fulltext":
                rows = await repo.search_by_fulltext(query, self.dataset_id, top_k)
            else:
                msg = f"unknown mode: {self.mode}"
                raise ValueError(msg)

            return [
                ScoredDocument(
                    chunk_id=chunk.id,
                    dataset_id=chunk.dataset_id,
                    text=chunk.text,
                    score=score,
                    rank=i,
                    source=self.mode,  # type: ignore[arg-type]
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
                for i, (chunk, score) in enumerate(rows)
            ]

    def invoke(
        self,
        input: dict[str, object],
        config: object = None,
        **kwargs: object,
    ) -> list[ScoredDocument]:
        """同步入口, 走 ``run_coroutine_sync`` 桥接 (与 VectorRetriever 对齐)。"""
        return run_coroutine_sync(lambda: self.ainvoke(input, config, **kwargs))


def make_subgraph(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dataset_id: uuid.UUID,
    embed_model: Embeddings,
    top_k: int = 10,
) -> SearchSubgraph:
    """构造一个连真实 PG 的 ``SearchSubgraph`` (vector + fulltext 双路)。

    Args:
        session_factory: 共享 sessionmaker, 避免与 module-level pool 冲突。
        dataset_id: 目标 dataset UUID。
        embed_model: 文本 embedding 模型 (vector 路用)。
        top_k: 每路召回数量, 默认 10。

    Returns:
        ``SearchSubgraph``, 可直接喂给 ``SearchPipeline``。
    """
    return SearchSubgraph(
        dataset_id=dataset_id,
        vector_retriever=RepoRetriever(
            session_factory=session_factory,
            dataset_id=dataset_id,
            mode="vector",
            embed_model=embed_model,
        ),
        fulltext_retriever=RepoRetriever(
            session_factory=session_factory,
            dataset_id=dataset_id,
            mode="fulltext",
        ),
        top_k=top_k,
    )
