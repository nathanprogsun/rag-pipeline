"""Unit tests for ``rag.eval.ragas_metrics`` and ``ragas_runner`` (5i).

Tests cover RAGAS-stub implementations:
- faithfulness_stub: hallucination heuristic
- answer_relevance_stub: Jaccard similarity
- context_precision_stub: retrieved ∩ ground_truth / |retrieved|
- RagasRunner: JSONL orchestration, aggregation, output JSON
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from rag.eval.ragas_metrics import (
    _split_into_claims,
    _tokenize,
    answer_relevance_stub,
    context_precision_stub,
    faithfulness_stub,
)
from rag.eval.ragas_runner import (
    RagasRecord,
    RagasRunner,
    RagasSummary,
    _compute_ragas_metrics,
    _load_jsonl,
)

# ---------- _tokenize ----------


def test_tokenize_empty() -> None:
    assert _tokenize("") == set()


def test_tokenize_lowercases() -> None:
    assert _tokenize("Hello WORLD") == {"hello", "world"}


def test_tokenize_chinese() -> None:
    """CJK tokenization splits into per-character tokens (not combined runs).

    Per-character CJK tokens enable subset-based presence checks against
    context tokenized the same way. ``教程`` is split into ``{"教", "程"}``.
    """
    tokens = _tokenize("Python 教程")
    assert "python" in tokens
    assert "教" in tokens
    assert "程" in tokens


def test_tokenize_punctuation_stripped() -> None:
    assert _tokenize("hello, world!") == {"hello", "world"}


# ---------- faithfulness_stub ----------


def test_faithfulness_empty_answer() -> None:
    """No claims → vacuously faithful (1.0)."""
    assert faithfulness_stub("", ["some context"]) == 1.0


def test_faithfulness_empty_context() -> None:
    """No evidence → 0.0."""
    assert faithfulness_stub("Python is great", []) == 0.0


def test_faithfulness_all_claims_in_context() -> None:
    """Every claim's tokens appear in context → 1.0."""
    answer = "Python is great. Lists are useful."
    context = ["Python is great and lists are very useful in programming"]
    assert faithfulness_stub(answer, context) == 1.0


def test_faithfulness_some_claims_out_of_context() -> None:
    """1 of 2 claims has tokens not in context → 0.5."""
    answer = "Python is great. JavaScript is unrelated."
    context = ["Python is great for scripting"]
    assert faithfulness_stub(answer, context) == 0.5


def test_faithfulness_no_claims_in_context() -> None:
    """All claims out of context → 0.0."""
    answer = "Mars has two moons. Jupiter is huge."
    context = ["Python is great for data science"]
    assert faithfulness_stub(answer, context) == 0.0


def test_faithfulness_chinese_claims_in_chinese_context() -> None:
    """Chinese claims fully covered by Chinese context."""
    answer = "Python 教程. 列表推导式."
    context = ["Python 教程讲解列表推导式的简洁用法"]
    # Claim 1: {python, 教程} ⊂ context
    # Claim 2: {列表, 推导, 式} ⊂ context
    # Both → faithfulness=1.0
    assert faithfulness_stub(answer, context) == 1.0


def test_faithfulness_chinese_partial() -> None:
    """1 of 2 Chinese claims has tokens not in context → 0.5."""
    answer = "Python 教程很好. 列表推导式简洁."  # 很好 not in context
    context = ["Python 教程讲解列表推导式简洁用法"]
    assert faithfulness_stub(answer, context) == 0.5


def test_faithfulness_multiple_contexts() -> None:
    """Contexts are unioned before checking claim tokens."""
    answer = "Python and Java are programming languages."
    context = ["Python is a language.", "Java is also a language."]
    # claim tokens: {python, and, java, are, programming, languages}
    # union context: {python, is, a, language, java, also}
    # "and", "are", "programming" missing → not subset → faithfulness < 1.0
    assert faithfulness_stub(answer, context) == 0.0


def test_faithfulness_multiple_contexts_union_works() -> None:
    """Verify that ALL context tokens are pooled (union).

    answer = "Python Java" (claim tokens: {python, java})
    ctx1 = "Python" (tokens: {python})
    ctx2 = "Java" (tokens: {java})
    union = {python, java} → claim is subset → 1.0
    """
    answer = "Python Java."
    context = ["Python", "Java"]
    assert faithfulness_stub(answer, context) == 1.0


# ---------- answer_relevance_stub ----------


def test_answer_relevance_identical() -> None:
    """Same tokens → Jaccard = 1.0."""
    assert answer_relevance_stub("python tutorial", "Python tutorial") == 1.0


def test_answer_relevance_disjoint() -> None:
    """No overlap → Jaccard = 0.0."""
    assert answer_relevance_stub("apple banana", "cat dog") == 0.0


def test_answer_relevance_partial() -> None:
    """3 tokens in q, 4 in a, 2 overlap → 2/(3+4-2) = 2/5 = 0.4."""
    q = "a b c"
    a = "b c d e"
    assert answer_relevance_stub(q, a) == pytest.approx(2 / 5)


def test_answer_relevance_empty_query() -> None:
    assert answer_relevance_stub("", "answer") == 0.0


def test_answer_relevance_empty_answer() -> None:
    assert answer_relevance_stub("query", "") == 0.0


def test_answer_relevance_case_insensitive() -> None:
    assert answer_relevance_stub("PYTHON", "python") == 1.0


# ---------- context_precision_stub ----------


def test_context_precision_perfect() -> None:
    assert context_precision_stub(["a", "b", "c"], {"a", "b", "c"}) == 1.0


def test_context_precision_partial() -> None:
    """2 of 3 retrieved in gt → 2/3."""
    assert context_precision_stub(["a", "b", "x"], {"a", "b"}) == pytest.approx(2 / 3)


def test_context_precision_zero() -> None:
    assert context_precision_stub(["x", "y"], {"a", "b"}) == 0.0


def test_context_precision_empty_retrieved() -> None:
    """Vacuous: no retrieved → 1.0."""
    assert context_precision_stub([], {"a"}) == 1.0


def test_context_precision_empty_gt() -> None:
    """No ground truth → 0.0 (no signal)."""
    assert context_precision_stub(["a"], set()) == 0.0


# ---------- _split_into_claims ----------


def test_split_claims_sentence_boundary() -> None:
    claims = _split_into_claims("First claim. Second claim. Third claim.")
    assert len(claims) == 3


def test_split_claims_chinese_punctuation() -> None:
    claims = _split_into_claims("第一句。第二句！第三句？")
    assert len(claims) == 3


def test_split_claims_newline_boundary() -> None:
    claims = _split_into_claims("Line one\nLine two\nLine three")
    assert len(claims) == 3


def test_split_claims_empty() -> None:
    assert _split_into_claims("") == []


def test_split_claims_filters_empty_strings() -> None:
    """Empty parts (e.g. from consecutive periods) skipped."""
    claims = _split_into_claims("Real claim.. Another claim.")
    assert len(claims) == 2


# ---------- RagasRecord ----------


def test_ragas_record_minimal() -> None:
    rec = RagasRecord(query="test")
    assert rec.answer == ""
    assert rec.contexts == []
    assert rec.retrieved_chunk_ids == []
    assert rec.ground_truth_chunk_ids == []


def test_ragas_record_full() -> None:
    chunk_id = uuid.uuid4()
    rec = RagasRecord(
        query="Python 教程",
        answer="Python 教程很好",
        contexts=["Python 教程讲解列表推导式"],
        retrieved_chunk_ids=[str(chunk_id)],
        ground_truth_chunk_ids=[chunk_id],
        metadata={"source": "test"},
    )
    assert rec.query == "Python 教程"
    assert rec.answer == "Python 教程很好"
    assert rec.contexts == ["Python 教程讲解列表推导式"]
    assert rec.metadata == {"source": "test"}


# ---------- _load_jsonl ----------


def test_load_ragas_jsonl_parses(tmp_path: Path) -> None:
    f = tmp_path / "ragas.jsonl"
    f.write_text(
        json.dumps(
            {
                "query": "q1",
                "answer": "a1",
                "contexts": ["c1"],
                "retrieved_chunk_ids": [],
                "ground_truth_chunk_ids": [str(uuid.uuid4())],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    records = _load_jsonl(f)
    assert len(records) == 1
    assert records[0].query == "q1"


def test_load_ragas_jsonl_skips_malformed(tmp_path: Path) -> None:
    f = tmp_path / "ragas.jsonl"
    f.write_text('{"query":"q1"}\nNOT VALID\n{"query":"q2"}\n', encoding="utf-8")
    records = _load_jsonl(f)
    assert len(records) == 2


def test_load_ragas_jsonl_empty(tmp_path: Path) -> None:
    f = tmp_path / "ragas.jsonl"
    f.write_text("", encoding="utf-8")
    assert _load_jsonl(f) == []


# ---------- _compute_ragas_metrics ----------


def test_compute_ragas_metrics_all_three() -> None:
    metrics = _compute_ragas_metrics(
        answer="Python is great",
        query="Python",
        contexts=["Python is great for scripting"],
        retrieved_chunk_ids=["a", "b"],
        ground_truth_chunk_ids=["a"],
        metrics=["faithfulness", "answer_relevance", "context_precision"],
    )
    assert set(metrics.keys()) == {
        "faithfulness",
        "answer_relevance",
        "context_precision",
    }


def test_compute_ragas_metrics_unknown_skipped() -> None:
    metrics = _compute_ragas_metrics(
        answer="x",
        query="y",
        contexts=["z"],
        retrieved_chunk_ids=[],
        ground_truth_chunk_ids=[],
        metrics=["faithfulness", "unknown_metric"],
    )
    assert "faithfulness" in metrics
    assert "unknown_metric" not in metrics


# ---------- RagasRunner.run ----------


def test_ragas_runner_empty_dataset(tmp_path: Path) -> None:
    f = tmp_path / "ragas.jsonl"
    f.write_text("", encoding="utf-8")
    runner = RagasRunner()
    summary = runner.run(f)
    assert isinstance(summary, RagasSummary)
    assert summary.sample_count == 0
    assert summary.warnings == ["empty dataset"]


def test_ragas_runner_runs_per_record(tmp_path: Path) -> None:
    gt_a = uuid.uuid4()
    gt_b = uuid.uuid4()
    f = tmp_path / "ragas.jsonl"
    records_data = [
        {
            "query": "Python 教程",
            "answer": "Python 教程讲解列表推导式",
            "contexts": ["Python 教程讲解列表推导式的简洁用法"],
            "retrieved_chunk_ids": [str(gt_a)],
            "ground_truth_chunk_ids": [str(gt_a)],
        },
        {
            "query": "Java 静态类型",
            "answer": "Java 编译型语言",
            "contexts": ["Java 是编译型静态类型语言"],
            "retrieved_chunk_ids": [str(gt_b), str(uuid.uuid4())],
            "ground_truth_chunk_ids": [str(gt_b)],
        },
    ]
    with f.open("w", encoding="utf-8") as fp:
        for r in records_data:
            fp.write(json.dumps(r) + "\n")

    runner = RagasRunner()
    summary = runner.run(f)

    assert summary.sample_count == 2
    # All 3 metrics present
    assert set(summary.metric_aggregates.keys()) == {
        "faithfulness",
        "answer_relevance",
        "context_precision",
    }
    # Each metric has aggregate fields
    for _metric_name, agg in summary.metric_aggregates.items():
        assert "mean" in agg
        assert "count" in agg
        assert agg["count"] == 2


def test_ragas_runner_writes_output_json(tmp_path: Path) -> None:
    f = tmp_path / "ragas.jsonl"
    f.write_text(
        json.dumps(
            {
                "query": "q",
                "answer": "a",
                "contexts": ["c"],
                "retrieved_chunk_ids": [],
                "ground_truth_chunk_ids": [],
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "ragas_summary.json"
    runner = RagasRunner()

    runner.run(f, output_path=output_path)

    assert output_path.exists()
    parsed = json.loads(output_path.read_text(encoding="utf-8"))
    assert parsed["sample_count"] == 1
    assert "faithfulness" in parsed["metric_aggregates"]


def test_ragas_runner_metric_failure_captured(tmp_path: Path) -> None:
    """Edge case triggering failure: empty answer + empty contexts → faithfulness=0.0
    (not a failure, but verify the runner handles edge cases gracefully)."""
    f = tmp_path / "ragas.jsonl"
    f.write_text(
        json.dumps(
            {
                "query": "",
                "answer": "",
                "contexts": [],
                "retrieved_chunk_ids": [],
                "ground_truth_chunk_ids": [],
            }
        ),
        encoding="utf-8",
    )
    runner = RagasRunner()
    summary = runner.run(f)
    # No failure (just zero values); sample_count=1
    assert summary.sample_count == 1
    assert summary.warnings == []
