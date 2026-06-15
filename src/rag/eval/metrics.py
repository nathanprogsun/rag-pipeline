"""检索质量指标 (无 DB / LLM 依赖的纯函数)。

提供 recall@k、precision@k、hit_rate@k、mrr、ndcg@k 及聚合函数
``aggregate_metric``。RAGAS 相关的 faithfulness / answer-relevancy
指标位于独立的 ragas_metrics 模块。
"""

from __future__ import annotations

import math


def _to_set(ids: list[str] | set[str]) -> set[str]:
    """将 ground_truth_ids 归一化为 set 便于快速查询。"""
    return set(ids)


def recall_at_k(
    retrieved_ids: list[str],
    ground_truth_ids: list[str] | set[str],
    k: int,
) -> float:
    """``Recall@K = |retrieved[:k] ∩ ground_truth| / |ground_truth|``。

    ``k <= 0`` 或 ground_truth 为空时返回 0.0。
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
    """``Precision@K = |retrieved[:k] ∩ ground_truth| / min(k, len(retrieved))``。

    ``len(retrieved) < k`` 时分母取 ``len(retrieved)``, 避免惩罚召回不足的 pipeline。
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
    """``Hit Rate@K`` = 1.0 (存在任何交集) 或 0.0, recall 的二元简化版。"""
    if k <= 0 or not retrieved_ids or not ground_truth_ids:
        return 0.0
    gt = _to_set(ground_truth_ids)
    top_k = retrieved_ids[:k]
    return 1.0 if any(cid in gt for cid in top_k) else 0.0


def mrr(
    retrieved_ids: list[str],
    ground_truth_ids: list[str] | set[str],
) -> float:
    """``MRR = 1 / 首次命中 rank`` (无命中返回 0.0, 1-based rank)。"""
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
    """``NDCG@K = DCG@K / IDCG@K``, 二元相关性。

    IDCG = 0 时返回 0.0。
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

    # IDCG: 理想排序下的 DCG, 即前 min(k, |gt|) 位全命中
    ideal_hits = min(k, len(gt))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg == 0:
        return 0.0
    return dcg / idcg


# ---------- 聚合 ----------


def aggregate_metric(values: list[float]) -> dict[str, float]:
    """聚合一组 per-query 指标值。

    Returns:
        ``{mean, std, min, max, median, count}`` 字典, 空列表返回全 0。
    """
    if not values:
        return {
            "mean": 0.0,
            "std": 0.0,
            "min": 0.0,
            "max": 0.0,
            "median": 0.0,
            "count": 0,
        }
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    sorted_v = sorted(values)
    median = (
        sorted_v[n // 2]
        if n % 2 == 1
        else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    )
    return {
        "mean": mean,
        "std": math.sqrt(variance),
        "min": min(values),
        "max": max(values),
        "median": median,
        "count": n,
    }
