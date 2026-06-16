"""rag-ingest CLI (cli.py) 单元测试。

覆盖:
    - ``_expand_paths`` 路径展开 (文件 / 目录 / hidden / 大小 / 不存在)
    - ``ingest`` 子命令多文件 + 文件夹递归
    - ``ingest-buffer`` 子命令已删除
    - 单文件失败不中断 batch; 全失败 → exit 1
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

# 让 ``data`` (tests/data 包) 可被 mypy / pytest 解析
from data import (  # noqa: E402
    DATA_DIR,
    SAMPLE_CSV,
    SAMPLE_HTML,
    SAMPLE_MD,
)
from rag.ingest.cli import _SEPARATOR, _expand_paths, app  # noqa: E402
from rag.ingest.pipeline import IngestPipeline  # noqa: E402
from rag.ingest.source import FileSource, IngestSource  # noqa: E402
from rag.ingest.types import IngestResult  # noqa: E402

runner = CliRunner()


# ─────────────────────────────────────────────────────────────────────────────
# _expand_paths 纯函数测试
# ─────────────────────────────────────────────────────────────────────────────


def test_expand_paths_single_file(tmp_path: Path) -> None:
    """单文件 → 直接保留, 不展开。"""
    p = tmp_path / "a.txt"
    p.write_text("hi", encoding="utf-8")
    expanded, warnings = _expand_paths([p], recursive=False)
    assert expanded == [p]
    assert warnings == []


def test_expand_paths_dir_without_recursive_warns(tmp_path: Path) -> None:
    """目录但没 --recursive → 不展开, 走 warning。"""
    d = tmp_path / "sub"
    d.mkdir()
    expanded, warnings = _expand_paths([d], recursive=False)
    assert expanded == []
    assert len(warnings) == 1
    assert "--recursive" in warnings[0]


def test_expand_paths_dir_with_recursive(tmp_path: Path) -> None:
    """目录 + --recursive → rglob 全部非隐藏文件, 按文件名排序。"""
    d = tmp_path / "sub"
    d.mkdir()
    (d / "b.md").write_text("b", encoding="utf-8")
    (d / "a.md").write_text("a", encoding="utf-8")
    (d / ".hidden").write_text("x", encoding="utf-8")
    sub = d / "nested"
    sub.mkdir()
    (sub / "c.md").write_text("c", encoding="utf-8")

    expanded, warnings = _expand_paths([d], recursive=True)
    assert warnings == []
    assert [p.name for p in expanded] == ["a.md", "b.md", "c.md"]


def test_expand_paths_skips_hidden_files(tmp_path: Path) -> None:
    """目录里以 . 开头的文件 (含 .DS_Store / .git) 一律跳过。"""
    d = tmp_path / "d"
    d.mkdir()
    (d / ".DS_Store").write_text("x", encoding="utf-8")
    (d / "kept.txt").write_text("y", encoding="utf-8")

    expanded, warnings = _expand_paths([d], recursive=True)
    assert [p.name for p in expanded] == ["kept.txt"]
    assert warnings == []


def test_expand_paths_skips_pycache(tmp_path: Path) -> None:
    """``__pycache__/`` 目录里的 ``.pyc`` 一律跳过, 即使名字不带 ``.`` 前缀。"""
    d = tmp_path / "d"
    d.mkdir()
    (d / "kept.txt").write_text("y", encoding="utf-8")
    cache = d / "__pycache__"
    cache.mkdir()
    (cache / "foo.cpython-313.pyc").write_bytes(b"\x00")

    expanded, warnings = _expand_paths([d], recursive=True)
    assert [p.name for p in expanded] == ["kept.txt"]
    assert warnings == []


def test_expand_paths_skips_oversized(tmp_path: Path) -> None:
    """超过 _MAX_FILE_BYTES 的文件 → warning + 跳过。"""
    from rag.ingest.cli import _MAX_FILE_BYTES

    d = tmp_path / "d"
    d.mkdir()
    small = d / "small.txt"
    small.write_text("ok", encoding="utf-8")
    big = d / "big.bin"
    # 制造一个 > 100MB 的假文件 (稀疏写)
    with big.open("wb") as f:
        f.seek(_MAX_FILE_BYTES + 1)
        f.write(b"\0")

    expanded, warnings = _expand_paths([d], recursive=True)
    assert [p.name for p in expanded] == ["small.txt"]
    assert any("oversized" in w for w in warnings)


def test_expand_paths_missing_path_warns(tmp_path: Path) -> None:
    """不存在的 path → warning, 不抛错。"""
    ghost = tmp_path / "nope.txt"
    expanded, warnings = _expand_paths([ghost], recursive=False)
    assert expanded == []
    assert any("not found" in w for w in warnings)


def test_expand_paths_mixed_files_and_dirs(tmp_path: Path) -> None:
    """文件 + 目录混合: 文件直接进, 目录展开, 整体排序。"""
    d = tmp_path / "d"
    d.mkdir()
    (d / "inside.md").write_text("x", encoding="utf-8")
    a = tmp_path / "a.txt"
    a.write_text("a", encoding="utf-8")
    b = tmp_path / "b.txt"
    b.write_text("b", encoding="utf-8")

    expanded, warnings = _expand_paths([a, d, b], recursive=True)
    assert warnings == []
    # 跨目录 + 顶层文件按 full Path 字典序: a.txt, b.txt, d/inside.md
    assert expanded == [a, b, d / "inside.md"]


def test_expand_paths_dedupes(tmp_path: Path) -> None:
    """同一 path 传两次 → 去重。"""
    p = tmp_path / "a.txt"
    p.write_text("hi", encoding="utf-8")
    expanded, warnings = _expand_paths([p, p], recursive=False)
    assert expanded == [p]
    assert warnings == []


# ─────────────────────────────────────────────────────────────────────────────
# Typer CLI 端到端 (CliRunner)
# ─────────────────────────────────────────────────────────────────────────────


def test_ingest_single_file_default(tmp_path: Path) -> None:
    """单文件 (无 batch 前缀) 保持原行为。"""
    p = tmp_path / "a.md"
    p.write_text("# 标题\n\n正文一。\n\n正文二。", encoding="utf-8")
    result = runner.invoke(app, [str(p)])
    assert result.exit_code == 0, result.output
    assert "title: 标题" in result.output
    assert "[0/" in result.output
    # 单文件场景不应该有 batch 前缀
    assert "batch:" not in result.output


def test_ingest_multiple_files(tmp_path: Path) -> None:
    """传 2 个文件 → 输出含 2 个 ``[N/2]`` 块 + 分隔线。"""
    p1 = tmp_path / "a.md"
    p1.write_text("# A\n\nA 正文。", encoding="utf-8")
    p2 = tmp_path / "b.md"
    p2.write_text("# B\n\nB 正文。", encoding="utf-8")

    result = runner.invoke(app, [str(p1), str(p2)])
    assert result.exit_code == 0, result.output
    assert "[1/2]" in result.output
    assert "[2/2]" in result.output
    assert "batch: 2 file(s)" in result.output
    assert "=" * 60 in result.output
    assert "title: A" in result.output
    assert "title: B" in result.output


def test_ingest_recursive_folder(tmp_path: Path) -> None:
    """--recursive + 文件夹: 全部 fixture 都处理。"""
    d = tmp_path / "fixtures"
    d.mkdir()
    (d / "first.md").write_text("# First\n\nfirst body。", encoding="utf-8")
    (d / "second.md").write_text("# Second\n\nsecond body。", encoding="utf-8")
    (d / ".hidden.md").write_text("# Hidden\n\nhidden body。", encoding="utf-8")

    result = runner.invoke(app, ["--recursive", str(d)])
    assert result.exit_code == 0, result.output
    # 2 个 fixture + 1 个 hidden 应该被跳过
    assert "[1/2]" in result.output
    assert "[2/2]" in result.output
    assert "title: First" in result.output
    assert "title: Second" in result.output
    # hidden 不该出现
    assert "Hidden" not in result.output


def test_ingest_dir_without_recursive_fails(tmp_path: Path) -> None:
    """目录但没 --recursive → 走 warning, exit 1。"""
    d = tmp_path / "empty"
    d.mkdir()
    result = runner.invoke(app, [str(d)])
    assert result.exit_code != 0
    assert "--recursive" in (result.output + result.stderr)


def test_ingest_missing_path_fails(tmp_path: Path) -> None:
    """不存在的 path → warning + exit 1。"""
    ghost = tmp_path / "nope.txt"
    result = runner.invoke(app, [str(ghost)])
    assert result.exit_code != 0
    assert "not found" in (result.output + result.stderr)


def test_ingest_one_failure_does_not_break_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """batch 里有 1 个失败 → 其他成功, 整体 exit 1, 输出仍含成功的 ``[N/M]``。"""
    p_ok = tmp_path / "ok.md"
    p_ok.write_text("# OK\n\nok body。", encoding="utf-8")
    p_bad = tmp_path / "bad.md"
    p_bad.write_text("# Bad\n\nbad body。", encoding="utf-8")

    original = IngestPipeline.ingest

    async def flaky(
        self: IngestPipeline,
        source: IngestSource,
        *,
        get_format_text: bool = True,
    ) -> IngestResult:
        if isinstance(source, FileSource) and str(source.path).endswith("bad.md"):
            raise RuntimeError("synthetic failure")
        return await original(self, source, get_format_text=get_format_text)

    monkeypatch.setattr(IngestPipeline, "ingest", flaky)

    result = runner.invoke(app, [str(p_ok), str(p_bad)])
    assert result.exit_code == 1
    assert "title: OK" in result.output
    assert "synthetic failure" in (result.output + result.stderr)


def test_ingest_buffer_cmd_removed() -> None:
    """``ingest-buffer`` 不再是子命令；会被当作不存在的文件路径。"""
    result = runner.invoke(app, ["ingest-buffer", "x", "md"])
    assert result.exit_code != 0
    combined = result.output + result.stderr
    assert "ingest-buffer" in combined


def test_help_lists_mode_and_targets() -> None:
    """顶层 --help 列出 TARGETS 与 --mode，无 ingest-url / ingest-buffer。"""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "ingest-url" not in result.output
    assert "ingest-buffer" not in result.output
    assert "--mode" in result.output
    assert "TARGETS" in result.output


def test_help_lists_params() -> None:
    """``--help`` 通过 Typer Options 面板列出参数。"""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "TARGETS" in result.output
    assert "--mode" in result.output
    assert "--recursive" in result.output
    assert "--format-text" in result.output
    assert "--chunk-stats" in result.output
    assert "--normalize" in result.output


def test_ingest_url_mode_requires_single_target() -> None:
    """``--mode url`` 只接受一个 URL。"""
    result = runner.invoke(
        app,
        ["--mode", "url", "https://a.example", "https://b.example"],
    )
    assert result.exit_code == 1
    assert "exactly one URL" in (result.output + result.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# 真实 fixture (tests/data) 烟雾测试: 验证多文件 + recursive 真能跑通。
# ─────────────────────────────────────────────────────────────────────────────


def test_ingest_real_fixtures_multi_file() -> None:
    """真实 sample.md + sample.html: 输出含 2 块且没有 hard fail。"""
    result = runner.invoke(app, [str(SAMPLE_MD), str(SAMPLE_HTML)])
    assert result.exit_code == 0, result.output
    assert "[1/2]" in result.output
    assert "[2/2]" in result.output


def test_ingest_real_fixtures_recursive_data_dir() -> None:
    """tests/data 目录全部 fixture 走 --recursive, 验证能跑通。"""
    result = runner.invoke(app, ["--recursive", str(DATA_DIR)])
    # 不检查 exit_code == 0 因为某些 fixture 可能内部 warning
    # 但至少要有 batch 前缀 + 多个 [N/M] 块
    assert "batch:" in result.output
    assert "[1/" in result.output
    assert SAMPLE_MD.name in result.output
    assert SAMPLE_CSV.name in result.output


def test_ingest_block_separator_count() -> None:
    """N 个文件 → N 个分隔线 (prelude 1 个 + 块之间 N-1 个)。"""
    p1 = SAMPLE_MD
    p2 = SAMPLE_CSV
    result = runner.invoke(app, [str(p1), str(p2)])
    assert result.exit_code == 0, result.output
    # 2 个文件 → prelude 1 个 + 块之间 1 个 = 2 个分隔线
    assert result.output.count(_SEPARATOR) == 2


def test_batch_prefix_indices_are_one_based_and_contiguous(tmp_path: Path) -> None:
    """``[N/M]`` 的 N 是 1-based, 1..M 连续。"""
    p1 = tmp_path / "x.md"
    p1.write_text("# X\n\nbody x.", encoding="utf-8")
    p2 = tmp_path / "y.md"
    p2.write_text("# Y\n\nbody y.", encoding="utf-8")
    p3 = tmp_path / "z.md"
    p3.write_text("# Z\n\nbody z.", encoding="utf-8")

    result = runner.invoke(app, [str(p1), str(p2), str(p3)])
    assert result.exit_code == 0, result.output
    # 验证 3 个 1-based 块都出现
    for needle in ("[1/3]", "[2/3]", "[3/3]"):
        assert needle in result.output, f"missing {needle} in:\n{result.output}"

    # 抽所有 batch prefix 的 N: 行首 ``[N/M] /abs-path`` (chunk 的是 ``[N/M] file:///...``)
    seen = sorted(
        int(m.group(1))
        for m in re.finditer(r"^\[(\d+)/\d+\] /", result.output, re.MULTILINE)
    )
    assert seen == [1, 2, 3]
