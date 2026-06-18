"""Unit tests for ``rag.eval.artifacts.ArtifactWriter``."""

from __future__ import annotations

import json
from pathlib import Path


def test_artifact_writer_creates_dirs(tmp_path: Path) -> None:
    from rag.eval.artifacts import ArtifactWriter

    ArtifactWriter(tmp_path / "artifacts")
    assert (tmp_path / "artifacts" / "per_query").exists()


def test_artifact_writer_write_query(tmp_path: Path) -> None:
    from rag.eval.artifacts import ArtifactWriter

    writer = ArtifactWriter(tmp_path / "artifacts")
    path = writer.write_query(0, {"query": "q1", "score": 0.8})

    assert path.name == "0000.json"
    assert path.exists()
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["query"] == "q1"
    assert parsed["score"] == 0.8


def test_artifact_writer_write_summary(tmp_path: Path) -> None:
    from rag.eval.artifacts import ArtifactWriter

    writer = ArtifactWriter(tmp_path / "artifacts")
    path = writer.write_summary({"sample_count": 5, "metric_aggregates": {}})

    assert path.name == "summary.json"
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert parsed["sample_count"] == 5


def test_artifact_writer_handles_unicode(tmp_path: Path) -> None:
    from rag.eval.artifacts import ArtifactWriter

    writer = ArtifactWriter(tmp_path / "artifacts")
    writer.write_query(42, {"query": "北京天气", "answer": "晴"})

    path = tmp_path / "artifacts" / "per_query" / "0042.json"
    text = path.read_text(encoding="utf-8")
    assert "北京天气" in text
    assert "晴" in text
