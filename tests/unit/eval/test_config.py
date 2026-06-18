"""Unit tests for ``rag.eval.config``."""

from __future__ import annotations

from pathlib import Path

from rag.eval.config import EvalConfig, GateThresholds


def test_eval_config_defaults() -> None:
    cfg = EvalConfig()
    assert cfg.retrieval_metrics == ["recall", "precision", "hit_rate", "mrr", "ndcg"]
    assert cfg.gen_backend == "naive"
    assert cfg.gen_metrics == [
        "faithfulness",
        "answer_relevance",
        "context_precision",
    ]
    assert cfg.concurrency == 4
    assert cfg.artifact_dir is None
    assert cfg.baseline_path is None


def test_eval_config_gen_backend_literal_validation() -> None:
    cfg = EvalConfig(gen_backend="ragas")
    assert cfg.gen_backend == "ragas"


def test_eval_config_frozen() -> None:
    """Config 是 frozen, 防止运行中意外修改。"""
    cfg = EvalConfig()
    try:
        cfg.concurrency = 10  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except Exception:
        pass


def test_gate_thresholds_all_none_by_default() -> None:
    gate = GateThresholds()
    assert gate.min_recall_at_k is None
    assert gate.min_faithfulness is None


def test_eval_config_with_gate() -> None:
    cfg = EvalConfig(
        gate=GateThresholds(min_recall_at_k=0.7, min_faithfulness=0.8),
        baseline_path=Path("/tmp/baseline.json"),
        max_regression_pct=5.0,
    )
    assert cfg.gate.min_recall_at_k == 0.7
    assert cfg.baseline_path == Path("/tmp/baseline.json")
    assert cfg.max_regression_pct == 5.0
