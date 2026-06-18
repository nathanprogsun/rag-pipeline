"""``GateResult`` + ``evaluate_gate``: 阈值 + 回归判定。

将 ``EvalConfig.gate`` 与 ``EvalConfig.max_regression_pct`` 应用到聚合指标,
输出 ``passed: bool`` + 失败 / 回归详情, 供 CLI 决定 exit code。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import GateThresholds


@dataclass(frozen=True)
class GateResult:
    """门禁判定结果。

    Args:
        passed: True 表示所有阈值达标且无回归。
        failed_metrics: 阈值未达标项, 形如 ``["recall@10=0.65 < 0.7"]``。
        regressions: baseline diff 触发项, 形如 ``["ndcg@10: -7.2%"]``。
    """

    passed: bool
    failed_metrics: list[str] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)


def _threshold_to_metric_name(thresh_field: str) -> str:
    """``min_recall_at_k`` -> ``recall@10`` 类的字段名映射。

    实际 k 由 caller 在 metric_name 已知的情况下从 ``aggregates`` 选取,
    此处只把字段名转成"不含 k 的基准名"以便后续 caller 拼接。
    """
    return thresh_field.removeprefix("min_")  # min_recall_at_k -> recall_at_k


def evaluate_gate(
    aggregates: dict[str, dict[str, float]],
    baseline_delta: dict[str, float] | None,
    thresholds: GateThresholds,
    max_regression_pct: float | None,
) -> GateResult:
    """对当前聚合 + baseline diff 应用阈值。

    Args:
        aggregates: ``{metric_name: {mean, std, ...}}``, metric_name 已带 ``@k``。
        baseline_delta: ``{metric_name: pct_change}``, 无 baseline 时 None。
        thresholds: 阈值配置。
        max_regression_pct: 跌超此百分比视为回归; None 表示不检查回归。

    Returns:
        ``GateResult``。
    """
    failed: list[str] = []
    thresholds_dict: dict[str, float | None] = thresholds.model_dump(exclude_none=True)

    for thresh_field, thresh_value in thresholds_dict.items():
        if thresh_value is None:
            continue
        # 阈值字段 ``min_recall_at_k`` 映射到 aggregates 键 ``recall@10``:
        # 先去掉 ``_at_k`` 后缀, 再由 caller 在 record k 已知时拼接 ``@k``。
        # 此处用启发式: 在 aggregates 里找包含基准名 + ``@`` 的 key。
        base = _threshold_to_metric_name(thresh_field).replace("_at_k", "")
        candidates = [
            (k, v["mean"])
            for k, v in aggregates.items()
            if k == base or k.startswith(base + "@")
        ]
        # 多 k 时优先选第一个; 通常 dataset 用同一 k, 此处取第一个匹配即可。
        if not candidates:
            continue
        metric_name, actual = candidates[0]
        if actual < thresh_value:
            failed.append(f"{metric_name}={actual:.3f} < {thresh_value}")

    regressions: list[str] = []
    if baseline_delta and max_regression_pct is not None:
        for metric_name, delta_pct in baseline_delta.items():
            if delta_pct < -max_regression_pct:
                regressions.append(f"{metric_name}: {delta_pct:+.1f}%")

    return GateResult(
        passed=not failed and not regressions,
        failed_metrics=failed,
        regressions=regressions,
    )


def compute_baseline_delta(
    current: dict[str, dict[str, float]],
    baseline_path: Path,
) -> dict[str, float]:
    """读 baseline summary, 计算 ``current`` 相对 baseline 的百分比变化。

    Args:
        current: 当前聚合指标。
        baseline_path: 基线 ``UnifiedEvalSummary`` JSON 文件路径。

    Returns:
        ``{metric_name: pct_change}``, pct_change = (current - baseline) / baseline * 100。
        baseline 中不存在或为 0 的 metric 跳过。
    """
    baseline_doc: dict[str, Any] = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_agg = baseline_doc.get("metric_aggregates", {})

    delta: dict[str, float] = {}
    for metric_name, stats in current.items():
        base_mean = baseline_agg.get(metric_name, {}).get("mean")
        if base_mean is None or base_mean == 0:
            continue
        delta[metric_name] = (stats["mean"] - base_mean) / base_mean * 100.0
    return delta
