"""per-dataset 检索子图: 验证 → 并行向量/全文召回 → intra-fusion 融合。

复用 ``rag.infra.pg`` 中的 ``VectorRetriever`` / ``FulltextRetriever``
(均为 LangChain ``Runnable``), 不引入新 DB 代码。
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from langchain_core.runnables import Runnable

from rag.domain.document import ScoredDocument
from rag.search.retrieve.fusion import intra_fusion

logger = logging.getLogger(__name__)


class SearchRequestValidationError(ValueError):
    """Raised when the per-dataset subgraph request is invalid."""


def validate_subgraph_request(
    *,
    query: str,
    dataset_id: uuid.UUID,
    top_k: int,
) -> None:
    """校验 per-dataset subgraph 请求, 无效时抛出 ``SearchRequestValidationError``。

    每个 subgraph 在启动检索前自行校验输入。
    """
    if not isinstance(query, str) or not query.strip():
        msg = f"query must be a non-empty string, got {type(query).__name__}"
        raise SearchRequestValidationError(msg)
    if not isinstance(dataset_id, uuid.UUID):
        msg = f"dataset_id must be UUID, got {type(dataset_id).__name__}"
        raise SearchRequestValidationError(msg)
    if not isinstance(top_k, int) or top_k <= 0:
        msg = f"top_k must be a positive int, got {top_k!r}"
        raise SearchRequestValidationError(msg)


class SearchSubgraph:
    """per-dataset 检索子图 (intra-fusion 融合)。

    Args:
        dataset_id: 待搜索 dataset 的 UUID。
        vector_retriever: 向量检索 LangChain Runnable。
        fulltext_retriever: 全文检索 LangChain Runnable。
        rrf_k: RRF k 常数, 默认 60。
        vector_weight: 向量结果权重, 默认 0.7。
        fulltext_weight: 全文结果权重, 默认 0.3。
        top_k: 单源 top-k, 默认 10。
    """

    DEFAULT_TOP_K: int = 10
    DEFAULT_VECTOR_WEIGHT: float = 0.7
    DEFAULT_FULLTEXT_WEIGHT: float = 0.3
    DEFAULT_RRF_K: int = 60

    def __init__(
        self,
        *,
        dataset_id: uuid.UUID,
        vector_retriever: Runnable,
        fulltext_retriever: Runnable,
        rrf_k: int = DEFAULT_RRF_K,
        vector_weight: float = DEFAULT_VECTOR_WEIGHT,
        fulltext_weight: float = DEFAULT_FULLTEXT_WEIGHT,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        if vector_weight < 0 or fulltext_weight < 0:
            msg = (
                f"weights must be non-negative, got {vector_weight=} {fulltext_weight=}"
            )
            raise SearchRequestValidationError(msg)
        self.dataset_id = dataset_id
        self.vector_retriever = vector_retriever
        self.fulltext_retriever = fulltext_retriever
        self.rrf_k = rrf_k
        self.vector_weight = vector_weight
        self.fulltext_weight = fulltext_weight
        self.top_k = top_k

    async def ainvoke(self, query: str) -> list[ScoredDocument]:
        """per-dataset 检索: 校验 → 并行向量+全文 → intra-fuse 融合。"""
        validate_subgraph_request(
            query=query, dataset_id=self.dataset_id, top_k=self.top_k
        )

        vec_hits, ft_hits = await asyncio.gather(
            self._safe_retrieve(self.vector_retriever, query),
            self._safe_retrieve(self.fulltext_retriever, query),
        )

        return intra_fusion(
            [vec_hits, ft_hits],
            weights=[self.vector_weight, self.fulltext_weight],
            rrf_k=self.rrf_k,
        )

    async def _safe_retrieve(
        self, retriever: Runnable, query: str
    ) -> list[ScoredDocument]:
        """调用 ``retriever.ainvoke`` 并做安全错误处理。"""
        try:
            result = await retriever.ainvoke({"query": query, "top_k": self.top_k})
        except Exception as e:
            logger.warning(
                "Retriever %r failed for dataset %s: %r",
                type(retriever).__name__,
                self.dataset_id,
                e,
            )
            return []
        return list(result) if result else []
