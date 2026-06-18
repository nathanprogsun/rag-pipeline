"""Unit tests for ``rag.eval.gate``."""

from __future__ import annotations

import json
from pathlib import Path

from rag.eval.config import GateThresholds
from rag.eval.gate import compute_baseline_delta, evaluate_gate

# ---------- evaluate_gate ----------


def test_gate_passes_when_no_thresholds_and_no_baseline() -> None:
    result = evaluate_gate(
        aggregates={
            "recall@10": {
                "mean": 0.5,
                "std": 0.0,
                "min": 0.5,
                "max": 0.5,
                "median": 0.5,
                "count": 1,
            }
        },
        baseline_delta=None,
        thresholds=GateThresholds(),
        max_regression_pct=None,
    )
    assert result.passed is True
    assert result.failed_metrics == []
    assert result.regressions == []


def test_gate_fails_when_metric_below_threshold() -> None:
    result = evaluate_gate(
        aggregates={
            "recall@10": {
                "mean": 0.5,
                "std": 0.0,
                "min": 0.5,
                "max": 0.5,
                "median": 0.5,
                "count": 1,
            }
        },
        baseline_delta=None,
        thresholds=GateThresholds(min_recall_at_k=0.7),
        max_regression_pct=None,
    )
    assert result.passed is False
    assert any("recall@10" in f for f in result.failed_metrics)


def test_gate_passes_when_metric_meets_threshold() -> None:
    result = evaluate_gate(
        aggregates={
            "recall@10": {
                "mean": 0.8,
                "std": 0.0,
                "min": 0.8,
                "max": 0.8,
                "median": 0.8,
                "count": 1,
            }
        },
        baseline_delta=None,
        thresholds=GateThresholds(min_recall_at_k=0.7),
        max_regression_pct=None,
    )
    assert result.passed is True


def test_gate_detects_regression_against_baseline() -> None:
    result = evaluate_gate(
        aggregates={
            "recall@10": {
                "mean": 0.6,
                "std": 0.0,
                "min": 0.6,
                "max": 0.6,
                "median": 0.6,
                "count": 1,
            }
        },
        baseline_delta={"recall@10": -10.0},
        thresholds=GateThresholds(),
        max_regression_pct=5.0,
    )
    assert result.passed is False
    assert len(result.regressions) == 1
    assert "recall@10" in result.regressions[0]


def test_gate_no_regression_within_tolerance() -> None:
    result = evaluate_gate(
        aggregates={
            "recall@10": {
                "mean": 0.7,
                "std": 0.0,
                "min": 0.7,
                "max": 0.7,
                "median": 0.7,
                "count": 1,
            }
        },
        baseline_delta={"recall@10": -3.0},
        thresholds=GateThresholds(),
        max_regression_pct=5.0,
    )
    assert result.passed is True


def test_gate_skips_threshold_when_metric_absent() -> None:
    """``min_recall_at_k`` 但 aggregates 没 recall -> 不判定 (静默跳过)。"""
    result = evaluate_gate(
        aggregates={
            "mrr": {
                "mean": 0.5,
                "std": 0.0,
                "min": 0.5,
                "max": 0.5,
                "median": 0.5,
                "count": 1,
            }
        },
        baseline_delta=None,
        thresholds=GateThresholds(min_recall_at_k=0.7),
        max_regression_pct=None,
    )
    assert result.passed is True


# ---------- compute_baseline_delta ----------


def test_compute_baseline_delta_calculates_pct_change(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "sample_count": 10,
                "metric_aggregates": {
                    "recall@10": {
                        "mean": 0.6,
                        "std": 0.0,
                        "min": 0.6,
                        "max": 0.6,
                        "median": 0.6,
                        "count": 10,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    current = {
        "recall@10": {
            "mean": 0.72,
            "std": 0.0,
            "min": 0.72,
            "max": 0.72,
            "median": 0.72,
            "count": 10,
        },
    }
    delta = compute_baseline_delta(current, baseline)
    # (0.72 - 0.60) / 0.60 * 100 = 20.0%
    assert abs(delta["recall@10"] - 20.0) < 1e-6


def test_compute_baseline_delta_skips_zero_baseline() -> None:
    """baseline 为 0 时跳过 (避免除零)。"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(
            {
                "metric_aggregates": {
                    "recall@10": {
                        "mean": 0.0,
                        "std": 0.0,
                        "min": 0.0,
                        "max": 0.0,
                        "median": 0.0,
                        "count": 1,
                    }
                }
            },
            f,
        )
        baseline_path = Path(f.name)
    try:
        delta = compute_baseline_delta(
            {
                "recall@10": {
                    "mean": 0.5,
                    "std": 0.0,
                    "min": 0.5,
                    "max": 0.5,
                    "median": 0.5,
                    "count": 1,
                }
            },
            baseline_path,
        )
        assert delta == {}
    finally:
        baseline_path.unlink()


def test_compute_baseline_delta_skips_missing_metric() -> None:
    """baseline 没该 metric -> 跳过。"""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"metric_aggregates": {}}, f)
        baseline_path = Path(f.name)
    try:
        delta = compute_baseline_delta(
            {
                "recall@10": {
                    "mean": 0.5,
                    "std": 0.0,
                    "min": 0.5,
                    "max": 0.5,
                    "median": 0.5,
                    "count": 1,
                }
            },
            baseline_path,
        )
        assert delta == {}
    finally:
        baseline_path.unlink()
