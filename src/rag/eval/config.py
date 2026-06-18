"""``EvalConfig`` + ``GateThresholds``: UnifiedEvalRunner 的配置面。

- ``EvalConfig``: 控制 runner 行为 (指标列表 / backend / 并发 / artifact)。
- ``GateThresholds``: 门禁阈值, 任意指标低于阈值则 gate 失败。
- ``max_regression_pct``: 与 baseline 对比, 跌超此百分比视为回归。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GateThresholds(BaseModel):
    """评估门禁阈值: 任何指标低于阈值则 gate 失败。所有字段可选。"""

    model_config = ConfigDict(frozen=True)

    min_recall_at_k: float | None = None
    min_precision_at_k: float | None = None
    min_hit_rate_at_k: float | None = None
    min_mrr: float | None = None
    min_ndcg_at_k: float | None = None
    min_faithfulness: float | None = None
    min_answer_relevance: float | None = None
    min_context_precision: float | None = None


class EvalConfig(BaseModel):
    """UnifiedEvalRunner 配置。

    Args:
        retrieval_metrics: 检索侧指标模板列表 (无 ``@k`` 后缀, k 由 record 填充)。
        default_k: record 未指定 k 时使用。
        gen_backend: 生成侧 backend, ``naive`` / ``ragas`` / ``skip``。
        gen_metrics: 生成侧指标模板列表。
        gate: 门禁阈值。
        baseline_path: 上一次跑出的 ``UnifiedEvalSummary`` 路径, 启用 baseline diff。
        max_regression_pct: 比 baseline 跌超此百分比视为回归。
        concurrency: pipeline.ainvoke 并发上限。
        llm_concurrency: 全局 LLM 调用并发上限 (独立于 pipeline 数)。
        artifact_dir: per-query trace 落盘目录, None 表示不落盘。
    """

    model_config = ConfigDict(frozen=True)

    retrieval_metrics: list[str] = Field(
        default_factory=lambda: ["recall", "precision", "hit_rate", "mrr", "ndcg"]
    )
    default_k: int = 10

    gen_backend: Literal["naive", "ragas", "skip"] = "naive"
    gen_metrics: list[str] = Field(
        default_factory=lambda: [
            "faithfulness",
            "answer_relevance",
            "context_precision",
        ]
    )

    gate: GateThresholds = GateThresholds()
    baseline_path: Path | None = None
    max_regression_pct: float | None = None

    concurrency: int = 4
    llm_concurrency: int = 8
    artifact_dir: Path | None = None
