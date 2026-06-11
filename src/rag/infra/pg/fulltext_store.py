import asyncio
import uuid
from typing import Literal, cast

import jieba
from langchain_core.runnables import Runnable, RunnableConfig

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository

_jieba_loaded = False


def _ensure_jieba() -> None:
    global _jieba_loaded
    if not _jieba_loaded:
        jieba.initialize()
        _jieba_loaded = True


def tokenize_chinese(text: str) -> list[str]:
    """应用层 jieba 分词, 空格 join 用于 tsvector。"""
    _ensure_jieba()
    return [t for t in jieba.cut_for_search(text) if t.strip()]


def build_tsvector(text: str) -> str:
    """把 jieba 分词结果转 tsvector 字面量。"""
    tokens = tokenize_chinese(text)
    return " ".join(tokens)


class FulltextRetriever(Runnable):
    """jieba 预分词 + tsvector GIN 检索。每次 search 创建新 session。"""

    def __init__(self, dataset_id: uuid.UUID) -> None:
        self.dataset_id = dataset_id

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        tokens = tokenize_chinese(query)
        ts_query = " & ".join(tokens)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_fulltext(ts_query, self.dataset_id, top_k)

        return [
            ScoredDocument(
                chunk_id=row.id,
                dataset_id=row.dataset_id,
                text=row.text,
                score=score,
                rank=i,
                source="fulltext",
                modality=cast(Literal["text", "image_caption"], row.modality),
                image_path=row.image_path,
                metadata=ChunkMetadata(
                    dataset_id=row.dataset_id,
                    datasource="file",
                    filename=row.filename,
                    parent_title=row.parent_title,
                    chunk_index=row.chunk_index,
                    created_at=row.created_at,
                ),
            )
            for i, (row, score) in enumerate(rows)
        ]

    async def ainvoke(
        self,
        input: dict[str, object],  # Runnable 松散 dict；运行时含 query(str)、top_k(int)
        config: RunnableConfig | None = None,
        **kwargs: object,  # Runnable.ainvoke 基类要求 **kwargs，本实现未消费
    ) -> list[ScoredDocument]:
        query = str(input["query"])
        raw_top_k = input.get("top_k", 10)
        top_k = raw_top_k if isinstance(raw_top_k, int) else 10
        return await self.search(query, top_k)

    def invoke(
        self,
        input: dict[str, object],  # Runnable 松散 dict；运行时含 query(str)、top_k(int)
        config: RunnableConfig | None = None,
        **kwargs: object,  # Runnable.invoke 基类要求 **kwargs，本实现未消费
    ) -> list[ScoredDocument]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        return asyncio.run(self.ainvoke(input, config))
