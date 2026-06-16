import uuid

from rag.domain.document import ScoredDocument
from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.retriever_base import BaseRetriever, to_scored_documents


class FulltextRetriever(BaseRetriever):
    """基于 jieba 预分词与 PostgreSQL ``tsvector`` GIN 索引的全文检索器。

    ``source`` 字段标识为 ``"fulltext"``, I/O 与 ``VectorRetriever`` 对齐。
    """

    def __init__(
        self,
        dataset_id: uuid.UUID,
        tokenizer: ChineseTokenizer | None = None,
    ) -> None:
        super().__init__(dataset_id)
        self.tokenizer = tokenizer or ChineseTokenizer()

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        """执行全文检索: jieba 分词 → tsquery → tsvector 匹配。

        Args:
            query: 原始查询文本。
            top_k: 返回前 k 条结果。

        Returns:
            按 ``ts_rank`` 降序排列的 ``ScoredDocument`` 列表, ``source="fulltext"``。
        """
        tokens = self.tokenizer.tokenize(query)
        ts_query = " & ".join(tokens)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_fulltext(ts_query, self.dataset_id, top_k)
        return to_scored_documents(rows, source="fulltext")
