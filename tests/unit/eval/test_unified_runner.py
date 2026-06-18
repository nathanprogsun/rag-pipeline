"""Unit tests for ``rag.eval.runner.UnifiedEvalRunner`` (5h 重写).

覆盖:
- JSONL 加载 + 并发跑 pipeline
- 检索指标聚合
- 生成指标 (via NaiveBackend) 集成
- gate 判定 + baseline diff
- artifact 落盘
- 失败 query 作为 warning 保留
- 并发上限生效
- 空数据集
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest, SearchResult
from rag.eval.config import EvalConfig, GateThresholds
from rag.eval.runner import UnifiedEvalRunner

# ---------- Helpers ----------


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


def _result_with_hits(
    hits: list[ScoredDocument], *, answer: str = "test answer"
) -> SearchResult:
    r = SearchResult(response=answer, citations=[])
    r._intermediate_hits = hits
    return r


def _write_jsonl(tmp_path: Path, records: list[dict]) -> Path:
    f = tmp_path / "eval.jsonl"
    with f.open("w", encoding="utf-8") as fp:
        for r in records:
            fp.write(json.dumps(r) + "\n")
    return f


# ---------- run() ----------


@pytest.mark.asyncio
async def test_runner_empty_dataset_returns_summary(tmp_path: Path) -> None:
    f = tmp_path / "eval.jsonl"
    f.write_text("", encoding="utf-8")

    pipeline = AsyncMock()
    runner = UnifiedEvalRunner(pipeline=pipeline, config=EvalConfig(gen_backend="skip"))
    summary = await runner.run(f)

    assert summary.sample_count == 0
    assert summary.metric_aggregates == {}
    assert summary.warnings == ["empty dataset"]
    pipeline.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_runs_pipeline_per_record(tmp_path: Path) -> None:
    a_id, b_id = str(uuid.uuid4()), str(uuid.uuid4())
    f = _write_jsonl(
        tmp_path,
        [
            {"query": "q1", "ground_truth_chunk_ids": [a_id], "k": 5},
            {"query": "q2", "ground_truth_chunk_ids": [b_id], "k": 5},
        ],
    )

    pipeline = AsyncMock(
        side_effect=[
            _result_with_hits([_doc(a_id)]),
            _result_with_hits([_doc(b_id)]),
        ]
    )
    runner = UnifiedEvalRunner(pipeline=pipeline, config=EvalConfig(gen_backend="skip"))
    summary = await runner.run(f)

    assert summary.sample_count == 2
    assert pipeline.await_count == 2
    # Each query has perfect recall (1 GT hit in top-5)
    assert summary.metric_aggregates["recall@5"]["mean"] == 1.0


@pytest.mark.asyncio
async def test_runner_pipeline_failure_captured_as_warning(tmp_path: Path) -> None:
    """Pipeline 抛错时, query 仍计入 sample_count 但无指标 + warning 记录。"""
    f = _write_jsonl(tmp_path, [{"query": "q1", "ground_truth_chunk_ids": [], "k": 5}])

    pipeline = AsyncMock(side_effect=RuntimeError("DB down"))
    runner = UnifiedEvalRunner(pipeline=pipeline, config=EvalConfig(gen_backend="skip"))
    summary = await runner.run(f)

    # 失败 query 计入样本数 (便于看到"跑了多少条"), 但 metric_aggregates 为空
    assert summary.sample_count == 1
    assert summary.metric_aggregates == {}
    assert len(summary.warnings) == 1
    assert "pipeline failed" in summary.warnings[0]


@pytest.mark.asyncio
async def test_runner_writes_output_json(tmp_path: Path) -> None:
    a_id = str(uuid.uuid4())
    f = _write_jsonl(
        tmp_path, [{"query": "q1", "ground_truth_chunk_ids": [a_id], "k": 5}]
    )
    pipeline = AsyncMock(return_value=_result_with_hits([_doc(a_id)]))
    output_path = tmp_path / "summary.json"

    runner = UnifiedEvalRunner(pipeline=pipeline, config=EvalConfig(gen_backend="skip"))
    await runner.run(f, output_path=output_path)

    assert output_path.exists()
    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["sample_count"] == 1
    assert "recall@5" in parsed["metric_aggregates"]


@pytest.mark.asyncio
async def test_runner_concurrency_limits_parallelism(tmp_path: Path) -> None:
    f = _write_jsonl(
        tmp_path,
        [{"query": f"q{i}", "ground_truth_chunk_ids": [], "k": 5} for i in range(5)],
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

    config = EvalConfig(gen_backend="skip", concurrency=2)
    runner = UnifiedEvalRunner(pipeline=slow_pipeline, config=config)
    await runner.run(f)

    assert max_concurrent <= 2


# ---------- gen backend integration ----------


@pytest.mark.asyncio
async def test_runner_calls_gen_backend(tmp_path: Path) -> None:
    """``gen_backend=naive`` 时生成指标出现在聚合里。"""
    a_id = str(uuid.uuid4())
    f = _write_jsonl(
        tmp_path,
        [
            {
                "query": "北京天气",
                "ground_truth_chunk_ids": [a_id],
                "reference_answer": "北京天气晴",
                "reference_contexts": ["北京天气晴朗"],
            }
        ],
    )
    pipeline = AsyncMock(
        return_value=_result_with_hits([_doc(a_id)], answer="北京天气晴")
    )
    runner = UnifiedEvalRunner(
        pipeline=pipeline, config=EvalConfig(gen_backend="naive")
    )
    summary = await runner.run(f)

    # 检索指标
    assert "recall@10" in summary.metric_aggregates
    # 生成指标 (来自 NaiveBackend)
    assert "faithfulness" in summary.metric_aggregates
    assert "answer_relevance" in summary.metric_aggregates


@pytest.mark.asyncio
async def test_runner_skip_backend_skips_generation(tmp_path: Path) -> None:
    a_id = str(uuid.uuid4())
    f = _write_jsonl(tmp_path, [{"query": "q", "ground_truth_chunk_ids": [a_id]}])
    pipeline = AsyncMock(return_value=_result_with_hits([_doc(a_id)]))

    runner = UnifiedEvalRunner(pipeline=pipeline, config=EvalConfig(gen_backend="skip"))
    summary = await runner.run(f)

    # 只有检索指标
    assert "recall@10" in summary.metric_aggregates
    assert "faithfulness" not in summary.metric_aggregates


# ---------- gate ----------


@pytest.mark.asyncio
async def test_runner_gate_passed_when_above_threshold(tmp_path: Path) -> None:
    a_id = str(uuid.uuid4())
    f = _write_jsonl(tmp_path, [{"query": "q", "ground_truth_chunk_ids": [a_id]}])
    pipeline = AsyncMock(return_value=_result_with_hits([_doc(a_id)]))

    cfg = EvalConfig(
        gen_backend="skip",
        gate=GateThresholds(min_recall_at_k=0.5),
    )
    runner = UnifiedEvalRunner(pipeline=pipeline, config=cfg)
    summary = await runner.run(f)

    assert summary.passed is True
    assert summary.exit_code() == 0


@pytest.mark.asyncio
async def test_runner_gate_failed_when_below_threshold(tmp_path: Path) -> None:
    a_id = str(uuid.uuid4())
    f = _write_jsonl(tmp_path, [{"query": "q", "ground_truth_chunk_ids": [a_id]}])
    # pipeline 不返回 gt chunk, recall=0
    pipeline = AsyncMock(return_value=_result_with_hits([]))

    cfg = EvalConfig(
        gen_backend="skip",
        gate=GateThresholds(min_recall_at_k=0.5),
    )
    runner = UnifiedEvalRunner(pipeline=pipeline, config=cfg)
    summary = await runner.run(f)

    assert summary.passed is False
    assert summary.exit_code() == 1


# ---------- baseline diff ----------


@pytest.mark.asyncio
async def test_runner_baseline_diff_attached(tmp_path: Path) -> None:
    a_id = str(uuid.uuid4())
    f = _write_jsonl(tmp_path, [{"query": "q", "ground_truth_chunk_ids": [a_id]}])
    pipeline = AsyncMock(return_value=_result_with_hits([_doc(a_id)]))

    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "sample_count": 1,
                "metric_aggregates": {
                    "recall@10": {
                        "mean": 0.5,
                        "std": 0,
                        "min": 0.5,
                        "max": 0.5,
                        "median": 0.5,
                        "count": 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    cfg = EvalConfig(gen_backend="skip", baseline_path=baseline)
    runner = UnifiedEvalRunner(pipeline=pipeline, config=cfg)
    summary = await runner.run(f)

    assert summary.baseline_delta is not None
    assert "recall@10" in summary.baseline_delta


# ---------- artifact ----------


@pytest.mark.asyncio
async def test_runner_writes_artifact_per_query(tmp_path: Path) -> None:
    a_id = str(uuid.uuid4())
    f = _write_jsonl(tmp_path, [{"query": "q1", "ground_truth_chunk_ids": [a_id]}])
    pipeline = AsyncMock(return_value=_result_with_hits([_doc(a_id)], answer="a"))

    artifact_dir = tmp_path / "artifacts"
    cfg = EvalConfig(gen_backend="naive", artifact_dir=artifact_dir)
    runner = UnifiedEvalRunner(pipeline=pipeline, config=cfg)
    await runner.run(f)

    assert (artifact_dir / "per_query" / "0000.json").exists()
    assert (artifact_dir / "summary.json").exists()

    parsed = json.loads((artifact_dir / "per_query" / "0000.json").read_text())
    assert parsed["query"] == "q1"
    assert parsed["answer"] == "a"
    assert "retrieval_metrics" in parsed
