import uuid

from langchain_core.runnables import Runnable, RunnableConfig

from rag.domain.document import ScoredDocument
from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.runnable_sync import run_coroutine_sync


class FulltextRetriever(Runnable):
    """基于 jieba 预分词与 PostgreSQL `tsvector` GIN 索引的全文检索器。

    实现 LangChain `Runnable` 协议，I/O 与 `VectorRetriever` 对齐，
    便于 LCEL 链式编排（`source` 字段标识为 `"fulltext"`）。
    """

    def __init__(
        self,
        dataset_id: uuid.UUID,
        tokenizer: ChineseTokenizer | None = None,
    ) -> None:
        """初始化全文检索器。

        Args:
            dataset_id: 数据集 ID，用于过滤检索范围。
            tokenizer: 中文分词器，默认使用 `ChineseTokenizer`。
        """
        self.dataset_id = dataset_id
        self.tokenizer = tokenizer or ChineseTokenizer()

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        """执行全文检索。

        Args:
            query: 原始查询文本。
            top_k: 返回前 k 条结果。

        Returns:
            命中的文档列表，按相关度降序，`source="fulltext"`。
        """
        tokens = self.tokenizer.tokenize(query)
        ts_query = " & ".join(tokens)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_fulltext(ts_query, self.dataset_id, top_k)

        return [
            ScoredDocument(
                chunk_id=chunk.id,
                dataset_id=chunk.dataset_id,
                text=chunk.text,
                score=score,
                rank=i,
                source="fulltext",
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
