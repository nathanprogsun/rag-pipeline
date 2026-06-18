"""Unit tests for ``rag.eval.backends.naive.NaiveBackend`` (5i naive 重写).

覆盖:
- 分词 (拉丁词 / CJK 逐字 / 标点剥离 / 大小写)
- faithfulness 启发式 (空 / 全覆盖 / 部分覆盖 / 中文 / 多 context 合并)
- answer_relevance Jaccard
- context_precision rank-unaware
"""

from __future__ import annotations

import pytest

from rag.eval.backends.naive import (
    NaiveBackend,
    _naive_answer_relevance,
    _naive_context_precision,
    _naive_faithfulness,
    _split_into_claims,
    _tokenize,
)

# ---------- _tokenize ----------


def test_tokenize_empty() -> None:
    assert _tokenize("") == set()


def test_tokenize_lowercases() -> None:
    assert _tokenize("Hello WORLD") == {"hello", "world"}


def test_tokenize_chinese() -> None:
    """CJK tokenization splits into per-character tokens (not combined runs)."""
    tokens = _tokenize("Python 教程")
    assert "python" in tokens
    assert "教" in tokens
    assert "程" in tokens


def test_tokenize_punctuation_stripped() -> None:
    assert _tokenize("hello, world!") == {"hello", "world"}


# ---------- faithfulness ----------


def test_faithfulness_empty_answer() -> None:
    assert _naive_faithfulness("", ["some context"]) == 1.0


def test_faithfulness_empty_context() -> None:
    assert _naive_faithfulness("Python is great", []) == 0.0


def test_faithfulness_all_claims_in_context() -> None:
    answer = "Python is great. Lists are useful."
    context = ["Python is great and lists are very useful in programming"]
    assert _naive_faithfulness(answer, context) == 1.0


def test_faithfulness_some_claims_out_of_context() -> None:
    answer = "Python is great. JavaScript is unrelated."
    context = ["Python is great for scripting"]
    assert _naive_faithfulness(answer, context) == 0.5


def test_faithfulness_no_claims_in_context() -> None:
    answer = "Mars has two moons. Jupiter is huge."
    context = ["Python is great for data science"]
    assert _naive_faithfulness(answer, context) == 0.0


def test_faithfulness_chinese_claims_in_chinese_context() -> None:
    answer = "Python 教程. 列表推导式."
    context = ["Python 教程讲解列表推导式的简洁用法"]
    assert _naive_faithfulness(answer, context) == 1.0


def test_faithfulness_chinese_partial() -> None:
    answer = "Python 教程很好. 列表推导式简洁."  # 很好 not in context
    context = ["Python 教程讲解列表推导式简洁用法"]
    assert _naive_faithfulness(answer, context) == 0.5


def test_faithfulness_multiple_contexts_union() -> None:
    answer = "Python Java."
    context = ["Python", "Java"]
    assert _naive_faithfulness(answer, context) == 1.0


# ---------- answer_relevance ----------


def test_answer_relevance_identical() -> None:
    assert _naive_answer_relevance("python tutorial", "Python tutorial") == 1.0


def test_answer_relevance_disjoint() -> None:
    assert _naive_answer_relevance("apple banana", "cat dog") == 0.0


def test_answer_relevance_partial() -> None:
    assert _naive_answer_relevance("a b c", "b c d e") == pytest.approx(2 / 5)


def test_answer_relevance_empty_query() -> None:
    assert _naive_answer_relevance("", "answer") == 0.0


def test_answer_relevance_empty_answer() -> None:
    assert _naive_answer_relevance("query", "") == 0.0


def test_answer_relevance_case_insensitive() -> None:
    assert _naive_answer_relevance("PYTHON", "python") == 1.0


# ---------- context_precision ----------


def test_context_precision_perfect() -> None:
    assert _naive_context_precision(["a", "b", "c"], {"a", "b", "c"}) == 1.0


def test_context_precision_partial() -> None:
    assert _naive_context_precision(["a", "b", "x"], {"a", "b"}) == pytest.approx(2 / 3)


def test_context_precision_zero() -> None:
    assert _naive_context_precision(["x", "y"], {"a", "b"}) == 0.0


def test_context_precision_empty_retrieved() -> None:
    assert _naive_context_precision([], {"a"}) == 1.0


def test_context_precision_empty_gt() -> None:
    assert _naive_context_precision(["a"], set()) == 0.0


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
    assert len(_split_into_claims("Real claim.. Another claim.")) == 2


# ---------- NaiveBackend.compute ----------


async def test_naive_backend_returns_all_three_metrics() -> None:
    backend = NaiveBackend()
    result = await backend.compute(
        query="python",
        answer="Python is great. Lists are useful.",
        contexts=["Python is great and lists are very useful"],
        retrieved_chunk_ids=["a", "b"],
        ground_truth_chunk_ids=["a"],
    )
    assert set(result.keys()) == {
        "faithfulness",
        "answer_relevance",
        "context_precision",
    }


async def test_naive_backend_respects_supported_subset() -> None:
    backend = NaiveBackend(supported_metrics=["faithfulness"])
    result = await backend.compute(
        query="q",
        answer="a",
        contexts=["c"],
        retrieved_chunk_ids=["x"],
        ground_truth_chunk_ids=["y"],
    )
    assert set(result.keys()) == {"faithfulness"}


async def test_naive_backend_skips_context_precision_without_retrieved() -> None:
    backend = NaiveBackend()
    result = await backend.compute(
        query="q",
        answer="a",
        contexts=["c"],
        retrieved_chunk_ids=None,
        ground_truth_chunk_ids=[],
    )
    assert "context_precision" not in result
