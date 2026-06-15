"""Cite stage 9 per Contract 5.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 5:

  Format: The LLM's ``response`` text contains ``[id](CITE)`` markers,
  where ``id`` is the 1-based index into ``SearchResult.citations``.

Stage 9 produces ``list[Citation]`` from final ScoredDocument list.
The 1-based index is implicit in the list order (``citations[0]``
corresponds to ``[1](CITE)`` in ``response``).

This module provides:
- ``SimpleCite``: default stage 9 — number docs 1-based, build Citation DTOs

Marker-parsing utilities (``parse_inline_citations``, ``resolve_citation_positions``)
have moved to ``rag.infra.text.citation_check`` and are imported by
``CitationChecker`` from there. The ``SimpleCite`` generator remains here
because it knows the search-specific Citation DTO shape.
"""

from __future__ import annotations

from collections.abc import Callable

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest


class CiteStageProtocol:
    """Stage 9 callback. Maps final ScoredDocument list to Citation DTOs.

    Note: this is a Callable, not a strict Protocol, so the orchestrator
    can use either class instances or plain functions.
    """

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]:
        raise NotImplementedError


class SimpleCite:
    """Number docs 1-based, build Citation DTOs preserving order.

    Each ``ScoredDocument`` becomes one ``Citation``:
    - ``chunk_id`` / ``dataset_id``: from ScoredDocument
    - ``source_name``: derived from ``source_name_fn`` (default: "src-{i}")
    - ``content``: ScoredDocument.text
    - ``image_path``: ScoredDocument.image_path (None for text chunks)
    - ``score``: ScoredDocument.score (post-fusion RRF; for raw per-source
                use ``score_breakdown`` directly)

    Args:
        source_name_fn: Optional override for citation naming. Receives
            (ScoredDocument, 1-based index) and returns a string.
            Default: ``"src-{i}"`` (FastGPT-style).
    """

    DEFAULT_SOURCE_NAME: str = "src-{i}"

    def __init__(
        self,
        *,
        source_name_fn: Callable[[ScoredDocument, int], str] | None = None,
    ) -> None:
        self.source_name_fn = source_name_fn or self._default_source_name

    @staticmethod
    def _default_source_name(doc: ScoredDocument, idx: int) -> str:
        """Default 1-based naming."""
        return SimpleCite.DEFAULT_SOURCE_NAME.format(i=idx)

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]:
        return [
            Citation(
                chunk_id=d.chunk_id,
                dataset_id=d.dataset_id,
                source_name=self.source_name_fn(d, i),
                content=d.text,
                image_path=d.image_path,
                score=d.score,
            )
            for i, d in enumerate(docs, start=1)
        ]
