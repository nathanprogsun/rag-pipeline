"""Citation marker validation per Contract 5.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 5:
- LLM's response contains ``[id](CITE)`` markers where id is 1-based
  index into ``SearchResult.citations``.
- CitationChecker validates that all markers map to actual citations,
  and reports orphan citations (in citations list but not referenced).

This is task 15's citation_check: regex-based validation only
(no LLM hallucination detection, per audit G-P0-2).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rag.domain.search import Citation
from rag.pipeline.cite import parse_inline_citations


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

    def check(
        self, response: str, citations: list[Citation]
    ) -> CitationCheckResult:
        """Run full validation.

        Returns:
            CitationCheckResult with all discrepancies. ``valid=True`` only
            when no out-of-range markers exist.
        """
        ids = parse_inline_citations(response)
        unique_ids = sorted(set(ids))
        n = len(citations)

        # Out-of-range: id < 1 or id > n
        out_of_range = [i for i in ids if i < 1 or i > n]
        # Orphan citations: citations whose 1-based id never appears
        referenced_set = set(ids)
        orphan = [
            idx for idx in range(n) if (idx + 1) not in referenced_set
        ]

        return CitationCheckResult(
            valid=not out_of_range,
            referenced_ids=ids,
            referenced_unique=unique_ids,
            out_of_range_ids=out_of_range,
            orphan_citation_indices=orphan,
            unused_orphan_marker_ids=out_of_range,  # alias
        )