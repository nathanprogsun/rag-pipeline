"""Retrieval quality metrics (5h).

Per `.agents/design/2026-06-14-rag-pipeline-delivery.md` task 18:

Pure per-query metric functions (no DB / no LLM dependencies):
- ``recall_at_k(retrieved_ids, ground_truth_ids, k) -> float``
- ``precision_at_k(retrieved_ids, ground_truth_ids, k) -> float``
- ``mrr(retrieved_ids, ground_truth_ids) -> float``
- ``ndcg_at_k(retrieved_ids, ground_truth_ids, k) -> float``
- ``hit_rate_at_k(retrieved_ids, ground_truth_ids, k) -> float`` (any overlap?)

All metrics assume:
- ``retrieved_ids``: ordered list of chunk_ids returned by pipeline
- ``ground_truth_ids``: set (or list) of expected chunk_ids for this query
- Higher is better; range [0, 1]

These are the building blocks for EvalRunner (5h). RAGAS-based
faithfulness / answer-relevancy metrics live in 5i.
"""

from __future__ import annotations

import math


def _to_set(ids: list[str] | set[str]) -> set[str]:
    """Normalize ground_truth_ids to a set for fast lookup."""
    return set(ids)


def recall_at_k(
    retrieved_ids: list[str],
    ground_truth_ids: list[str] | set[str],
    k: int,
) -> float:
    """Recall@K = |retrieved[:k] ∩ ground_truth| / |ground_truth|.

    Edge cases:
    - ground_truth empty → 0.0 (division by zero protection)
    - k <= 0 → 0.0
    """
    if k <= 0 or not ground_truth_ids:
        return 0.0
    gt = _to_set(ground_truth_ids)
    if not gt:
        return 0.0
    top_k = retrieved_ids[:k]
    hits = sum(1 for cid in top_k if cid in gt)
    return hits / len(gt)


def precision_at_k(
    retrieved_ids: list[str],
    ground_truth_ids: list[str] | set[str],
    k: int,
) -> float:
    """Precision@K = |retrieved[:k] ∩ ground_truth| / min(k, len(retrieved)).

    Edge cases:
    - k <= 0 or empty retrieved → 0.0
    - len(retrieved) < k → denominator = len(retrieved) (avoid penalizing
      pipelines that return fewer than K hits)
    """
    if k <= 0 or not retrieved_ids:
        return 0.0
    gt = _to_set(ground_truth_ids)
    top_k = retrieved_ids[:k]
    hits = sum(1 for cid in top_k if cid in gt)
    return hits / min(k, len(retrieved_ids))


def hit_rate_at_k(
    retrieved_ids: list[str],
    ground_truth_ids: list[str] | set[str],
    k: int,
) -> float:
    """Hit rate@K = 1.0 if any overlap, else 0.0.

    Binary version of recall (any hit counts).
    """
    if k <= 0 or not retrieved_ids or not ground_truth_ids:
        return 0.0
    gt = _to_set(ground_truth_ids)
    top_k = retrieved_ids[:k]
    return 1.0 if any(cid in gt for cid in top_k) else 0.0


def mrr(
    retrieved_ids: list[str],
    ground_truth_ids: list[str] | set[str],
) -> float:
    """MRR = 1/rank_of_first_correct_hit (0 if no hit).

    Rank is 1-based (first position is rank 1, so MRR is 1.0 for top-1 hit).
    """
    if not retrieved_ids or not ground_truth_ids:
        return 0.0
    gt = _to_set(ground_truth_ids)
    for rank, cid in enumerate(retrieved_ids, start=1):
        if cid in gt:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    retrieved_ids: list[str],
    ground_truth_ids: list[str] | set[str],
    k: int,
) -> float:
    """NDCG@K = DCG@K / IDCG@K (binary relevance).

    DCG@K = Σ_{i=1}^{k} rel_i / log2(i+1) where rel_i = 1 if hit else 0.
    IDCG@K = DCG for ideal ordering (all hits at top).
    Edge case: IDCG = 0 (no ground truth) → 0.0.
    """
    if k <= 0 or not retrieved_ids or not ground_truth_ids:
        return 0.0
    gt = _to_set(ground_truth_ids)
    top_k = retrieved_ids[:k]

    # DCG
    dcg = 0.0
    for i, cid in enumerate(top_k, start=1):
        if cid in gt:
            dcg += 1.0 / math.log2(i + 1)

    # IDCG: best possible DCG = sum over min(k, |gt|) hits at top positions
    ideal_hits = min(k, len(gt))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg == 0:
        return 0.0
    return dcg / idcg


# ---------- Aggregations ----------


def aggregate_metric(values: list[float]) -> dict[str, float]:
    """Aggregate a list of per-query metric values.

    Returns: ``{mean, std, min, max, median, count}``. Empty list returns zeros.
    """
    if not values:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "median": 0.0, "count": 0}
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    sorted_v = sorted(values)
    median = sorted_v[n // 2] if n % 2 == 1 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    return {
        "mean": mean,
        "std": math.sqrt(variance),
        "min": min(values),
        "max": max(values),
        "median": median,
        "count": n,
    }