"""rag.eval — 检索质量评估框架。"""

from rag.eval.metrics import (
    aggregate_metric,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from rag.eval.ragas_metrics import (
    answer_relevance_stub,
    context_precision_stub,
    faithfulness_stub,
)
from rag.eval.ragas_runner import (
    RagasRecord,
    RagasRunner,
    RagasSampleResult,
    RagasSummary,
)
from rag.eval.runner import EvalRecord, EvalRunner, EvalSampleResult, EvalSummary

__all__ = [
    "EvalRecord",
    "EvalRunner",
    "EvalSampleResult",
    "EvalSummary",
    "RagasRecord",
    "RagasRunner",
    "RagasSampleResult",
    "RagasSummary",
    "aggregate_metric",
    "answer_relevance_stub",
    "context_precision_stub",
    "faithfulness_stub",
    "hit_rate_at_k",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
]
