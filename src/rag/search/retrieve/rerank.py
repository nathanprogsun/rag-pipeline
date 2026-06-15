"""Rerank stage 4+5 per Contract 8.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 8:

  4. Rerank (text-only hits) — this module
  5. Re-fuse intra_fusion over [reranked_text_hits, original_text_hits]
     with weights=[rerank_weight, 1 - rerank_weight]

Invariants:
- Rerank is **text-only**: image_caption modality hits bypass rerank
  (Contract 8: "Image hits bypass step 4 and are added at step 5/6
   with weight 1.0").
- score_breakdown["rerank"] is populated with raw rerank score (Contract 2).
- ``rerank_score`` (ScoredDocument field) is also populated for downstream
  consumers that read it directly.
- intra_fusion re-fuse applies per-source max merge on score_breakdown,
  preserving vector / fulltext / rerank raw scores.
- On reranker failure: log warning + fall back to original order
  (no crash, orchestrator's downstream stages still work).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.document import ScoredDocument
from rag.domain.search import SearchRequest
from rag.search.retrieve.fusion import intra_fusion

logger = logging.getLogger(__name__)


class RerankStageProtocol(Protocol):
    """Stage 4+5 callback. Takes fused hits, returns re-fused hits."""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


class _TextReranker(Protocol):
    """Text-only reranker contract. ``(idx, score)`` pairs by relevance desc."""

    async def rerank(
        self, query: str, documents: list[str], top_k: int
    ) -> list[tuple[int, float]]: ...


class NoOpRerankStage:
    """Identity passthrough — used when rerank is disabled (no API key,
    use_rerank=False in SearchRequest.retrieval).
    """

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        return list(docs)


class RerankStageAdapter:
    """Stage 4+5 adapter: rerank text hits + re-fuse with original.

    Per Contract 8:
      1. Split docs into text (modality=text) and image (modality=image_caption).
      2. Rerank text docs via ``self.reranker``.
      3. Populate ``score_breakdown["rerank"]`` and ``rerank_score`` on
         reranked copies (Contract 2 invariant).
      4. Re-fuse: ``intra_fusion([reranked, original_text],
         weights=[rerank_weight, 1-rerank_weight])``.
      5. Append image hits via ``intra_fusion([..., image], weights=[1, 1])``
         — image hits bypass rerank per Contract 8.

    Args:
        reranker: Text-only reranker (e.g. ``rag.infra.llm.rerank.QwenRerank``
            or ``NoOpRerank``). Must implement ``rerank(query, docs, top_k)
            -> list[(idx, score)]``.
        rerank_weight: Per-source weight for reranked hits in re-fuse
            (default 0.7, matches FastGPT default). 1.0 = trust rerank
            fully; 0.0 = trust original RRF fully.
        on_error: Optional async callback receiving the original docs and
            the exception, for logging/warnings. None means silent fallback.

    Raises:
        Never — reranker exceptions are caught, logged, and the original
        docs are returned unchanged (orchestrator keeps working).
    """

    DEFAULT_RERANK_WEIGHT: float = 0.7

    def __init__(
        self,
        *,
        reranker: _TextReranker,
        rerank_weight: float = DEFAULT_RERANK_WEIGHT,
        on_error: Callable[[list[ScoredDocument], BaseException], Awaitable[None]]
        | None = None,
    ) -> None:
        if rerank_weight < 0 or rerank_weight > 1:
            msg = f"rerank_weight must be in [0, 1], got {rerank_weight}"
            raise ValueError(msg)
        self.reranker = reranker
        self.rerank_weight = rerank_weight
        self.on_error = on_error

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        if not docs:
            return []

        text_docs = [d for d in docs if d.modality == "text"]
        image_docs = [d for d in docs if d.modality == "image_caption"]

        if not text_docs:
            return list(image_docs)

        try:
            results = await self.reranker.rerank(
                req.query,
                [d.text for d in text_docs],
                top_k=len(text_docs),
            )
        except Exception as e:
            logger.warning(
                "Rerank failed for query=%r, falling back to original order: %r",
                req.query,
                e,
            )
            if self.on_error is not None:
                await self.on_error(list(docs), e)
            return list(docs)

        reranked_text: list[ScoredDocument] = []
        for orig_idx, score in results:
            if orig_idx < 0 or orig_idx >= len(text_docs):
                logger.warning(
                    "Reranker returned out-of-range index %d (text_docs len=%d)",
                    orig_idx,
                    len(text_docs),
                )
                continue
            original = text_docs[orig_idx]
            reranked_text.append(
                original.model_copy(
                    update={
                        "rerank_score": score,
                        "score_breakdown": {
                            **original.score_breakdown,
                            "rerank": score,
                        },
                    }
                )
            )

        if self.rerank_weight >= 1.0:
            text_fused: list[ScoredDocument] = reranked_text
        elif self.rerank_weight <= 0.0:
            text_fused = text_docs
        else:
            text_fused = intra_fusion(
                [reranked_text, text_docs],
                weights=[self.rerank_weight, 1.0 - self.rerank_weight],
            )

        if not image_docs:
            return text_fused
        return intra_fusion(
            [text_fused, image_docs],
            weights=[1.0, 1.0],
        )
