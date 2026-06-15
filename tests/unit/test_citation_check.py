"""Unit tests for ``rag.infra.text.citation_check`` (5e2).

Tests cover Contract 5 marker validation:
- All referenced ids map to actual citations → valid=True
- Out-of-range ids (id < 1 or id > n) → valid=False, out_of_range_ids populated
- Orphan citations (in citations list but not referenced) → orphan_citation_indices populated
- Empty response / empty citations
- Repeated ids preserved in referenced_ids
"""

from __future__ import annotations

import uuid

import pytest

from rag.domain.search import Citation
from rag.infra.text.citation_check import CitationChecker


def _cite(idx_1based: int) -> Citation:
    """Construct a Citation with chunk_id derived from 1-based idx."""
    return Citation(
        chunk_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        source_name=f"src-{idx_1based}",
        content=f"content {idx_1based}",
        score=0.5,
    )


# ---------- Happy path ----------


def test_check_valid_when_all_markers_in_range() -> None:
    response = "Paris [1](CITE) is the capital. Lyon [2](CITE) is second."
    citations = [_cite(1), _cite(2)]
    result = CitationChecker().check(response, citations)
    assert result.valid is True
    assert result.out_of_range_ids == []
    assert result.referenced_unique == [1, 2]


def test_check_empty_response_is_valid() -> None:
    """No markers → no out-of-range → valid=True."""
    citations = [_cite(1), _cite(2)]
    result = CitationChecker().check("", citations)
    assert result.valid is True
    assert result.referenced_ids == []
    assert result.orphan_citation_indices == [0, 1]  # both orphan


def test_check_empty_citations_with_markers_invalid() -> None:
    """Has markers but no citations → all markers out of range."""
    response = "[1](CITE) [2](CITE)"
    result = CitationChecker().check(response, [])
    assert result.valid is False
    assert sorted(result.out_of_range_ids) == [1, 2]


def test_check_both_empty() -> None:
    """No markers, no citations → valid=True (trivially)."""
    result = CitationChecker().check("", [])
    assert result.valid is True
    assert result.referenced_ids == []


# ---------- Out-of-range detection ----------


def test_check_id_greater_than_n_is_out_of_range() -> None:
    """[3](CITE) when only 2 citations exist → invalid."""
    response = "[1](CITE) [3](CITE)"
    citations = [_cite(1), _cite(2)]
    result = CitationChecker().check(response, citations)
    assert result.valid is False
    assert 3 in result.out_of_range_ids


def test_check_id_zero_is_out_of_range() -> None:
    """[0](CITE) is invalid (1-based)."""
    response = "[0](CITE)"
    citations = [_cite(1)]
    result = CitationChecker().check(response, citations)
    assert result.valid is False
    assert 0 in result.out_of_range_ids


def test_check_id_negative_impossible_due_to_regex() -> None:
    """Negative ids 不会被 regex 捕获 (\\d+ 不允许符号)。"""
    response = "[1](CITE)"  # 没有负数 id
    citations = [_cite(1)]
    result = CitationChecker().check(response, citations)
    assert result.valid is True


def test_check_multi_digit_id_in_range_valid() -> None:
    """[10](CITE) with 10 citations → valid."""
    response = "[10](CITE)"
    citations = [_cite(i) for i in range(1, 11)]
    result = CitationChecker().check(response, citations)
    assert result.valid is True


def test_check_multi_digit_id_out_of_range() -> None:
    """[11](CITE) with 10 citations → invalid."""
    response = "[11](CITE)"
    citations = [_cite(i) for i in range(1, 11)]
    result = CitationChecker().check(response, citations)
    assert result.valid is False
    assert 11 in result.out_of_range_ids


# ---------- Orphan citation detection ----------


def test_check_orphan_citations_listed() -> None:
    """Citations whose id never referenced → orphan_citation_indices 包含其 index."""
    response = "[1](CITE)"  # only cites [1]
    citations = [_cite(1), _cite(2), _cite(3)]  # [2] and [3] are orphans
    result = CitationChecker().check(response, citations)
    # Indices 1 and 2 are orphan (0-based)
    assert result.orphan_citation_indices == [1, 2]


def test_check_no_orphans_when_all_referenced() -> None:
    response = "[1](CITE) [2](CITE) [3](CITE)"
    citations = [_cite(1), _cite(2), _cite(3)]
    result = CitationChecker().check(response, citations)
    assert result.orphan_citation_indices == []


# ---------- Repeated ids ----------


def test_check_repeated_ids_preserved_in_referenced_ids() -> None:
    """[3](CITE) cited twice → referenced_ids = [3, 3], referenced_unique = [3]."""
    response = "[3](CITE) and again [3](CITE)"
    citations = [_cite(1), _cite(2), _cite(3)]
    result = CitationChecker().check(response, citations)
    assert result.referenced_ids == [3, 3]
    assert result.referenced_unique == [3]


def test_check_mixed_valid_and_out_of_range() -> None:
    """[1](CITE) valid + [99](CITE) invalid → valid=False, out_of_range=[99]."""
    response = "[1](CITE) and [99](CITE)"
    citations = [_cite(1), _cite(2)]
    result = CitationChecker().check(response, citations)
    assert result.valid is False
    # 99 在 out_of_range_ids 中 (超出 len=2)
    assert result.out_of_range_ids == [99]
    # referenced_unique 是所有 referenced 的 unique (包括越界)
    assert 1 in result.referenced_unique
    assert 99 in result.referenced_unique


# ---------- Non-CITE markers ignored ----------


def test_check_ignores_other_brackets() -> None:
    """[1](http://x) 不是 CITE 标记, 不被解析。"""
    response = "[1](http://example.com) and [1](CITE)"
    citations = [_cite(1)]
    result = CitationChecker().check(response, citations)
    assert result.valid is True
    assert result.referenced_ids == [1]


# ---------- Result immutability ----------


def test_result_is_frozen() -> None:
    """CitationCheckResult is frozen (immutable)."""
    from pydantic import ValidationError

    result = CitationChecker().check("", [_cite(1)])
    with pytest.raises(ValidationError, match="frozen"):
        result.valid = False  # type: ignore[misc]


# ---------- 0-based vs 1-based ----------


def test_orphan_indices_are_0_based() -> None:
    """orphan_citation_indices 是 0-based (对应 citations list index)。"""
    response = "[1](CITE)"  # only first citation referenced
    citations = [_cite(1), _cite(2), _cite(3)]
    result = CitationChecker().check(response, citations)
    # citations[0] referenced, citations[1] and citations[2] orphan
    # 0-based indices: 1 and 2
    assert result.orphan_citation_indices == [1, 2]


# ---------- CitationCheckResult schema ----------


def test_result_unused_orphan_marker_ids_alias() -> None:
    """unused_orphan_marker_ids 与 out_of_range_ids 等价 (alias)."""
    response = "[99](CITE)"
    citations = [_cite(1)]
    result = CitationChecker().check(response, citations)
    assert result.unused_orphan_marker_ids == result.out_of_range_ids
    assert result.unused_orphan_marker_ids == [99]
