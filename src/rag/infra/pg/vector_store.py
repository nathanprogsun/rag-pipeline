import uuid
from typing import Literal, cast

from langchain_core.embeddings import Embeddings
from langchain_core.runnables import Runnable, RunnableConfig

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.runnable_sync import run_coroutine_sync


class VectorRetriever(Runnable):
    """pgvector HNSW 检索；Runnable 契约与 ``FulltextRetriever`` 相同（见该类 docstring）。

    每次 search 创建新 session, 完成后自动回收。
    """

    def __init__(self, dataset_id: uuid.UUID, embed_model: Embeddings) -> None:
        self.dataset_id = dataset_id
        self.embed_model = embed_model

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        vec = await self.embed_model.aembed_query(query)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_vector(vec, self.dataset_id, top_k)
        return [
            ScoredDocument(
                chunk_id=row.id,
                dataset_id=row.dataset_id,
                text=row.text,
                score=score,
                rank=i,
                source="vector",
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
        return run_coroutine_sync(lambda: self.ainvoke(input, config, **kwargs))
