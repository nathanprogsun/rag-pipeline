"""rag-ingest CLI (cli.py) 单元测试。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from data import (
    DATA_DIR,
    SAMPLE_CSV,
    SAMPLE_HTML,
    SAMPLE_MD,
)
from rag.ingest.cli import _SEPARATOR, app
from rag.ingest.pipeline import IngestPipeline, expand_paths

runner = CliRunner()


# ─────────────────────────────────────────────────────────────────────────────
# expand_paths 纯函数测试 (pipeline 层)
# ─────────────────────────────────────────────────────────────────────────────


def test_expand_paths_single_file(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hi", encoding="utf-8")
    expanded, warnings = expand_paths([p])
    assert expanded == [p]
    assert warnings == []


def test_expand_paths_dir_recursive(tmp_path: Path) -> None:
    d = tmp_path / "sub"
    d.mkdir()
    (d / "b.md").write_text("b", encoding="utf-8")
    (d / "a.md").write_text("a", encoding="utf-8")
    (d / ".hidden").write_text("x", encoding="utf-8")
    sub = d / "nested"
    sub.mkdir()
    (sub / "c.md").write_text("c", encoding="utf-8")

    expanded, warnings = expand_paths([d])
    assert warnings == []
    assert [p.name for p in expanded] == ["a.md", "b.md", "c.md"]


def test_expand_paths_skips_hidden_files(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / ".DS_Store").write_text("x", encoding="utf-8")
    (d / "kept.txt").write_text("y", encoding="utf-8")

    expanded, warnings = expand_paths([d])
    assert [p.name for p in expanded] == ["kept.txt"]
    assert warnings == []


def test_expand_paths_skips_pycache(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "kept.txt").write_text("y", encoding="utf-8")
    cache = d / "__pycache__"
    cache.mkdir()
    (cache / "foo.cpython-313.pyc").write_bytes(b"\x00")

    expanded, warnings = expand_paths([d])
    assert [p.name for p in expanded] == ["kept.txt"]
    assert warnings == []


def test_expand_paths_skips_oversized(tmp_path: Path) -> None:
    from rag.ingest.pipeline import _MAX_FILE_BYTES

    d = tmp_path / "d"
    d.mkdir()
    small = d / "small.txt"
    small.write_text("ok", encoding="utf-8")
    big = d / "big.bin"
    with big.open("wb") as f:
        f.seek(_MAX_FILE_BYTES + 1)
        f.write(b"\0")

    expanded, warnings = expand_paths([d])
    assert [p.name for p in expanded] == ["small.txt"]
    assert any("oversized" in w for w in warnings)


def test_expand_paths_missing_path_warns(tmp_path: Path) -> None:
    ghost = tmp_path / "nope.txt"
    expanded, warnings = expand_paths([ghost])
    assert expanded == []
    assert any("not found" in w for w in warnings)


def test_expand_paths_mixed_files_and_dirs(tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "inside.md").write_text("x", encoding="utf-8")
    a = tmp_path / "a.txt"
    a.write_text("a", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("b", encoding="utf-8")

    expanded, warnings = expand_paths([a, d, b])
    assert warnings == []
    assert expanded == [a, b, d / "inside.md"]


def test_expand_paths_dedupes(tmp_path: Path) -> None:
    p = tmp_path / "a.txt"
    p.write_text("hi", encoding="utf-8")
    expanded, warnings = expand_paths([p, p])
    assert expanded == [p]
    assert warnings == []


# ─────────────────────────────────────────────────────────────────────────────
# Typer CLI 端到端
# ─────────────────────────────────────────────────────────────────────────────


def test_ingest_single_file_default(tmp_path: Path) -> None:
    p = tmp_path / "a.md"
    p.write_text("# 标题\n\n正文一。\n\n正文二。", encoding="utf-8")
    result = runner.invoke(app, [str(p)])
    assert result.exit_code == 0, result.output
    assert "title: 标题" in result.output
    assert "[0/" in result.output
    assert "batch:" not in result.output


def test_ingest_multiple_files(tmp_path: Path) -> None:
    p1 = tmp_path / "a.md"
    p1.write_text("# A\n\nA 正文。", encoding="utf-8")
    p2 = tmp_path / "b.md"
    p2.write_text("# B\n\nB 正文。", encoding="utf-8")

    result = runner.invoke(app, [str(p1), str(p2)])
    assert result.exit_code == 0, result.output
    assert "[1/2]" in result.output
    assert "[2/2]" in result.output
    assert "batch: 2 file(s)" in result.output
    assert "title: A" in result.output
    assert "title: B" in result.output


def test_ingest_recursive_folder(tmp_path: Path) -> None:
    d = tmp_path / "fixtures"
    d.mkdir()
    (d / "first.md").write_text("# First\n\nfirst body。", encoding="utf-8")
    (d / "second.md").write_text("# Second\n\nsecond body。", encoding="utf-8")
    (d / ".hidden.md").write_text("# Hidden\n\nhidden body。", encoding="utf-8")

    result = runner.invoke(app, [str(d)])
    assert result.exit_code == 0, result.output
    assert "[1/2]" in result.output
    assert "[2/2]" in result.output
    assert "title: First" in result.output
    assert "title: Second" in result.output
    assert "Hidden" not in result.output


def test_ingest_empty_dir_warns(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    result = runner.invoke(app, [str(d)])
    assert result.exit_code != 0


def test_ingest_missing_path_fails(tmp_path: Path) -> None:
    ghost = tmp_path / "nope.txt"
    result = runner.invoke(app, [str(ghost)])
    assert result.exit_code != 0
    assert "not found" in (result.output + result.stderr)


def test_ingest_one_failure_does_not_break_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p_ok = tmp_path / "ok.md"
    p_ok.write_text("# OK\n\nok body。", encoding="utf-8")
    p_bad = tmp_path / "bad.md"
    p_bad.write_text("# Bad\n\nbad body。", encoding="utf-8")

    original = IngestPipeline._process

    async def flaky_process(
        self: IngestPipeline, file: Path, *, dataset_id: object = None
    ) -> object:
        if file.name == "bad.md":
            raise RuntimeError("synthetic failure")
        return await original(self, file)

    monkeypatch.setattr(IngestPipeline, "_process", flaky_process)

    result = runner.invoke(app, [str(p_ok), str(p_bad)])
    assert result.exit_code == 1
    assert "title: OK" in result.output
    assert "synthetic failure" in (result.output + result.stderr)


def test_ingest_buffer_cmd_removed() -> None:
    result = runner.invoke(app, ["ingest-buffer", "x", "md"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "ingest-buffer" in combined


def test_help_lists_targets_and_dataset() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "ingest-url" not in result.output
    assert "ingest-buffer" not in result.output
    assert "TARGETS" in result.output
    assert "--dataset-name" in result.output


def test_help_lists_dataset_options() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--dataset-name" in result.output
    assert "--dataset-id" in result.output
    assert "--recursive" not in result.output
    assert "--normalize" not in result.output


def test_ingest_real_fixtures_multi_file() -> None:
    result = runner.invoke(app, [str(SAMPLE_MD), str(SAMPLE_HTML)])
    assert result.exit_code == 0, result.output
    assert "[1/2]" in result.output
    assert "[2/2]" in result.output


def test_ingest_real_fixtures_data_dir() -> None:
    result = runner.invoke(app, [str(DATA_DIR)])
    assert "batch:" in result.output
    assert "[1/" in result.output
    assert SAMPLE_MD.name in result.output
    assert SAMPLE_CSV.name in result.output


def test_ingest_block_separator_count() -> None:
    result = runner.invoke(app, [str(SAMPLE_MD), str(SAMPLE_CSV)])
    assert result.exit_code == 0, result.output
    assert result.output.count(_SEPARATOR) >= 1


def test_batch_prefix_indices_are_one_based_and_contiguous(tmp_path: Path) -> None:
    p1 = tmp_path / "x.md"
    p1.write_text("# X\n\nbody x.", encoding="utf-8")
    p2 = tmp_path / "y.md"
    p2.write_text("# Y\n\nbody y.", encoding="utf-8")
    p3 = tmp_path / "z.md"
    p3.write_text("# Z\n\nbody z.", encoding="utf-8")

    result = runner.invoke(app, [str(p1), str(p2), str(p3)])
    assert result.exit_code == 0, result.output
    for needle in ("[1/3]", "[2/3]", "[3/3]"):
        assert needle in result.output, f"missing {needle} in:\n{result.output}"

    seen = sorted(
        int(m.group(1))
        for m in re.finditer(
            r"^\[(\d+)/\d+\] [^:\n]+$",
            result.output,
            re.MULTILINE,
        )
    )
    assert seen == [1, 2, 3]
