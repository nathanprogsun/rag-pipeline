"""Unit tests for ``rag.search.cli`` (rag-search) and ``rag.eval.cli`` (rag-eval).

Tests use CliRunner to invoke the typer apps with mocked pipeline / runner.
Verifies:
- Argument validation (query / dataset_ids required)
- Output format selection (text vs json)
- Audit flag handling
- Error exit codes on missing args
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.eval.cli import app as eval_app
from rag.eval.gate import GateResult
from rag.eval.runner import UnifiedEvalSummary
from rag.search.cli import app as search_app

runner = CliRunner()


def _meta() -> ChunkMetadata:
    return ChunkMetadata(datasource="file")


def _doc(chunk_id_str: str, *, score: float = 0.5) -> ScoredDocument:
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str),
        dataset_id=uuid.uuid4(),
        text=f"text {chunk_id_str}",
        score=score,
        rank=0,
        source="vector",
        metadata=_meta(),
    )


def _search_result() -> SearchResult:
    citations = [
        Citation(
            chunk_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            source_name="src-1",
            content="Python 教程内容",
            score=0.9,
        )
    ]
    r = SearchResult(
        response="Python 教程很好. [1](CITE)",
        citations=citations,
    )
    r._intermediate_hits = [_doc(str(uuid.uuid4()))]
    return r


# ---------- rag-search: argument validation ----------


def test_search_requires_query() -> None:
    """No --query → typer.Exit(1)."""
    result = runner.invoke(search_app, ["--dataset-id", str(uuid.uuid4())])
    assert result.exit_code != 0


def test_search_requires_dataset_id() -> None:
    """No --dataset-id → typer.Exit(1)."""
    result = runner.invoke(search_app, ["--query", "test"])
    assert result.exit_code != 0


def test_search_invalid_output_format() -> None:
    """--output invalid → typer.Exit(1)."""
    result = runner.invoke(
        search_app,
        [
            "--query",
            "test",
            "--dataset-id",
            str(uuid.uuid4()),
            "--output",
            "xml",
        ],
    )
    assert result.exit_code != 0


# ---------- rag-search: text output ----------


def test_search_text_output_runs_pipeline() -> None:
    """Default output format is text. Pipeline is invoked."""
    ds_id = uuid.uuid4()
    fake_result = _search_result()
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.search.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.search.cli.get_chat_model", return_value=fake_llm),
        patch("rag.search.cli.SearchPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.ainvoke = AsyncMock(return_value=fake_result)
        mock_pipeline_cls.return_value = mock_pipeline

        result = runner.invoke(
            search_app,
            ["--query", "Python 教程", "--dataset-id", str(ds_id)],
        )

    assert result.exit_code == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "Query: Python 教程" in result.output
    assert "Response:" in result.output
    assert "Citations (1):" in result.output


def test_search_json_output_serializes() -> None:
    """--output json emits valid JSON to stdout."""
    ds_id = uuid.uuid4()
    fake_result = _search_result()
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.search.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.search.cli.get_chat_model", return_value=fake_llm),
        patch("rag.search.cli.SearchPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.ainvoke = AsyncMock(return_value=fake_result)
        mock_pipeline_cls.return_value = mock_pipeline

        result = runner.invoke(
            search_app,
            [
                "--query",
                "Python 教程",
                "--dataset-id",
                str(ds_id),
                "--output",
                "json",
            ],
        )

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.output)
    assert parsed["query"] == "Python 教程"
    assert str(ds_id) in parsed["dataset_ids"]
    assert len(parsed["citations"]) == 1


# ---------- rag-search: audit ----------


def test_search_audit_writes_to_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--audit + --audit-path writes one NDJSON line via SearchPipeline._maybe_record_audit."""
    from rag.domain.document import ChunkMetadata
    from rag.infra.observability.audit import AuditTap
    from rag.search.orchestrator import SearchPipeline

    audit_file = tmp_path / "audit.jsonl"
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    ai = MagicMock()
    ai.content = "answer"
    fake_llm.ainvoke = AsyncMock(return_value=ai)

    async def _mock_search_vector(self: object, query: str, top_k: int = 10) -> list:
        from rag.domain.document import ScoredDocument

        return [
            ScoredDocument(
                chunk_id=uuid.uuid4(),
                dataset_id=self.dataset_id,  # type: ignore[attr-defined]
                text="hello",
                score=0.9,
                rank=0,
                source="vector",
                metadata=ChunkMetadata(datasource="file"),
            )
        ]

    async def _mock_search_fulltext(self: object, query: str, top_k: int = 10) -> list:
        return []

    monkeypatch.setattr(
        "rag.infra.pg.vector_store.VectorRetriever.search",
        _mock_search_vector,
    )
    monkeypatch.setattr(
        "rag.infra.pg.fulltext_store.FulltextRetriever.search",
        _mock_search_fulltext,
    )

    real_pipeline = SearchPipeline(
        embedder=fake_embedder,
        llm=fake_llm,
        audit_tap=AuditTap(audit_file, sample_rate=1.0, sync=True),
    )

    with (
        patch("rag.search.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.search.cli.get_chat_model", return_value=fake_llm),
        patch("rag.search.cli.SearchPipeline", return_value=real_pipeline),
    ):
        result = runner.invoke(
            search_app,
            [
                "--query",
                "test",
                "--dataset-id",
                str(uuid.uuid4()),
                "--audit",
                "--audit-path",
                str(audit_file),
                "--no-query-ext",
            ],
        )

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    text = audit_file.read_text(encoding="utf-8").strip()
    assert len(text.split("\n")) == 1


def test_search_audit_no_path_warns(
    tmp_path: Path,
) -> None:
    """--audit without --audit-path and no settings → warn + skip audit.

    当前 settings 没有 cache_audit_path 字段, 此测试覆盖 ``_default_audit_path``
    返回 None 的分支: CLI 走 no-op 但 exit 0.
    """
    fake_result = _search_result()
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.search.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.search.cli.get_chat_model", return_value=fake_llm),
        patch("rag.search.cli.SearchPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.ainvoke = AsyncMock(return_value=fake_result)
        mock_pipeline_cls.return_value = mock_pipeline

        result = runner.invoke(
            search_app,
            [
                "--query",
                "test",
                "--dataset-id",
                str(uuid.uuid4()),
                "--audit",
                # no --audit-path
            ],
        )

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    # Warning emitted (path is None)
    assert "audit" in result.stderr.lower() or "audit" in result.output.lower()


# ---------- rag-search: multiple dataset_ids ----------


def test_search_multiple_dataset_ids() -> None:
    """--dataset-id 可多次指定。"""
    ds_a = uuid.uuid4()
    ds_b = uuid.uuid4()
    fake_result = _search_result()
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.search.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.search.cli.get_chat_model", return_value=fake_llm),
        patch("rag.search.cli.SearchPipeline") as mock_pipeline_cls,
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.ainvoke = AsyncMock(return_value=fake_result)
        mock_pipeline_cls.return_value = mock_pipeline

        result = runner.invoke(
            search_app,
            [
                "--query",
                "test",
                "--dataset-id",
                str(ds_a),
                "--dataset-id",
                str(ds_b),
            ],
        )

    assert result.exit_code == 0, f"stderr={result.stderr!r}"


# ---------- rag-eval: argument validation ----------


def test_eval_requires_dataset(tmp_path: Path) -> None:
    """No --dataset → typer.Exit(1)."""
    result = runner.invoke(eval_app, [])
    assert result.exit_code != 0


def test_eval_dataset_not_found() -> None:
    """--dataset pointing to nonexistent file → typer.Exit(1)."""
    result = runner.invoke(eval_app, ["--dataset", "/nonexistent/path.jsonl"])
    assert result.exit_code != 0


def test_eval_invalid_output_format(tmp_path: Path) -> None:
    """--output invalid → typer.Exit(1)."""
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")
    result = runner.invoke(eval_app, ["--dataset", str(f), "--output", "yaml"])
    assert result.exit_code != 0


# ---------- rag-eval: text output ----------


def test_eval_text_output(tmp_path: Path) -> None:
    """--output text emits human-readable summary."""
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")

    fake_summary = UnifiedEvalSummary(
        sample_count=0,
        metric_aggregates={},
        gate=GateResult(passed=True),
        warnings=["empty dataset"],
    )
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.eval.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.eval.cli.get_chat_model", return_value=fake_llm),
        patch("rag.eval.cli.UnifiedEvalRunner") as mock_runner_cls,
    ):
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=fake_summary)
        mock_runner_cls.return_value = mock_runner

        result = runner.invoke(eval_app, ["--dataset", str(f)])

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    assert "Samples: 0" in result.output
    assert "Warnings: 1" in result.output


def test_eval_json_output(tmp_path: Path) -> None:
    """--output json emits JSON."""
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")

    fake_summary = UnifiedEvalSummary(
        sample_count=0,
        metric_aggregates={},
        gate=GateResult(passed=True),
        warnings=[],
    )
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.eval.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.eval.cli.get_chat_model", return_value=fake_llm),
        patch("rag.eval.cli.UnifiedEvalRunner") as mock_runner_cls,
    ):
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=fake_summary)
        mock_runner_cls.return_value = mock_runner

        result = runner.invoke(eval_app, ["--dataset", str(f), "--output", "json"])

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    parsed = json.loads(result.output)
    assert parsed["sample_count"] == 0


def test_eval_output_path_writes_file(tmp_path: Path) -> None:
    """--output-path writes summary JSON file.

    Uses a real EvalRunner so the output_path write logic is exercised
    end-to-end (a mock that only sets ``run`` return value would skip the
    file write inside ``EvalRunner.run``).
    """
    from rag.domain.search import SearchResult
    from rag.eval.runner import UnifiedEvalRunner as RealEvalRunner

    f = tmp_path / "eval.jsonl"
    # Write 2 valid records
    f.write_text(
        json.dumps(
            {
                "query": "q1",
                "dataset_ids": [],
                "ground_truth_chunk_ids": [str(uuid.uuid4())],
                "k": 5,
            }
        )
        + "\n"
        + json.dumps(
            {
                "query": "q2",
                "dataset_ids": [],
                "ground_truth_chunk_ids": [str(uuid.uuid4())],
                "k": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "summary.json"

    async def _fake_pipeline(req: SearchRequest) -> SearchResult:
        r = SearchResult(response="x", citations=[])
        r._intermediate_hits = []
        return r

    fake_embedder = MagicMock()
    fake_llm = MagicMock()

    real_runner = RealEvalRunner(
        pipeline=_fake_pipeline,
        config=__import__("rag.eval.config", fromlist=["EvalConfig"]).EvalConfig(
            gen_backend="skip", default_k=10, concurrency=1
        ),
    )
    with (
        patch("rag.eval.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.eval.cli.get_chat_model", return_value=fake_llm),
        patch("rag.eval.cli.UnifiedEvalRunner", return_value=real_runner),
    ):
        result = runner.invoke(
            eval_app,
            ["--dataset", str(f), "--output-path", str(out)],
        )

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    assert out.exists()
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["sample_count"] == 2


def test_eval_warning_printed_to_stderr(tmp_path: Path) -> None:
    """Run with warnings → stderr message printed."""
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")

    fake_summary = UnifiedEvalSummary(
        sample_count=0,
        metric_aggregates={},
        gate=GateResult(passed=True),
        warnings=["pipeline failed"],
    )
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.eval.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.eval.cli.get_chat_model", return_value=fake_llm),
        patch("rag.eval.cli.UnifiedEvalRunner") as mock_runner_cls,
    ):
        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=fake_summary)
        mock_runner_cls.return_value = mock_runner

        result = runner.invoke(eval_app, ["--dataset", str(f)])

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    assert "warning" in result.stderr.lower()


# ---------- rag-eval: pipeline integration ----------


def test_eval_passes_correct_args_to_runner(tmp_path: Path) -> None:
    """EvalRunner initialized with --concurrency and SearchPipeline.ainvoke."""
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")

    fake_summary = UnifiedEvalSummary(
        sample_count=0,
        metric_aggregates={},
        gate=GateResult(passed=True),
        warnings=[],
    )
    fake_embedder = MagicMock()
    fake_llm = MagicMock()
    with (
        patch("rag.eval.cli.get_embed_model", return_value=fake_embedder),
        patch("rag.eval.cli.get_chat_model", return_value=fake_llm),
        patch("rag.eval.cli.SearchPipeline") as mock_pipeline_cls,
        patch("rag.eval.cli.UnifiedEvalRunner") as mock_runner_cls,
    ):
        mock_pipeline = MagicMock()
        mock_pipeline.ainvoke = AsyncMock()
        mock_pipeline_cls.return_value = mock_pipeline

        mock_runner = MagicMock()
        mock_runner.run = AsyncMock(return_value=fake_summary)
        mock_runner_cls.return_value = mock_runner

        result = runner.invoke(
            eval_app,
            [
                "--dataset",
                str(f),
                "--concurrency",
                "8",
                "--k",
                "20",
            ],
        )

    assert result.exit_code == 0, f"stderr={result.stderr!r}"
    mock_runner_cls.assert_called_once()
    call = mock_runner_cls.call_args
    # 统一 runner 接 EvalConfig, 字段从 config 取
    cfg = call.kwargs["config"]
    assert cfg.concurrency == 8
    assert cfg.default_k == 20
