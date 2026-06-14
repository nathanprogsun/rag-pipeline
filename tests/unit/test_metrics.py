"""Unit tests for ``rag.eval.metrics`` (5h).

Tests cover:
- recall_at_k, precision_at_k, hit_rate_at_k, mrr, ndcg_at_k
- aggregate_metric
- Edge cases: empty inputs, perfect hit, no hit, k > len(retrieved)
"""

from __future__ import annotations

import pytest

from rag.eval.metrics import (
    aggregate_metric,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

A = "chunk-a"
B = "chunk-b"
C = "chunk-c"
D = "chunk-d"


# ---------- recall_at_k ----------


def test_recall_perfect_hit() -> None:
    """All ground truth in top-k → recall=1.0"""
    retrieved = [A, B, C]
    gt = {A, B, C}
    assert recall_at_k(retrieved, gt, k=3) == 1.0


def test_recall_partial_hit() -> None:
    """2/3 ground truth in top-k → recall=2/3"""
    retrieved = [A, "X", B]  # C missing
    gt = {A, B, C}
    assert recall_at_k(retrieved, gt, k=3) == pytest.approx(2 / 3)


def test_recall_no_hit() -> None:
    retrieved = ["X", "Y", "Z"]
    gt = {A, B}
    assert recall_at_k(retrieved, gt, k=3) == 0.0


def test_recall_k_smaller_than_gt() -> None:
    """k=1, ground_truth has 3 → recall=1/3."""
    retrieved = [A, "X", "Y"]
    gt = {A, B, C}
    assert recall_at_k(retrieved, gt, k=1) == pytest.approx(1 / 3)


def test_recall_empty_ground_truth() -> None:
    assert recall_at_k([A, B], set(), k=5) == 0.0


def test_recall_k_zero() -> None:
    assert recall_at_k([A, B], {A}, k=0) == 0.0


def test_recall_k_larger_than_retrieved() -> None:
    """k > len(retrieved): only score what we have."""
    retrieved = [A]
    gt = {A, B, C}
    assert recall_at_k(retrieved, gt, k=10) == pytest.approx(1 / 3)


# ---------- precision_at_k ----------


def test_precision_perfect() -> None:
    """Top-k all hit → precision=1.0"""
    retrieved = [A, B, C]
    gt = {A, B, C, D}
    assert precision_at_k(retrieved, gt, k=3) == 1.0


def test_precision_partial() -> None:
    """1/3 of top-k is correct → precision=1/3"""
    retrieved = [A, "X", "Y"]
    gt = {A}
    assert precision_at_k(retrieved, gt, k=3) == pytest.approx(1 / 3)


def test_precision_k_larger_than_retrieved() -> None:
    """k > len(retrieved): denominator = len(retrieved)"""
    retrieved = [A, B]
    gt = {A, B}
    # k=5 but only 2 retrieved → 2/2 = 1.0 (perfect in what we returned)
    assert precision_at_k(retrieved, gt, k=5) == 1.0


def test_precision_empty_retrieved() -> None:
    assert precision_at_k([], {A}, k=5) == 0.0


def test_precision_k_zero() -> None:
    assert precision_at_k([A, B], {A}, k=0) == 0.0


# ---------- hit_rate_at_k ----------


def test_hit_rate_any_overlap() -> None:
    retrieved = [A, "X", "Y"]
    gt = {A}
    assert hit_rate_at_k(retrieved, gt, k=3) == 1.0


def test_hit_rate_no_overlap() -> None:
    retrieved = ["X", "Y"]
    gt = {A}
    assert hit_rate_at_k(retrieved, gt, k=2) == 0.0


def test_hit_rate_at_smaller_k() -> None:
    """Hit at rank 3, but k=2 → no hit (k truncates)."""
    retrieved = ["X", "Y", A]
    gt = {A}
    assert hit_rate_at_k(retrieved, gt, k=2) == 0.0
    assert hit_rate_at_k(retrieved, gt, k=3) == 1.0


def test_hit_rate_empty() -> None:
    assert hit_rate_at_k([], {A}, k=5) == 0.0
    assert hit_rate_at_k([A], set(), k=5) == 0.0


# ---------- mrr ----------


def test_mrr_first_position() -> None:
    retrieved = [A, "X", "Y"]
    gt = {A}
    assert mrr(retrieved, gt) == 1.0


def test_mrr_second_position() -> None:
    retrieved = ["X", A, "Y"]
    gt = {A}
    assert mrr(retrieved, gt) == 0.5


def test_mrr_third_position() -> None:
    retrieved = ["X", "Y", A]
    gt = {A}
    assert mrr(retrieved, gt) == pytest.approx(1 / 3)


def test_mrr_multiple_hits_takes_first() -> None:
    """MRR uses rank of FIRST hit, not best."""
    retrieved = ["X", A, B]
    gt = {A, B}
    # A is at rank 2 (first hit) → 1/2 = 0.5
    assert mrr(retrieved, gt) == 0.5


def test_mrr_no_hit() -> None:
    assert mrr([A, B], {"X", "Y"}) == 0.0
    assert mrr([], {A}) == 0.0
    assert mrr([A], set()) == 0.0


# ---------- ndcg_at_k ----------


def test_ndcg_perfect() -> None:
    """All hits at top → NDCG=1.0"""
    retrieved = [A, B]
    gt = {A, B}
    assert ndcg_at_k(retrieved, gt, k=2) == pytest.approx(1.0)


def test_ndcg_partial() -> None:
    """1 hit at rank 2 (k=3) → DCG = 1/log2(3) ≈ 0.631, IDCG = 1.0 (1 hit at rank 1)"""
    import math

    retrieved = ["X", A, "Y"]
    gt = {A}
    expected = 1.0 / math.log2(3)  # rank 2 → log2(2+1) = log2(3) ≈ 1.585
    assert ndcg_at_k(retrieved, gt, k=3) == pytest.approx(expected)


def test_ndcg_no_hit() -> None:
    assert ndcg_at_k(["X", "Y"], {A}, k=2) == 0.0


def test_ndcg_empty_ground_truth() -> None:
    assert ndcg_at_k([A, B], set(), k=2) == 0.0


def test_ndcg_k_larger_than_retrieved() -> None:
    """k > len: IDCG only counts what we'd see with len(retrieved)."""
    retrieved = [A, B]
    gt = {A, B}
    # k=5 but only 2 retrieved → perfect = 1.0 (both at top)
    assert ndcg_at_k(retrieved, gt, k=5) == 1.0


def test_ndcg_idcg_caps_at_min_k_gt() -> None:
    """IDCG uses min(k, |gt|) hits."""
    import math

    retrieved = ["X", A, "Y"]
    gt = {A, B}
    # k=3, |gt|=2 → IDCG = 1/log2(2) + 1/log2(3) = 1 + 0.631 = 1.631
    # DCG = 1/log2(3) (A at rank 2) ≈ 0.631
    dcg = 1.0 / math.log2(3)
    idcg = 1.0 / math.log2(2) + 1.0 / math.log2(3)
    expected = dcg / idcg
    assert ndcg_at_k(retrieved, gt, k=3) == pytest.approx(expected)


# ---------- aggregate_metric ----------


def test_aggregate_empty() -> None:
    agg = aggregate_metric([])
    assert agg == {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "median": 0.0, "count": 0}


def test_aggregate_single_value() -> None:
    agg = aggregate_metric([0.5])
    assert agg["mean"] == 0.5
    assert agg["std"] == 0.0
    assert agg["min"] == 0.5
    assert agg["max"] == 0.5
    assert agg["median"] == 0.5
    assert agg["count"] == 1


def test_aggregate_multiple_values() -> None:
    agg = aggregate_metric([0.2, 0.4, 0.6, 0.8])
    assert agg["count"] == 4
    assert agg["mean"] == pytest.approx(0.5)
    assert agg["min"] == 0.2
    assert agg["max"] == 0.8
    # median of [0.2, 0.4, 0.6, 0.8] = (0.4 + 0.6) / 2 = 0.5
    assert agg["median"] == pytest.approx(0.5)


def test_aggregate_median_odd_count() -> None:
    agg = aggregate_metric([0.1, 0.5, 0.9])
    assert agg["median"] == 0.5  # middle value
    assert agg["count"] == 3


def test_aggregate_std() -> None:
    # mean = (0+0+1+2)/4 = 0.75
    # variance = ((0-.75)² + (0-.75)² + (1-.75)² + (2-.75)²) / 4
    #           = (0.5625 + 0.5625 + 0.0625 + 1.5625) / 4 = 0.6875
    # std = sqrt(0.6875) ≈ 0.829
    agg = aggregate_metric([0.0, 0.0, 1.0, 2.0])
    assert agg["std"] == pytest.approx(0.6875 ** 0.5)


# ---------- _to_set (internal) is exercised via the public APIs above ----------