import uuid

from langchain_core.embeddings import Embeddings

from rag.domain.document import ScoredDocument
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.retriever_base import BaseRetriever, to_scored_documents


class VectorRetriever(BaseRetriever):
    """基于 `pgvector` HNSW 索引的向量检索器。

    `source` 字段标识为 ``"vector"``。每次 ``search`` 创建独立 ``AsyncSession``，
    用完即关。
    """

    def __init__(self, dataset_id: uuid.UUID, embed_model: Embeddings) -> None:
        super().__init__(dataset_id)
        self.embed_model = embed_model

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        """执行向量检索: embedding query → pgvector HNSW 余弦检索。

        Args:
            query: 原始查询文本, 会先调 ``embed_model.aembed_query``。
            top_k: 返回前 k 条结果。

        Returns:
            按余弦相似度降序排列的 ``ScoredDocument`` 列表, ``source="vector"``。
        """
        vec = await self.embed_model.aembed_query(query)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_vector(vec, self.dataset_id, top_k)
        return to_scored_documents(rows, source="vector")
