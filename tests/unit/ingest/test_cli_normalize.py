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


def test_default_pipeline_uses_structure_normalizer_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = MagicMock()
    monkeypatch.setattr(
        "rag.ingest.cli.get_structured_chat_model",
        lambda *args, **kwargs: fake_model,
    )
    pipeline = default_pipeline(persist_config=None)
    assert isinstance(pipeline.normalizer, StructureNormalizer)
    assert pipeline.normalizer._mode is StructureMode.AUTO  # noqa: SLF001


def test_ingest_without_api_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
    result = runner.invoke(app, [str(SAMPLE_MD)])
    assert result.exit_code == 1
    combined = result.output + result.stderr
    assert "config.missing_env" in combined
    assert "OPENAI_API_KEY" in combined


def test_ingest_help_lists_mode_and_dataset() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--dataset-name" in result.output
    assert "--dataset-id" in result.output
