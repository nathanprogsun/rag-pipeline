"""``rag-ingest`` CLI 的 ``--normalize`` 选项测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from data import SAMPLE_MD  # noqa: E402
from rag.config import settings  # noqa: E402
from rag.ingest.cli import (  # noqa: E402
    _build_pipeline,
    _structure_mode_for_cli,
    app,
)
from rag.ingest.normalizer import (  # noqa: E402
    NoOpNormalizer,
    StructureMode,
    StructureNormalizer,
)

runner = CliRunner()


def test_structure_mode_for_cli_mapping() -> None:
    assert _structure_mode_for_cli("off") is None
    assert _structure_mode_for_cli("auto") == StructureMode.AUTO
    assert _structure_mode_for_cli("force") == StructureMode.FORCE


def test_build_pipeline_off_uses_no_op() -> None:
    pipeline = _build_pipeline(normalize="off")
    assert isinstance(pipeline.normalizer, NoOpNormalizer)


def test_build_pipeline_force_uses_structure_normalizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_model = MagicMock()
    monkeypatch.setattr(
        "rag.ingest.cli.get_structured_chat_model",
        lambda *args, **kwargs: fake_model,
    )
    pipeline = _build_pipeline(normalize="force")
    assert isinstance(pipeline.normalizer, StructureNormalizer)
    assert pipeline.normalizer._mode is StructureMode.FORCE  # noqa: SLF001


def test_normalize_force_without_api_key_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", SecretStr(""))
    result = runner.invoke(app, ["--normalize", "force", str(SAMPLE_MD)])
    assert result.exit_code == 1
    combined = result.output + result.stderr
    assert "config.missing_env" in combined
    assert "OPENAI_API_KEY" in combined


def test_ingest_help_lists_normalize_option() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "--normalize" in result.output
    assert "--mode" in result.output
