"""Unit tests for ``rag.eval.records.EvalRecord``."""

from __future__ import annotations

import uuid


def test_eval_record_minimal_fields() -> None:
    from rag.eval.records import EvalRecord

    rec = EvalRecord(query="test")
    assert rec.dataset_ids == []
    assert rec.ground_truth_chunk_ids == []
    assert rec.k == 10
    assert rec.reference_answer == ""
    assert rec.reference_contexts == []
    assert rec.metadata == {}


def test_eval_record_full() -> None:
    from rag.eval.records import EvalRecord

    ds_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    rec = EvalRecord(
        query="Python 教程",
        dataset_ids=[ds_id],
        ground_truth_chunk_ids=[chunk_id],
        k=5,
        reference_answer="Python 教程讲解...",
        reference_contexts=["ctx1"],
        metadata={"source": "test"},
    )
    assert rec.query == "Python 教程"
    assert rec.dataset_ids == [ds_id]
    assert rec.ground_truth_chunk_ids == [chunk_id]
    assert rec.k == 5
    assert rec.reference_answer == "Python 教程讲解..."
    assert rec.reference_contexts == ["ctx1"]
    assert rec.metadata == {"source": "test"}


def test_eval_record_extra_allow() -> None:
    """额外字段不报错, 保留向后兼容。"""
    from rag.eval.records import EvalRecord

    rec = EvalRecord(query="q", extra_field="ignored")
    assert rec.query == "q"
