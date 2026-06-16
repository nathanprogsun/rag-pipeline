"""Unit tests for ``rag.eval.runner`` (5h EvalRunner).

Tests cover:
- _load_jsonl parses records
- _compute_metrics dispatches by metric name
- EvalRunner.run executes pipeline, aggregates metrics
- Empty dataset returns zeros
- Pipeline failure captured as warning
- Output JSON written when output_path set
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest, SearchResult
from rag.eval._jsonl import load_jsonl
from rag.eval.runner import (
    EvalRecord,
    EvalRunner,
    EvalSummary,
    _compute_metrics,
)

# ---------- Helpers ----------


def _meta() -> ChunkMetadata:
    return ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file")


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


def _result_with_hits(hits: list[ScoredDocument]) -> SearchResult:
    r = SearchResult(response="x", citations=[])
    r._intermediate_hits = hits
    return r


# ---------- _load_jsonl ----------


def test_load_jsonl_parses_records(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    f.write_text(
        json.dumps(
            {
                "query": "q1",
                "dataset_ids": [str(uuid.uuid4())],
                "ground_truth_chunk_ids": [str(uuid.uuid4())],
                "k": 5,
            }
        )
        + "\n"
        + json.dumps(
            {
                "query": "q2",
                "dataset_ids": [],
                "ground_truth_chunk_ids": [],
                "k": 10,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    records = load_jsonl(f, EvalRecord)
    assert len(records) == 2
    assert records[0].query == "q1"
    assert records[0].k == 5


def test_load_jsonl_skips_empty_lines(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    f.write_text(
        '{"query":"q1","dataset_ids":[],"ground_truth_chunk_ids":[],"k":10}\n\n',
        encoding="utf-8",
    )
    records = load_jsonl(f, EvalRecord)
    assert len(records) == 1


def test_load_jsonl_skips_malformed(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    f.write_text('{"query":"q1"}\nNOT VALID JSON\n{"query":"q2"}\n', encoding="utf-8")
    records = load_jsonl(f, EvalRecord)
    assert len(records) == 2


def test_load_jsonl_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")
    assert load_jsonl(f, EvalRecord) == []


# ---------- _compute_metrics ----------


def test_compute_metrics_recall_at_k() -> None:
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    retrieved = [a, b]
    gt = [a]
    metrics = _compute_metrics(
        retrieved_ids=[uuid.UUID(x) for x in retrieved],
        ground_truth_ids=[uuid.UUID(x) for x in gt],
        k=2,
        metrics=["recall@2"],
    )
    assert metrics["recall@2"] == 1.0  # both retrieved, 1 hit, 1 in gt


def test_compute_metrics_multiple_metrics() -> None:
    a = str(uuid.uuid4())
    metrics = _compute_metrics(
        retrieved_ids=[uuid.UUID(a)],
        ground_truth_ids=[uuid.UUID(a)],
        k=5,
        metrics=["recall@5", "precision@5", "mrr", "ndcg@5", "hit_rate@5"],
    )
    assert all(m == 1.0 for m in metrics.values())


def test_compute_metrics_unknown_name_skipped() -> None:
    """Unknown metric name doesn't crash; just not in output."""
    a = str(uuid.uuid4())
    metrics = _compute_metrics(
        retrieved_ids=[uuid.UUID(a)],
        ground_truth_ids=[uuid.UUID(a)],
        k=5,
        metrics=["recall@5", "unknown_metric"],
    )
    assert "recall@5" in metrics
    assert "unknown_metric" not in metrics


# ---------- EvalRunner.run ----------


async def test_runner_empty_dataset_returns_zeros(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")

    pipeline = AsyncMock()
    runner = EvalRunner(pipeline=pipeline)
    summary = await runner.run(f)

    assert isinstance(summary, EvalSummary)
    assert summary.sample_count == 0
    assert summary.metric_aggregates == {}
    assert summary.warnings == ["empty dataset"]
    pipeline.assert_not_awaited()


async def test_runner_runs_pipeline_per_record(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
    f.write_text(
        json.dumps(
            {
                "query": "q1",
                "dataset_ids": [str(uuid.uuid4())],
                "ground_truth_chunk_ids": [a_id],
                "k": 5,
            }
        )
        + "\n"
        + json.dumps(
            {
                "query": "q2",
                "dataset_ids": [],
                "ground_truth_chunk_ids": [b_id],
                "k": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    # Pipeline returns the ground truth chunk for each query
    a_doc = _doc(a_id)
    b_doc = _doc(b_id)
    pipeline = AsyncMock(
        side_effect=[
            _result_with_hits([a_doc]),
            _result_with_hits([b_doc]),
        ]
    )
    runner = EvalRunner(pipeline=pipeline)
    summary = await runner.run(f)

    assert summary.sample_count == 2
    assert pipeline.await_count == 2
    # Each query has perfect recall (1 ground truth hit in top-5)
    assert summary.metric_aggregates["recall@5"]["mean"] == 1.0


async def test_runner_pipeline_failure_captured_as_warning(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    f.write_text(
        json.dumps(
            {
                "query": "q1",
                "dataset_ids": [],
                "ground_truth_chunk_ids": [],
                "k": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    pipeline = AsyncMock(side_effect=RuntimeError("DB down"))
    runner = EvalRunner(pipeline=pipeline)
    summary = await runner.run(f)

    # sample_count=0 because pipeline failed for all records
    assert summary.sample_count == 0
    assert len(summary.warnings) == 1
    assert "pipeline failed" in summary.warnings[0]


async def test_runner_writes_output_json(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    a_id = str(uuid.uuid4())
    f.write_text(
        json.dumps(
            {
                "query": "q1",
                "dataset_ids": [],
                "ground_truth_chunk_ids": [a_id],
                "k": 5,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    a_doc = _doc(a_id)
    pipeline = AsyncMock(return_value=_result_with_hits([a_doc]))
    output_path = tmp_path / "summary.json"

    runner = EvalRunner(pipeline=pipeline)
    await runner.run(f, output_path=output_path)

    assert output_path.exists()
    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["sample_count"] == 1
    assert "recall@5" in parsed["metric_aggregates"]


async def test_runner_concurrency_limits_parallelism(tmp_path: Path) -> None:
    """With concurrency=2, never more than 2 pipeline calls run simultaneously."""
    import asyncio

    f = tmp_path / "eval.jsonl"
    for i in range(5):
        f.write_text(
            json.dumps(
                {
                    "query": f"q{i}",
                    "dataset_ids": [],
                    "ground_truth_chunk_ids": [],
                    "k": 5,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    concurrent = 0
    max_concurrent = 0

    async def slow_pipeline(req: SearchRequest) -> SearchResult:
        nonlocal concurrent, max_concurrent
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        await asyncio.sleep(0.05)
        concurrent -= 1
        return _result_with_hits([])

    runner = EvalRunner(pipeline=slow_pipeline, concurrency=2)
    await runner.run(f)

    # Should never exceed 2 concurrent calls
    assert max_concurrent <= 2


# ---------- EvalRecord ----------


def test_eval_record_minimal_fields() -> None:
    """Required: query. Optional: dataset_ids, ground_truth_chunk_ids, k."""
    rec = EvalRecord(query="test")
    assert rec.dataset_ids == []
    assert rec.ground_truth_chunk_ids == []
    assert rec.k == 10
    assert rec.metadata == {}


def test_eval_record_full() -> None:
    ds_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    rec = EvalRecord(
        query="Python 教程",
        dataset_ids=[ds_id],
        ground_truth_chunk_ids=[chunk_id],
        k=5,
        metadata={"source": "test"},
    )
    assert rec.query == "Python 教程"
    assert rec.dataset_ids == [ds_id]
    assert rec.ground_truth_chunk_ids == [chunk_id]
    assert rec.k == 5
    assert rec.metadata == {"source": "test"}
