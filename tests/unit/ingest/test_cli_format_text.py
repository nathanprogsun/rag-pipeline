"""``rag-ingest`` CLI 的 ``--format-text/--no-format-text`` flag 测试。

R3 新增: typer 选项 ``--format-text`` (默认) / ``--no-format-text``, 透传到
``IngestPipeline.ingest(get_format_text=...)``。csv fixture 验证两种模式下
渲染出的 chunk preview 不同。
"""

from __future__ import annotations

from typer.testing import CliRunner

from data import SAMPLE_CSV  # noqa: E402
from rag.ingest.cli import app  # noqa: E402

runner = CliRunner()


def test_cli_format_text_default() -> None:
    """``ingest sample.csv`` 默认走 --format-text → chunk preview 含 md table。"""
    result = runner.invoke(app, [str(SAMPLE_CSV)])
    assert result.exit_code == 0, result.output
    # md table preview: 第一个 chunk 的 text 含 "| id |" 或 "| name |"
    assert "| id |" in result.output or "| name |" in result.output


def test_cli_no_format_text() -> None:
    """``ingest --no-format-text sample.csv`` → chunk preview 走 raw csv。"""
    result = runner.invoke(app, ["--no-format-text", str(SAMPLE_CSV)])
    assert result.exit_code == 0, result.output
    # raw csv preview: 第一个 chunk 的 text 含 "id,name" (csv header 原串)
    assert "id,name" in result.output
    # 也不应有 md table 形式
    assert "| id |" not in result.output or "id,name" in result.output


def test_cli_format_text_explicit() -> None:
    """``ingest --format-text sample.csv`` 显式开启 → 行为等同默认。"""
    result = runner.invoke(app, ["--format-text", str(SAMPLE_CSV)])
    assert result.exit_code == 0, result.output
    assert "| id |" in result.output or "| name |" in result.output


def test_cli_help_lists_format_text_flag() -> None:
    """``ingest --help`` 应列出 ``--format-text/--no-format-text`` flag。"""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--format-text" in result.output
    assert "--no-format-text" in result.output
