"""WRRF (Weighted Reciprocal Rank Fusion) 跨 query variant 融合。

公式: ``score(c) = Σ_g w_g / (rrf_k + rank_g(c))``, 其中 ``rank_g(c)``
为 ``c`` 在 group ``g`` 内的 1-based 局部 rank。重复 ``chunk_id`` 的
``score`` 累加, ``score_breakdown[source]`` 取 max 合并。不修改入参
(通过 ``model_copy`` 输出新对象)。
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from rag.domain.document import ScoredDocument

DEFAULT_RRF_K: int = 60  # Cormack 2009 default; 可由 Dataset.rrf_k 覆盖


def intra_fusion(
    query_groups: list[list[ScoredDocument]],
    weights: list[float] | None = None,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[ScoredDocument]:
    """对多路 query variant 做 WRRF 融合。

    - ``query_groups[g]`` 为单一 query variant 的合并检索结果 (上游已
      合并 vector + fulltext)。
    - ``weights[g]`` 为 per-variant 信任权重, 默认 uniform 1.0。
    - 同 ``chunk_id`` 在不同 group 间按 RRF 累加。
    - ``score_breakdown[source]`` 在重复时取 max 合并。
    - 不修改入参, 返回新对象。

    Args:
        query_groups: N 路 query variant 检索结果。
        weights: per-variant 权重, 长度需与 ``query_groups`` 一致, 默认 uniform。
        rrf_k: RRF k 常数, 默认 60。

    Returns:
        按 RRF score 降序排列的 ``ScoredDocument`` 列表, ``score_breakdown``
        经 per-source max 合并保留。

    Raises:
        ValueError: ``weights`` 长度与 ``query_groups`` 不一致。
    """
    if not query_groups or not any(query_groups):
        return []

    if weights is None:
        weights = [1.0] * len(query_groups)
    if len(weights) != len(query_groups):
        msg = (
            f"weights length {len(weights)} != query_groups length {len(query_groups)}"
        )
        raise ValueError(msg)

    accum: dict[uuid.UUID, _Accumulator] = {}
    for g_idx, group in enumerate(query_groups):
        w_g = weights[g_idx]
        for rank, doc in enumerate(group, start=1):
            rrf_contribution = w_g / (rrf_k + rank)
            existing = accum.get(doc.chunk_id)
            if existing is None:
                new_breakdown: dict[str, float] = dict(doc.score_breakdown)
                new_breakdown[doc.source] = max(
                    new_breakdown.get(doc.source, -math.inf),
                    doc.score,
                )
                accum[doc.chunk_id] = _Accumulator(
                    doc=doc, rrf_score=rrf_contribution, breakdown=new_breakdown
                )
            else:
                existing.rrf_score += rrf_contribution
                existing.breakdown[doc.source] = max(
                    existing.breakdown.get(doc.source, -math.inf),
                    doc.score,
                )

    results: list[ScoredDocument] = [
        acc.doc.model_copy(
            update={"score": acc.rrf_score, "score_breakdown": acc.breakdown}
        )
        for acc in accum.values()
    ]
    results.sort(key=lambda d: (-d.score, str(d.chunk_id)))
    return results


@dataclass
class _Accumulator:
    """per-chunk 融合状态, 不对外暴露。"""

    doc: ScoredDocument
    rrf_score: float
    breakdown: dict[str, float]
