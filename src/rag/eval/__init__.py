"""rag.eval — 检索 + 生成 质量评估框架。

Public API:
    - 检索指标纯函数 (无副作用, 易测): ``recall_at_k`` / ``precision_at_k`` /
      ``hit_rate_at_k`` / ``mrr`` / ``ndcg_at_k`` / ``aggregate_metric``。
    - 统一 runner: ``UnifiedEvalRunner`` + ``UnifiedEvalSummary``。
    - 配置: ``EvalConfig`` + ``GateThresholds`` + ``GateResult``。
    - 数据: ``EvalRecord`` (单条 JSONL)。
    - Backend 抽象: ``GenMetricsBackend`` Protocol + ``NaiveBackend`` (无 LLM 启发式)
      + ``SkipBackend`` + ``get_backend`` factory。
    - 工具: ``ArtifactWriter`` (per-query 落盘) + ``evaluate_gate`` + ``compute_baseline_delta``。
"""

from rag.eval._jsonl import load_jsonl  # noqa: F401  re-exported for back-compat
from rag.eval.artifacts import ArtifactWriter
from rag.eval.backends import (
    GenMetricsBackend,
    NaiveBackend,
    SkipBackend,
    get_backend,
)
from rag.eval.config import EvalConfig, GateThresholds
from rag.eval.gate import GateResult, compute_baseline_delta, evaluate_gate
from rag.eval.metrics import (
    aggregate_metric,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)
from rag.eval.records import EvalRecord
from rag.eval.runner import UnifiedEvalRunner, UnifiedEvalSummary

__all__ = [
    "ArtifactWriter",
    "EvalConfig",
    "EvalRecord",
    "GateResult",
    "GateThresholds",
    "GenMetricsBackend",
    "NaiveBackend",
    "SkipBackend",
    "UnifiedEvalRunner",
    "UnifiedEvalSummary",
    "aggregate_metric",
    "compute_baseline_delta",
    "evaluate_gate",
    "get_backend",
    "hit_rate_at_k",
    "load_jsonl",
    "mrr",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
]
