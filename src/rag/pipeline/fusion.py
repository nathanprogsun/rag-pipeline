"""WRRF (Weighted Reciprocal Rank Fusion) 跨 query variant 融合。

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 1:

签名::

    def intra_fusion(
        query_groups: list[list[ScoredDocument]],  # N 路 query variant
        weights: list[float] | None = None,        # per-variant trust, default uniform
        rrf_k: int = DEFAULT_RRF_K,
    ) -> list[ScoredDocument]

公式: ``score(c) = Σ_g w_g / (rrf_k + rank_g(c))``
其中 ``rank_g(c)`` 是 c 在 group g 内的局部 rank (从 enumerate(start=1) 起)。

重复 chunk_id:
- ``score`` 累加 (RRF 排序信号)
- ``score_breakdown[source] = max(prev, raw_score)`` (per-source raw 保留)

不修改入参 (model_copy 输出新对象)。
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
    """N-way WRRF over query variants.

    Contract 1 invariants:
    - query_groups[g] is one query variant's combined retrieval result
      (already merged across vector+fulltext upstream by recall layer).
    - weights[g] is per-query-variant trust weight. Default uniform 1.0.
    - Local rank via enumerate(start=1) per group; cross-group RRF sums
      on same chunk_id.
    - score_breakdown[source] = max() on duplicate sightings.
    - Never mutates inputs; returns new list with new ScoredDocument copies.

    Args:
        query_groups: N query variants, each a list of ScoredDocument.
        weights: Per-variant weights, length == len(query_groups), default uniform.
        rrf_k: RRF k constant; default 60 (Cormack 2009).

    Returns:
        ScoredDocument list sorted by RRF score descending.
        score_breakdown preserved with per-source max merge.

    Raises:
        ValueError: weights length != query_groups length.
    """
    # Empty / all-empty input short-circuit
    if not query_groups or not any(query_groups):
        return []

    # Per-group weights
    if weights is None:
        weights = [1.0] * len(query_groups)
    if len(weights) != len(query_groups):
        msg = (
            f"weights length {len(weights)} != query_groups length {len(query_groups)}"
        )
        raise ValueError(msg)

    # Per-chunk accumulator: doc + rrf_score + score_breakdown
    accum: dict[uuid.UUID, _Accumulator] = {}
    for g_idx, group in enumerate(query_groups):
        w_g = weights[g_idx]
        for rank, doc in enumerate(group, start=1):
            rrf_contribution = w_g / (rrf_k + rank)
            existing = accum.get(doc.chunk_id)
            if existing is None:
                # First sighting: copy input's breakdown, then max with own score
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

    # Build result list with model_copy (ScoredDocument frozen=False)
    results: list[ScoredDocument] = [
        acc.doc.model_copy(
            update={"score": acc.rrf_score, "score_breakdown": acc.breakdown}
        )
        for acc in accum.values()
    ]
    # Sort by RRF score desc; tiebreak by chunk_id str for stability
    results.sort(key=lambda d: (-d.score, str(d.chunk_id)))
    return results


# Internal accumulator (private; lives in this module only)


@dataclass
class _Accumulator:
    """Per-chunk fusion state. Not exposed."""

    doc: ScoredDocument
    rrf_score: float
    breakdown: dict[str, float]
