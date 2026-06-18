"""``rag-ingest`` 默认 Pipeline 配置测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from data import SAMPLE_MD
from rag.config import settings
from rag.ingest.cli import app, default_pipeline
from rag.ingest.normalizer import StructureMode, StructureNormalizer

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape sequences (rich rendering inserts codes between tokens)."""
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_default_pipeline_uses_structure_normalizer_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = MagicMock()
    monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-fake"))
    monkeypatch.setattr(
        "rag.ingest.cli.get_structured_chat_model",
        lambda *args, **kwargs: fake_model,
    )
    pipeline = default_pipeline(persist_config=None)
    assert isinstance(pipeline.normalizer, StructureNormalizer)
    assert pipeline.normalizer._mode is StructureMode.AUTO  # noqa: SLF001
    assert pipeline.normalizer._chat_model is fake_model  # noqa: SLF001


def test_ingest_succeeds_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing API key → 降级为 FORBID normalizer (透传), 不报错。"""
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
    result = runner.invoke(app, [str(SAMPLE_MD)])
    assert result.exit_code == 0, result.output
    assert "title: " in result.output
    assert "chunks: " in result.output


def test_default_pipeline_forbid_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证无 API key 时 default_pipeline 使用 FORBID + chat_model=None。"""
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
    pipeline = default_pipeline(persist_config=None)
    assert isinstance(pipeline.normalizer, StructureNormalizer)
    assert pipeline.normalizer._mode is StructureMode.FORBID  # noqa: SLF001
    assert pipeline.normalizer._chat_model is None  # noqa: SLF001


def test_ingest_help_lists_mode_and_dataset() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    clean = _strip_ansi(result.output)
    assert "--dataset-name" in clean
    assert "--dataset-id" in clean
