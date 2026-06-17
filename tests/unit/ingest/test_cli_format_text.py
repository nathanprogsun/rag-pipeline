"""``rag-ingest`` CLI 默认 format_text 烟雾测试。"""

from __future__ import annotations

from typer.testing import CliRunner

from data import SAMPLE_CSV
from rag.ingest.cli import app

runner = CliRunner()


def test_cli_format_text_default() -> None:
    """默认 format_text=True → csv chunk preview 含 md table。"""
    result = runner.invoke(app, [str(SAMPLE_CSV)])
    assert result.exit_code == 0, result.output
    assert "| id |" in result.output or "| name |" in result.output
