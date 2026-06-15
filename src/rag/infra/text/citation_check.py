"""Citation marker parsing + validation per Contract 5.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 5:
- LLM's response contains ``[id](CITE)`` markers where id is 1-based
  index into ``SearchResult.citations``.
- ``CitationChecker`` validates that all markers map to actual citations,
  and reports orphan citations (in citations list but not referenced).

Also provides marker-parsing helpers used by the cite stage:
- ``parse_inline_citations(response)``: extract 1-based ids from markers
- ``resolve_citation_positions(response, citations)``: fill Citation.position

This module is the consolidated home for all citation-marker text logic;
it depends only on ``rag.domain.search.Citation`` (type) and the local regex,
so it lives in ``infra/text/`` with no business-package dependency.

Note: regex-based validation only (no LLM hallucination detection, per
audit G-P0-2).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.search import Citation

# Regex: [N](CITE) where N is 1+ digits. Captures the number.
_INLINE_CITE_RE: re.Pattern[str] = re.compile(r"\[(\d+)\]\(CITE\)")


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


# ---------- Validation ----------


class CitationCheckResult(BaseModel):
    """Result of validating ``[id](CITE)`` markers in a response.

    Attributes:
        valid: True iff all referenced ids are in range ``[1, len(citations)]``.
        referenced_ids: Ordered list of ids found in response (with duplicates
            preserved, as in parse_inline_citations).
        referenced_unique: Sorted unique ids.
        out_of_range_ids: ids referenced in response but ``> len(citations)``
            or ``< 1``.
        orphan_citation_indices: 0-based indices into ``citations`` for
            citations NOT referenced anywhere in the response. (0-based
            indices, NOT 1-based ids — add 1 to get the [id] value.)
        unused_orphan_marker_ids: 1-based ids that resolve to no citation
            (alias for out_of_range_ids, for symmetry in caller code).
    """

    model_config = ConfigDict(frozen=True)

    valid: bool
    referenced_ids: list[int] = Field(default_factory=list)
    referenced_unique: list[int] = Field(default_factory=list)
    out_of_range_ids: list[int] = Field(default_factory=list)
    orphan_citation_indices: list[int] = Field(default_factory=list)
    unused_orphan_marker_ids: list[int] = Field(default_factory=list)


class CitationChecker:
    """Validate ``[id](CITE)`` markers in LLM response against citations list.

    Stateless: pass ``response`` + ``citations`` to ``check()``. Use in
    pipeline post-gen step (after ``SearchResult.response`` is finalized)
    or in eval / debug tools.

    Example:
        result = checker.check(
            response="Paris [1](CITE) is the capital.",
            citations=[Citation(...)],
        )
        if not result.valid:
            log.warning(f"out-of-range markers: {result.out_of_range_ids}")
    """

    def check(self, response: str, citations: list[Citation]) -> CitationCheckResult:
        """Run full validation.

        Returns:
            CitationCheckResult with all discrepancies. ``valid=True`` only
            when no out-of-range markers exist.
        """
        ids = parse_inline_citations(response)
        unique_ids = sorted(set(ids))
        n = len(citations)

        out_of_range = [i for i in ids if i < 1 or i > n]
        referenced_set = set(ids)
        orphan = [idx for idx in range(n) if (idx + 1) not in referenced_set]

        return CitationCheckResult(
            valid=not out_of_range,
            referenced_ids=ids,
            referenced_unique=unique_ids,
            out_of_range_ids=out_of_range,
            orphan_citation_indices=orphan,
            unused_orphan_marker_ids=out_of_range,  # alias
        )
