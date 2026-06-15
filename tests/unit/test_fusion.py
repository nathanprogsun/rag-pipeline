"""Unit tests for ``rag.search.retrieve.fusion`` per Contract 1 of
``.agents/design/2026-06-14-cross-task-contracts.md``.
"""

from __future__ import annotations

import math
import uuid

import pytest

from rag.domain.dataset import Dataset
from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.search.retrieve.fusion import DEFAULT_RRF_K, intra_fusion

# ---------- Fixtures ----------


def _meta(dataset_id: uuid.UUID | None = None) -> ChunkMetadata:
    return ChunkMetadata(
        dataset_id=dataset_id or uuid.uuid4(),
        datasource="file",
    )


def _doc(
    chunk_id_str: str,
    *,
    score: float = 0.0,
    source: str = "vector",
    dataset_id: uuid.UUID | None = None,
) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=dataset_id or uuid.uuid4(),
        text="x",
        score=score,
        rank=0,
        source=source,  # type: ignore[arg-type]
        metadata=_meta(dataset_id),
    )


# Standard chunk_id strings for readability
A = "00000000-0000-0000-0000-000000000001"
B = "00000000-0000-0000-0000-000000000002"
C = "00000000-0000-0000-0000-000000000003"


# ---------- Empty / edge cases ----------


def test_intra_empty_query_groups_returns_empty() -> None:
    assert intra_fusion([]) == []


def test_intra_all_empty_groups_returns_empty() -> None:
    assert intra_fusion([[], [], []]) == []


def test_intra_weights_length_mismatch_raises() -> None:
    hits = [_doc(A)]
    with pytest.raises(ValueError, match="weights length 1 != query_groups length 2"):
        intra_fusion([hits, hits], weights=[1.0])


# ---------- Single-group invariants ----------


def test_intra_wrrf_formula_basic() -> None:
    """1 group 1 chunk: rank=1, score = 1/(k+1)."""
    hits = [_doc(A, score=0.9)]
    fused = intra_fusion([hits])
    assert len(fused) == 1
    assert math.isclose(fused[0].score, 1.0 / (DEFAULT_RRF_K + 1))


def test_intra_local_rank_per_group() -> None:
    """1 group of 3 chunks: local ranks 1, 2, 3 -> 1/(k+1), 1/(k+2), 1/(k+3)."""
    a, b, c = _doc(A), _doc(B), _doc(C)
    fused = intra_fusion([[a, b, c]])
    by_id = {str(d.chunk_id): d for d in fused}
    assert math.isclose(by_id[A].score, 1.0 / (DEFAULT_RRF_K + 1))
    assert math.isclose(by_id[B].score, 1.0 / (DEFAULT_RRF_K + 2))
    assert math.isclose(by_id[C].score, 1.0 / (DEFAULT_RRF_K + 3))


# ---------- Cross-group accumulation ----------


def test_intra_dual_group_same_chunk() -> None:
    """Same chunk in 2 groups: RRF summed (both rank 1 -> 2/(k+1))."""
    a1 = _doc(A, score=0.5)
    a2 = _doc(A, score=0.7)
    fused = intra_fusion([[a1], [a2]])
    assert len(fused) == 1
    assert math.isclose(fused[0].score, 2.0 / (DEFAULT_RRF_K + 1))


def test_intra_local_rank_per_group_invariant() -> None:
    """B4 invariant: group1 第 1 个 chunk 的局部 rank 是 1,不是全局 rank。
    group1 length 3, group2 length 1, both contain a.
    a is rank 1 in both -> 2/(k+1).
    """
    a, b, c = _doc(A), _doc(B), _doc(C)
    fused = intra_fusion([[a, b, c], [a]])
    assert fused[0].chunk_id == a.chunk_id
    assert math.isclose(fused[0].score, 2.0 / (DEFAULT_RRF_K + 1))


def test_intra_sort_descending() -> None:
    """3 groups with 1 chunk each, all rank 1, all same score.
    Output: 3 chunks, all score 1/(k+1), sorted stably by chunk_id.
    """
    g1 = [_doc(A, score=0.5)]
    g2 = [_doc(B, score=0.5)]
    g3 = [_doc(C, score=0.5)]
    fused = intra_fusion([g1, g2, g3])
    assert len(fused) == 3
    for d in fused:
        assert math.isclose(d.score, 1.0 / (DEFAULT_RRF_K + 1))
    # All same score -> sort by chunk_id str (stable)
    ids = [str(d.chunk_id) for d in fused]
    assert ids == sorted(ids)


# ---------- Per-group weights ----------


def test_intra_per_group_weight_applied() -> None:
    """weights=[1.0, 0.0] -> variant 1 RRF contributes 0, variant 0 dominates.
    score_breakdown takes max across all sightings (per FastGPT semantics),
    so 0.99 wins over 0.9 even though its RRF contribution is 0.
    """
    a_in_g0 = _doc(A, score=0.9)
    a_in_g1 = _doc(A, score=0.99)  # higher raw but weight=0
    fused = intra_fusion([[a_in_g0], [a_in_g1]], weights=[1.0, 0.0])
    assert len(fused) == 1
    # RRF: only g0 contributes (g1's 0/61 = 0)
    assert math.isclose(fused[0].score, 1.0 / (DEFAULT_RRF_K + 1))
    # score_breakdown: max across all sightings -> 0.99 wins
    assert math.isclose(fused[0].score_breakdown["vector"], 0.99)


def test_intra_per_group_weight_zero_contributes_zero_score() -> None:
    """weights=[0.0, 1.0] -> a (only in g0) gets score 0; b (in both) gets full RRF.

    Per FastGPT ``datasetSearchResultConcat`` semantics, weight=0 still
    creates an entry (with score 0), does NOT filter the chunk.
    """
    a = _doc(A, score=0.5)
    b = _doc(B, score=0.5)
    fused = intra_fusion([[a, b], [b]], weights=[0.0, 1.0])
    by_id = {str(d.chunk_id): d for d in fused}
    # a: only in g0 with weight 0 -> score 0
    assert math.isclose(by_id[A].score, 0.0)
    # b: rank 1 in both groups, weights 0+1 -> 1/(k+1) from g1 only
    assert math.isclose(by_id[B].score, 1.0 / (DEFAULT_RRF_K + 1))
    # b sorts first (higher score)
    assert str(fused[0].chunk_id) == B


# ---------- score_breakdown per-source max ----------


def test_intra_score_breakdown_max_within_source() -> None:
    """Same source 'vector' in 2 groups, different scores: max wins."""
    a_low = _doc(A, score=0.5, source="vector")
    a_high = _doc(A, score=0.95, source="vector")
    fused = intra_fusion([[a_low], [a_high]])
    assert fused[0].score_breakdown == {"vector": pytest.approx(0.95)}


def test_intra_score_breakdown_distinct_sources() -> None:
    """Different sources in different groups: each kept under its key."""
    a_vec = _doc(A, score=0.9, source="vector")
    a_ft = _doc(A, score=0.7, source="fulltext")
    fused = intra_fusion([[a_vec], [a_ft]])
    assert fused[0].score_breakdown == {
        "vector": pytest.approx(0.9),
        "fulltext": pytest.approx(0.7),
    }


# ---------- rrf_k configurability ----------


def test_intra_respects_dataset_rrf_k() -> None:
    """rrf_k=30 -> 1/(30+1), not 1/(60+1)."""
    ds = Dataset(
        id=uuid.uuid4(),
        name="t",
        embed_model="m",
        embed_dim=1536,
        rrf_k=30,
    )
    hits = [_doc(A)]
    fused = intra_fusion([hits], rrf_k=ds.rrf_k)
    assert math.isclose(fused[0].score, 1.0 / (ds.rrf_k + 1))


def test_intra_rrf_k_distinct_from_default() -> None:
    """rrf_k=10 -> 1/11, distinct from default 1/61."""
    hits = [_doc(A)]
    fused_default = intra_fusion([hits])
    fused_custom = intra_fusion([hits], rrf_k=10)
    assert fused_default[0].score != fused_custom[0].score
    assert math.isclose(fused_custom[0].score, 1.0 / 11)


# ---------- Immutability ----------


def test_intra_does_not_mutate_input_list() -> None:
    """Input list reference and length unchanged after call."""
    hits = [_doc(A), _doc(B)]
    before = list(hits)
    intra_fusion([hits])
    assert hits == before  # list equality (different objects, same content)
    assert len(hits) == 2


def test_intra_does_not_mutate_input_scored_document() -> None:
    """Input ScoredDocument.score and score_breakdown unchanged."""
    a = _doc(A, score=0.42, source="vector")
    a_orig_score = a.score
    a_orig_breakdown = dict(a.score_breakdown)
    intra_fusion([[a], [_doc(A, score=0.9, source="vector")]])
    # Original input score unchanged
    assert a.score == a_orig_score
    assert a.score_breakdown == a_orig_breakdown


def test_intra_returns_new_scored_document_instances() -> None:
    """Returned list contains NEW ScoredDocument, not the input refs."""
    a = _doc(A)
    fused = intra_fusion([[a]])
    assert fused[0] is not a  # new instance via model_copy
    # But text/chunk_id preserved
    assert fused[0].chunk_id == a.chunk_id
    assert fused[0].text == a.text
