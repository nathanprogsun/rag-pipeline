"""Multi-dataset retrieval+gen orchestrator per Contract 8.

Composes the 10 stages from `.agents/design/2026-06-14-cross-task-contracts.md`
Contract 8:

  1. query_ext (optional)                        — search.extension.query_ext
  2. per-variant per-dataset subgraph retrieval — search.retrieve.subgraph
  3. inter-variant intra_fusion                  — search.retrieve.fusion
  4. rerank (optional)                           — search.retrieve.rerank
  5. re-fuse (optional, baked into step 4)       — search.retrieve.rerank
  6. inter-dataset fusion                        — implicit in step 2
  7. filter (dedup + score + token budget)       — search.post.filter
  8. parent_doc expand (optional)                — search.post.parent_doc
  9. cite (optional)                             — search.post.cite
  10. generation (optional)                      — search.generate.answer

Public API:
    SearchPipeline: ainvoke(SearchRequest) -> SearchResult

Populates ``SearchResult._intermediate_hits`` (Contract 6: Field(exclude=True))
for audit_tap / EvalRunner consumers; the field is excluded from
``model_dump_json()`` output.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.search.extension.query_ext import QueryExtensionRunnable
from rag.search.post.filter import (
    DEFAULT_TOKEN_BUDGET,
    filter_by_score,
    filter_by_token_budget,
)
from rag.search.retrieve.fusion import DEFAULT_RRF_K, intra_fusion
from rag.search.retrieve.subgraph import SearchSubgraph

logger = logging.getLogger(__name__)


# ---------- Optional stage callbacks ----------


class RerankStage(Protocol):
    """Optional stage 4+5: rerank + re-fuse."""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


class ParentDocStage(Protocol):
    """Optional stage 8: parent_doc window expansion."""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


class CiteStage(Protocol):
    """Optional stage 9: cite formatter.

    Receives final hits + SearchRequest, returns citations list (1-based
    positions matching ``[id](CITE)`` markers in ``response``).
    """

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]: ...


# GenStage is defined in search.generate.answer (it lives with the LLM
# gen implementation, which is its only concrete producer).


# Functional aliases for test ergonomics (callables also accepted)
RerankFn = Callable[
    [list[ScoredDocument], SearchRequest], Awaitable[list[ScoredDocument]]
]
ParentDocFn = Callable[
    [list[ScoredDocument], SearchRequest], Awaitable[list[ScoredDocument]]
]
CiteFn = Callable[[list[ScoredDocument], SearchRequest], list[Citation]]


# ---------- Orchestrator ----------


class SearchPipeline:
    """Multi-dataset retrieval+gen orchestrator per Contract 8.

    Args:
        subgraphs: Map of ``dataset_id -> SearchSubgraph``. Each subgraph
            owns its own vector+fulltext retrievers (per dataset).
        query_ext: Optional query extension. None = identity (only original
            query is used, no LLM rewrite).
        filter_score_threshold: Optional per-source raw score threshold
            for ``filter_by_score`` (Contract 2: reads ``score_breakdown``,
            not RRF ``.score``). None = no threshold filter.
        token_budget: Max tokens for final hits (default 960K for
            MiniMax-M3 1M context minus 40K headroom).
        rerank: Optional stage 4+5 callback. None = no rerank.
        parent_doc: Optional stage 8 callback. None = no expansion.
        cite: Optional stage 9 callback. None = empty citations.
        gen: Optional stage 10 callback. None = empty response.
        rrf_k: RRF k constant for intra_fusion calls (default 60).

    Raises:
        ValueError: If ``subgraphs`` is empty.
    """

    def __init__(
        self,
        *,
        subgraphs: dict[uuid.UUID, SearchSubgraph],
        query_ext: QueryExtensionRunnable | None = None,
        filter_score_threshold: float | None = None,
        token_budget: int = DEFAULT_TOKEN_BUDGET,
        rerank: RerankFn | None = None,
        parent_doc: ParentDocFn | None = None,
        cite: CiteFn | None = None,
        gen: Callable | None = None,  # GenFn, loose type to avoid cycle
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        if not subgraphs:
            msg = "subgraphs must be a non-empty dict"
            raise ValueError(msg)
        self.subgraphs = subgraphs
        self.query_ext = query_ext
        self.filter_score_threshold = filter_score_threshold
        self.token_budget = token_budget
        self.rerank = rerank
        self.parent_doc = parent_doc
        self.cite = cite
        self.gen = gen
        self.rrf_k = rrf_k

    async def ainvoke(self, req: SearchRequest) -> SearchResult:
        """Run the 10-stage pipeline. Returns ``SearchResult`` with
        ``_intermediate_hits`` populated (Contract 6).
        """
        internal_warnings: list[str] = []

        # Stage 1: query extension (None → identity, single variant = original)
        variants = self._extend_query(req, internal_warnings)

        # Stages 2-3: per-variant per-dataset retrieval + inter-variant fusion
        variant_hits: list[list[ScoredDocument]] = await asyncio.gather(
            *(self._recall_one_variant(v, req, internal_warnings) for v in variants)
        )
        fused: list[ScoredDocument] = (
            intra_fusion(variant_hits, rrf_k=self.rrf_k) if variant_hits else []
        )

        # Stage 4-5: rerank + re-fuse (optional)
        if self.rerank is not None:
            fused = await self.rerank(fused, req)

        # Stage 7: filter (dedup by chunk_id, then threshold, then token budget)
        fused = _dedup_by_chunk_id(fused)
        if self.filter_score_threshold is not None and fused:
            fused, _ = filter_by_score(
                fused,
                threshold=self.filter_score_threshold,
                search_mode="mixed",
            )
        if fused:
            fused = filter_by_token_budget(fused, max_tokens=self.token_budget)

        # Stage 8: parent_doc expand (optional)
        if self.parent_doc is not None and fused:
            fused = await self.parent_doc(fused, req)

        # Stage 9: cite (optional)
        citations: list[Citation] = (
            list(self.cite(fused, req)) if self.cite is not None else []
        )

        # Stage 10: generation (optional)
        response: str = (
            await self.gen(fused, citations, req) if self.gen is not None else ""
        )

        failed_dataset_ids = [d for d in req.dataset_ids if d not in self.subgraphs]

        result = SearchResult(
            response=response,
            citations=citations,
            failed_dataset_ids=failed_dataset_ids,
            warnings=internal_warnings,
        )
        result._intermediate_hits = list(fused)
        return result

    # ---- Stage helpers ----

    def _extend_query(self, req: SearchRequest, warnings: list[str]) -> list[str]:
        """Stage 1: produce query variants. None → [req.query] identity."""
        if self.query_ext is None:
            return [req.query]
        try:
            ext = self.query_ext(
                req.query,
                chat_bg=req.history.chat_bg,
                histories=[h.get("content", "") for h in req.history.histories],
            )
        except Exception as e:
            warnings.append(f"query_ext_failed: {e!r}")
            logger.warning(
                "query_ext failed for query=%r, falling back to original: %r",
                req.query,
                e,
            )
            return [req.query]
        return ext.deduped_variants if ext.deduped_variants else [req.query]

    async def _recall_one_variant(
        self,
        variant: str,
        req: SearchRequest,
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """Stage 2 (per-variant): per-dataset subgraph retrieval in parallel."""
        per_dataset_results = await asyncio.gather(
            *(
                self._safe_subgraph(ds_id, variant, warnings)
                for ds_id in req.dataset_ids
                if ds_id in self.subgraphs
            )
        )
        return (
            intra_fusion(per_dataset_results, rrf_k=self.rrf_k)
            if per_dataset_results
            else []
        )

    async def _safe_subgraph(
        self,
        ds_id: uuid.UUID,
        query: str,
        warnings: list[str],
    ) -> list[ScoredDocument]:
        """Call subgraph with safe error handling — a failing dataset
        must not abort the whole pipeline."""
        sg = self.subgraphs[ds_id]
        try:
            return list(await sg.ainvoke(query))
        except Exception as e:
            warnings.append(f"subgraph_failed:{ds_id}: {e!r}")
            logger.warning(
                "Subgraph for dataset %s failed on query=%r: %r",
                ds_id,
                query,
                e,
            )
            return []


# ---------- Pure helpers ----------


def _dedup_by_chunk_id(docs: list[ScoredDocument]) -> list[ScoredDocument]:
    """Stable chunk_id dedup preserving first-seen order.

    Why not ``rag.infra.observability.trace.remove_duplicates``: it requires
    ``RetrievalTrace`` parallel array (q, a), which the orchestrator
    doesn't carry at the post-fusion stage. At this point, hits have
    been RRF-merged across variants+datasets; we only need to drop
    accidental repeats.
    """
    seen: set[uuid.UUID] = set()
    out: list[ScoredDocument] = []
    for d in docs:
        if d.chunk_id in seen:
            continue
        seen.add(d.chunk_id)
        out.append(d)
    return out
