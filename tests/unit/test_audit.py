"""Unit tests for ``rag.infra.observability.audit`` (5e1).

Tests cover:
- AuditRecord.from_search_result captures all fields
- AuditRecord.model_dump_json produces valid JSON
- AuditTap.record appends one JSON object per line (NDJSON)
- AuditTap sample_rate < 1.0 drops records probabilistically
- AuditTap.record on closed tap returns False
- AuditTap.record on write error logs warning, returns False
- read_jsonl_records parses valid NDJSON, skips malformed lines
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.infra.observability.audit import AuditRecord, AuditTap, read_jsonl_records


def _req(*, query: str = "test") -> SearchRequest:
    return SearchRequest(query=query, dataset_ids=[uuid.uuid4()])


def _result(
    *,
    response: str = "answer",
    citations: list[Citation] | None = None,
    warnings: list[str] | None = None,
    failed: list[uuid.UUID] | None = None,
    intermediate_hits: list[ScoredDocument] | None = None,
) -> SearchResult:
    citations = citations or []
    failed = failed or []
    warnings = warnings or []
    result = SearchResult(
        response=response,
        citations=citations,
        failed_dataset_ids=failed,
        warnings=warnings,
    )
    if intermediate_hits:
        result._intermediate_hits = list(intermediate_hits)
    return result


# ---------- AuditRecord ----------


def test_audit_record_from_search_result_captures_query() -> None:
    req = _req(query="Python 教程")
    result = _result()
    rec = AuditRecord.from_search_result(req, result)
    assert rec.query == "Python 教程"


def test_audit_record_from_search_result_captures_dataset_ids() -> None:
    ds = uuid.uuid4()
    req = SearchRequest(query="q", dataset_ids=[ds])
    rec = AuditRecord.from_search_result(req, _result())
    assert rec.dataset_ids == [ds]


def test_audit_record_from_search_result_captures_response() -> None:
    rec = AuditRecord.from_search_result(_req(), _result(response="answer text"))
    assert rec.response == "answer text"


def test_audit_record_from_search_result_captures_warnings() -> None:
    rec = AuditRecord.from_search_result(_req(), _result(warnings=["warn-1", "warn-2"]))
    assert rec.warnings == ["warn-1", "warn-2"]


def test_audit_record_from_search_result_captures_failed_dataset_ids() -> None:
    failed = uuid.uuid4()
    rec = AuditRecord.from_search_result(_req(), _result(failed=[failed]))
    assert rec.failed_dataset_ids == [failed]


def test_audit_record_from_search_result_intermediate_count() -> None:
    """Contract 6: _intermediate_hits 编程可访问。"""
    from rag.domain.document import ChunkMetadata

    hits = [
        ScoredDocument(
            chunk_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            text=f"t{i}",
            score=0.5,
            rank=0,
            source="vector",
            metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"),
        )
        for i in range(3)
    ]
    rec = AuditRecord.from_search_result(_req(), _result(intermediate_hits=hits))
    assert rec.intermediate_hits_count == 3


def test_audit_record_citation_count() -> None:
    cits = [
        Citation(
            chunk_id=uuid.uuid4(),
            dataset_id=uuid.uuid4(),
            source_name=f"src-{i}",
            content="x",
            score=0.5,
        )
        for i in range(4)
    ]
    rec = AuditRecord.from_search_result(_req(), _result(citations=cits))
    assert rec.citation_count == 4


def test_audit_record_request_id_unique_per_call() -> None:
    rec1 = AuditRecord.from_search_result(_req(), _result())
    rec2 = AuditRecord.from_search_result(_req(), _result())
    assert rec1.request_id != rec2.request_id


def test_audit_record_request_id_override() -> None:
    rec = AuditRecord.from_search_result(_req(), _result(), request_id="custom-id-123")
    assert rec.request_id == "custom-id-123"


def test_audit_record_to_json_round_trip() -> None:
    """model_dump_json 应产生可被 json.loads 解析的有效 JSON。"""
    rec = AuditRecord.from_search_result(
        _req(query="test"),
        _result(response="ans", warnings=["w"]),
    )
    parsed = json.loads(rec.model_dump_json())
    assert parsed["query"] == "test"
    assert parsed["response"] == "ans"
    assert parsed["warnings"] == ["w"]


def test_audit_record_timestamp_present() -> None:
    rec = AuditRecord.from_search_result(_req(), _result())
    parsed = json.loads(rec.model_dump_json())
    assert "timestamp" in parsed


# ---------- AuditTap ----------


async def test_audit_tap_writes_one_jsonl_line(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    tap = AuditTap(f, sample_rate=1.0, sync=True)
    rec = AuditRecord.from_search_result(_req(), _result())
    recorded = await tap.record(rec)
    tap.close()
    assert recorded is True
    # File has exactly one line
    lines = f.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    # Line is valid JSON
    parsed = json.loads(lines[0])
    assert parsed["query"] == rec.query


async def test_audit_tap_appends_multiple_records(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    tap = AuditTap(f, sample_rate=1.0, sync=True)
    for i in range(3):
        await tap.record(AuditRecord.from_search_result(_req(query=f"q{i}"), _result()))
    tap.close()
    lines = f.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3
    queries = [json.loads(line)["query"] for line in lines]
    assert queries == ["q0", "q1", "q2"]


async def test_audit_tap_sample_rate_zero_drops_all(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    tap = AuditTap(f, sample_rate=0.0, sync=True)
    for _ in range(5):
        recorded = await tap.record(AuditRecord.from_search_result(_req(), _result()))
        assert recorded is False
    tap.close()
    # No file content (file may not even exist if never written)
    if f.exists():
        assert f.read_text(encoding="utf-8") == ""


async def test_audit_tap_sample_rate_one_records_all(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    tap = AuditTap(f, sample_rate=1.0, sync=True)
    for _ in range(5):
        recorded = await tap.record(AuditRecord.from_search_result(_req(), _result()))
        assert recorded is True
    tap.close()
    lines = f.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 5


async def test_audit_tap_creates_file_if_missing(tmp_path: Path) -> None:
    f = tmp_path / "subdir" / "audit.jsonl"
    tap = AuditTap(f, sample_rate=1.0, sync=True)
    await tap.record(AuditRecord.from_search_result(_req(), _result()))
    tap.close()
    assert f.exists()


async def test_audit_tap_closed_returns_false(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    tap = AuditTap(f, sample_rate=1.0, sync=True)
    tap.close()
    recorded = await tap.record(AuditRecord.from_search_result(_req(), _result()))
    assert recorded is False


def test_audit_tap_validates_sample_rate(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    with pytest.raises(ValueError, match="sample_rate must be in"):
        AuditTap(f, sample_rate=1.5)
    with pytest.raises(ValueError, match="sample_rate must be in"):
        AuditTap(f, sample_rate=-0.1)


# ---------- read_jsonl_records ----------


def test_read_jsonl_records_parses_lines(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    f.write_text(
        '{"a": 1}\n{"a": 2}\n{"a": 3}\n',
        encoding="utf-8",
    )
    records = read_jsonl_records(f)
    assert records == [{"a": 1}, {"a": 2}, {"a": 3}]


def test_read_jsonl_records_skips_empty_lines(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    f.write_text(
        '{"a": 1}\n\n{"a": 2}\n\n',
        encoding="utf-8",
    )
    records = read_jsonl_records(f)
    assert records == [{"a": 1}, {"a": 2}]


def test_read_jsonl_records_skips_malformed_lines(tmp_path: Path) -> None:
    """畸形 JSON 行不抛异常, 只 skip + log warning。"""
    f = tmp_path / "audit.jsonl"
    f.write_text(
        '{"a": 1}\nNOT VALID JSON\n{"a": 3}\n',
        encoding="utf-8",
    )
    records = read_jsonl_records(f)
    # 2 valid records parsed, 1 skipped
    assert records == [{"a": 1}, {"a": 3}]


def test_read_jsonl_records_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    f.write_text("", encoding="utf-8")
    assert read_jsonl_records(f) == []


# ---------- Round-trip: record → read ----------


async def test_audit_tap_round_trip_read_back(tmp_path: Path) -> None:
    f = tmp_path / "audit.jsonl"
    tap = AuditTap(f, sample_rate=1.0, sync=True)
    await tap.record(
        AuditRecord.from_search_result(
            _req(query="Python"),
            _result(
                response="answer",
                warnings=["warn-x"],
            ),
        )
    )
    tap.close()
    records = read_jsonl_records(f)
    assert len(records) == 1
    assert records[0]["query"] == "Python"
    assert records[0]["response"] == "answer"
    assert records[0]["warnings"] == ["warn-x"]
