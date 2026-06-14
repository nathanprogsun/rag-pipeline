"""rag.eval — retrieval quality evaluation framework (5h)."""

from rag.eval.metrics import (
    aggregate_metric,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from rag.eval.runner import EvalRecord, EvalRunner, EvalSampleResult, EvalSummary

__all__ = [
    "EvalRecord",
    "EvalRunner",
    "EvalSampleResult",
    "EvalSummary",
    "aggregate_metric",
    "hit_rate_at_k",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
]