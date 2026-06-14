"""ParentDoc stage 8 per Contract 8.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 8:

  8. ParentDoc Expand (parent_doc.py)

For each matched chunk, expand to its parent window:
``[chunk_index - window_size, chunk_index + window_size]`` via
``ChunkRepository.get_siblings(dataset_id, parent_title, lo, hi)``.

The matched chunk keeps its original score (it's the actual match).
Siblings get a decayed score (default ``score * 0.5``) so they rank
below the actual match but still above unrelated chunks.

Image_caption modality hits bypass parent_doc expansion (their parent
context is the image itself, not text siblings).

This stage reads ``req.context.parent_doc_window`` (default 0 = disabled)
from ``SearchRequest`` to decide window size at runtime.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest
from rag.infra.pg.repositories.chunk_repo import ChunkRepository

logger = logging.getLogger(__name__)


# ---------- Stage protocol ----------


class ParentDocStage(Protocol):
    """Stage 8 callback. Expands matched chunks to parent windows."""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]: ...


# ---------- Default impl: NoOp ----------


class NoOpParentDoc:
    """Identity passthrough — used when parent_doc_window=0."""

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        return list(docs)


# ---------- Real impl: ParentDocExpander ----------


class ParentDocExpander:
    """Expand matched chunks to their parent window via ChunkRepository.

    Per Contract 8 stage 8:
      - For each matched chunk, fetch siblings in
        ``[chunk_index - window_size, chunk_index + window_size]``.
      - Matched chunk keeps original score.
      - Siblings get score * ``decay`` (default 0.5) so they rank below
        the actual match but still above unrelated chunks.
      - image_caption modality hits bypass expansion (return as-is).
      - Dedup by chunk_id (overlapping windows share siblings).

    Args:
        chunk_repo: ChunkRepository instance (must already have a session
            wired or session_factory pattern; see caller's responsibility).
        default_window: Fallback window size when ``req.context.parent_doc_window``
            is 0. Use ``default_window`` if you want a non-zero window
            regardless of request; else pass 0 to require explicit opt-in.
        sibling_decay: Score multiplier for siblings (default 0.5).
            0.0 = siblings kept but score=0 (LLM may still use as context);
            1.0 = siblings rank equal to matched (no decay).
        on_error: Optional async callback receiving original docs + exception,
            for warnings. None = silent fallback.

    Raises:
        Never — chunk_repo exceptions are caught, logged, and original
        docs are returned unchanged.
    """

    DEFAULT_SIBLING_DECAY: float = 0.5

    def __init__(
        self,
        *,
        chunk_repo: ChunkRepository,
        default_window: int = 0,
        sibling_decay: float = DEFAULT_SIBLING_DECAY,
        on_error: Callable[[list[ScoredDocument], BaseException], Awaitable[None]]
        | None = None,
    ) -> None:
        if default_window < 0:
            msg = f"default_window must be >= 0, got {default_window}"
            raise ValueError(msg)
        if sibling_decay < 0 or sibling_decay > 1:
            msg = f"sibling_decay must be in [0, 1], got {sibling_decay}"
            raise ValueError(msg)
        self.chunk_repo = chunk_repo
        self.default_window = default_window
        self.sibling_decay = sibling_decay
        self.on_error = on_error

    async def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[ScoredDocument]:
        # Window size from request context (allow runtime override)
        window = req.context.parent_doc_window or self.default_window
        if window == 0 or not docs:
            return list(docs)

        expanded: list[ScoredDocument] = []
        seen_ids: set = set()
        # Track which original chunk_ids are matched (preserve original
        # score when re-emitting)
        matched_ids: set = {d.chunk_id for d in docs}

        try:
            for doc in docs:
                # image_caption bypasses parent expansion (Contract 8 invariant)
                if doc.modality == "image_caption":
                    if doc.chunk_id not in seen_ids:
                        seen_ids.add(doc.chunk_id)
                        expanded.append(doc)
                    continue

                parent_title = doc.metadata.parent_title
                if not parent_title:
                    # No parent_title → can't expand, keep original
                    if doc.chunk_id not in seen_ids:
                        seen_ids.add(doc.chunk_id)
                        expanded.append(doc)
                    continue

                lo = max(0, doc.metadata.chunk_index - window)
                hi = doc.metadata.chunk_index + window

                siblings = await self.chunk_repo.get_siblings(
                    dataset_id=doc.dataset_id,
                    parent_title=parent_title,
                    lo=lo,
                    hi=hi,
                )

                for sib in siblings:
                    if sib.id in seen_ids:
                        continue
                    seen_ids.add(sib.id)

                    if sib.id in matched_ids:
                        # This is the original matched chunk — preserve
                        # original score (and the original doc object
                        # in case it has other state we shouldn't lose)
                        # The first sighting is always the original match
                        # because we iterate docs first.
                        # Find the original doc with this chunk_id:
                        orig = next((d for d in docs if d.chunk_id == sib.id), None)
                        expanded.append(orig if orig is not None else _to_scored(sib, doc.score))
                    else:
                        # Sibling: decay score
                        expanded.append(
                            _to_scored(sib, doc.score * self.sibling_decay)
                        )
        except Exception as e:
            logger.warning(
                "ParentDoc expansion failed, falling back to original order: %r",
                e,
            )
            if self.on_error is not None:
                await self.on_error(list(docs), e)
            return list(docs)

        return expanded


def _to_scored(chunk: DomainChunk, score: float) -> ScoredDocument:
    """Convert ChunkRepository domain Chunk → ScoredDocument with given score."""
    return ScoredDocument(
        chunk_id=chunk.id,
        dataset_id=chunk.dataset_id,
        text=chunk.text,
        score=score,
        rank=0,
        source="vector",
        modality=chunk.modality,
        image_path=chunk.image_path,
        metadata=ChunkMetadata(
            dataset_id=chunk.dataset_id,
            datasource=chunk.metadata.datasource,
            filename=chunk.metadata.filename,
            parent_title=chunk.metadata.parent_title,
            chunk_index=chunk.metadata.chunk_index,
        ),
    )