"""Per-dataset retrieval subgraph (intra-fusion per Contract 1).

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 8 stage
ordering: subgraph runs in parallel per-dataset, then orchestrator does
inter-fusion + filter + cite + generate.

Per-dataset flow:
1. Validate request (query non-empty, top_k positive, dataset_id is UUID)
2. Vector recall (async) — via ``VectorRetriever``
3. Fulltext recall (async) — via ``FulltextRetriever``
4. Intra-fuse with weights ``[vector_weight, fulltext_weight]`` (Contract 1)

Returns ``list[ScoredDocument]`` for the single dataset.

Reuses existing ``VectorRetriever`` / ``FulltextRetriever`` from
``rag.infra.pg`` (both are LangChain ``Runnable``). No new DB code.
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
    """Validate per-dataset subgraph request. Raises on invalid input.

    Per FastGPT subgraph design (task 14): each subgraph is responsible
    for its own input validation before kicking off retrieval.
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
    """Per-dataset retrieval subgraph (intra-fusion per Contract 1).

    Args:
        dataset_id: UUID of the dataset to search.
        vector_retriever: LangChain Runnable for vector recall (e.g.
            ``rag.infra.pg.vector_store.VectorRetriever``).
        fulltext_retriever: LangChain Runnable for fulltext recall (e.g.
            ``rag.infra.pg.fulltext_store.FulltextRetriever``).
        rrf_k: RRF k constant (default 60, Cormack 2009).
        vector_weight: Weight for vector result list (default 0.7).
        fulltext_weight: Weight for fulltext result list (default 0.3).
        top_k: Top-k per source (default 10).
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
        """Per-dataset retrieval: validate → parallel vector+fulltext → intra-fuse."""
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
        """Call retriever.ainvoke with safe error handling."""
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
