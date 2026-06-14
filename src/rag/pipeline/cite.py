"""Cite stage 9 per Contract 5.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 5:

  Format: The LLM's ``response`` text contains ``[id](CITE)`` markers,
  where ``id`` is the 1-based index into ``SearchResult.citations``.

  ``Citation.position: int | None = None`` — 1-based position in
  ``response`` (inferred by regex during cite step, or set by LLM-aware
  post-processor).

Stage 9 produces ``list[Citation]`` from final ScoredDocument list.
The 1-based index is implicit in the list order (``citations[0]``
corresponds to ``[1](CITE)`` in ``response``).

This module provides:
- ``SimpleCite``: default stage 9 — number docs 1-based, build Citation DTOs
- ``parse_inline_citations(response)``: parse ``[id](CITE)`` markers
- ``resolve_citation_positions(response, citations)``: fill Citation.position
  by mapping marker offsets back to Citation objects

The orchestrator calls ``cite(docs, req)`` BEFORE generation (stage 9 < stage 10),
so ``resolve_citation_positions`` is meant for post-processing after gen.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest

# Regex: [N](CITE) where N is 1+ digits. Captures the number.
_INLINE_CITE_RE: re.Pattern[str] = re.compile(r"\[(\d+)\]\(CITE\)")


# ---------- Stage protocol ----------


class CiteStageProtocol:
    """Stage 9 callback. Maps final ScoredDocument list to Citation DTOs.

    Note: this is a Callable, not a strict Protocol, so the orchestrator
    can use either class instances or plain functions.
    """

    def __call__(
        self, docs: list[ScoredDocument], req: SearchRequest
    ) -> list[Citation]:
        raise NotImplementedError


# ---------- Default implementation ----------


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


# ---------- Marker parsing utilities ----------


def parse_inline_citations(response: str) -> list[int]:
    """Parse ``[id](CITE)`` markers in response text.

    Returns ordered list of 1-based citation ids referenced. A single
    response may reference the same id multiple times; each occurrence
    is captured (callers can dedup with ``sorted(set(...))``).

    Examples:
        "a [1](CITE) b [2](CITE) c" -> [1, 2]
        "cited [3](CITE) and [3](CITE) again" -> [3, 3]
        "no citations here" -> []
    """
    if not response:
        return []
    return [int(m.group(1)) for m in _INLINE_CITE_RE.finditer(response)]


def resolve_citation_positions(
    response: str,
    citations: list[Citation],
) -> list[Citation]:
    """Populate ``Citation.position`` based on ``[id](CITE)`` markers.

    For each ``citations[i]`` (0-based) corresponding to ``id=i+1``,
    finds the FIRST occurrence of ``[i+1](CITE)`` in ``response`` and
    sets ``citation.position`` to the character offset of that marker.

    Citations not referenced in ``response`` get ``position=None``.
    Citations with id out of range (id > len(citations)) are ignored
    by the regex (no match), so they also get ``position=None``.

    Args:
        response: LLM-generated response text (may be empty).
        citations: Citation list, 0-based; position[i] corresponds to id i+1.

    Returns:
        New list of Citation with ``position`` field populated where applicable.
    """
    if not response or not citations:
        return list(citations)

    # Build {id (1-based): first_offset}
    first_offset: dict[int, int] = {}
    for m in _INLINE_CITE_RE.finditer(response):
        cid = int(m.group(1))
        if cid not in first_offset:
            first_offset[cid] = m.start()

    result: list[Citation] = []
    for idx, c in enumerate(citations):
        cid = idx + 1
        offset = first_offset.get(cid)
        if offset is not None:
            result.append(c.model_copy(update={"position": offset}))
        else:
            result.append(c)
    return result