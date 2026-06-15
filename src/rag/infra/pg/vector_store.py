import uuid

from langchain_core.embeddings import Embeddings
from langchain_core.runnables import Runnable, RunnableConfig

from rag.domain.document import ScoredDocument
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.runnable_sync import run_coroutine_sync


class VectorRetriever(Runnable):
    """基于 `pgvector` HNSW 索引的向量检索器。

    `Runnable` 契约与 `FulltextRetriever` 一致，`source` 字段标识为 `"vector"`。
    每次 `search` 创建独立 `AsyncSession`，用完即关。
    """

    def __init__(self, dataset_id: uuid.UUID, embed_model: Embeddings) -> None:
        """初始化向量检索器。

        Args:
            dataset_id: 数据集 ID，用于过滤检索范围。
            embed_model: LangChain `Embeddings` 兼容的向量模型。
        """
        self.dataset_id = dataset_id
        self.embed_model = embed_model

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        """执行向量检索。

        Args:
            query: 原始查询文本，会先调用 `embed_model` 嵌入。
            top_k: 返回前 k 条结果。

        Returns:
            命中的文档列表，按余弦相似度降序，`source="vector"`。
        """
        vec = await self.embed_model.aembed_query(query)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_vector(vec, self.dataset_id, top_k)
        return [
            ScoredDocument(
                chunk_id=chunk.id,
                dataset_id=chunk.dataset_id,
                text=chunk.text,
                score=score,
                rank=i,
                source="vector",
                modality=chunk.modality,
                image_path=chunk.image_path,
                metadata=chunk.metadata,
                embedding=chunk.embedding,
            )
            for i, (chunk, score) in enumerate(rows)
        ]

    async def ainvoke(
        self,
        input: dict[str, object],  # `Runnable` 协议入参；含 `query`(str)、`top_k`(int)
        config: RunnableConfig | None = None,
        **kwargs: object,  # `Runnable.ainvoke` 基类要求 `**kwargs`，本实现未消费
    ) -> list[ScoredDocument]:
        """`Runnable` 异步入口：从 dict 中提取 `query` / `top_k` 并委派给 `search`."""
        query = str(input["query"])
        raw_top_k = input.get("top_k", 10)
        top_k = raw_top_k if isinstance(raw_top_k, int) else 10
        return await self.search(query, top_k)

    def invoke(
        self,
        input: dict[str, object],  # `Runnable` 协议入参；含 `query`(str)、`top_k`(int)
        config: RunnableConfig | None = None,
        **kwargs: object,  # `Runnable.invoke` 基类要求 `**kwargs`，本实现未消费
    ) -> list[ScoredDocument]:
        """`Runnable` 同步入口：通过事件循环桥接调用 `ainvoke`."""
        return run_coroutine_sync(lambda: self.ainvoke(input, config, **kwargs))
