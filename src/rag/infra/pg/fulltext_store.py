import uuid

from langchain_core.runnables import Runnable, RunnableConfig

from rag.domain.document import ScoredDocument
from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.runnable_sync import run_coroutine_sync


class FulltextRetriever(Runnable):
    """jieba 预分词 + PostgreSQL tsvector GIN 全文检索。

    实现 LangChain ``Runnable``，与 ``VectorRetriever`` 对齐，便于 LCEL 编排：

    - **统一 I/O**：``{"query": str, "top_k": int}`` → ``list[ScoredDocument]``，
      其中 ``source="fulltext"``（向量侧为 ``"vector"``）。
    - **LCEL 可组合**：可与 ``|``、``RunnableParallel`` 等拼链路，例如
      向量 + 全文并行检索 → RRF 融合 → LLM；``RunnableConfig`` 支持 tracing。
    - **双 API**：
      - ``search(query, top_k)`` — 项目内直接 ``await`` 的语义化入口；
      - ``ainvoke(input_dict)`` / ``invoke(input_dict)`` — Runnable 协议，供链式编排。

    每次 ``search`` 创建独立 ``AsyncSession``，用完即关。
    """

    def __init__(
        self,
        dataset_id: uuid.UUID,
        tokenizer: ChineseTokenizer | None = None,
    ) -> None:
        self.dataset_id = dataset_id
        self.tokenizer = tokenizer or ChineseTokenizer()

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
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
