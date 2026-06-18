"""Unit tests for ``rag.eval.backends.ragas.RagasBackend`` (5i-real 重写).

覆盖 (与原 test_ragas_real 一一对应):
- compute() 调用全部 3 项指标
- SingleTurnSample 字段传递正确
- 返回值强转为 float
- 单指标失败不影响其他指标
- empty retrieved_contexts 仍可运行
- __init__ 把 LLM 绑定到 faithfulness / context_precision
- __init__ 把 embeddings 绑定到 answer_relevancy
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag.eval.backends.ragas import RagasBackend


def _fake_llm() -> MagicMock:
    return MagicMock()


def _fake_embeddings() -> MagicMock:
    return MagicMock()


# ---------- RagasBackend.compute ----------


@pytest.mark.asyncio
async def test_compute_calls_all_three_metrics() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())

    with (
        patch.object(
            runner._faithfulness,
            "single_turn_score",
            new=AsyncMock(return_value=0.9),
        ) as mock_f,
        patch.object(
            runner._answer_relevancy,
            "single_turn_score",
            new=AsyncMock(return_value=0.8),
        ) as mock_a,
        patch.object(
            runner._context_precision,
            "single_turn_score",
            new=AsyncMock(return_value=0.7),
        ) as mock_c,
    ):
        result = await runner.compute(
            query="q",
            answer="a",
            contexts=["c1", "c2"],
            reference="ref",
        )

    assert result["faithfulness"] == 0.9
    assert result["answer_relevancy"] == 0.8
    assert result["context_precision"] == 0.7
    mock_f.assert_awaited_once()
    mock_a.assert_awaited_once()
    mock_c.assert_awaited_once()


@pytest.mark.asyncio
async def test_compute_passes_correct_sample() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())

    captured: list = []

    async def _capture(sample: object) -> float:
        captured.append(sample)
        return 0.5

    with patch.object(runner._faithfulness, "single_turn_score", new=_capture):
        await runner.compute(
            query="test query",
            answer="test answer",
            contexts=["ctx1", "ctx2"],
            reference="ref text",
        )

    assert len(captured) == 1
    sample = captured[0]
    assert sample.user_input == "test query"
    assert sample.response == "test answer"
    assert sample.retrieved_contexts == ["ctx1", "ctx2"]
    assert sample.reference == "ref text"


@pytest.mark.asyncio
async def test_compute_coerces_to_float() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())

    fake_score = MagicMock()
    fake_score.__float__ = lambda self: 0.42
    with patch.object(
        runner._faithfulness,
        "single_turn_score",
        new=AsyncMock(return_value=fake_score),
    ):
        result = await runner.compute(query="q", answer="a", contexts=[])
    assert result["faithfulness"] == 0.42
    assert isinstance(result["faithfulness"], float)


@pytest.mark.asyncio
async def test_compute_per_metric_failure_does_not_fail_others() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())

    with (
        patch.object(
            runner._faithfulness,
            "single_turn_score",
            new=AsyncMock(side_effect=RuntimeError("LLM down")),
        ),
        patch.object(
            runner._answer_relevancy,
            "single_turn_score",
            new=AsyncMock(return_value=0.5),
        ),
        patch.object(
            runner._context_precision,
            "single_turn_score",
            new=AsyncMock(return_value=0.6),
        ),
    ):
        result = await runner.compute(query="q", answer="a", contexts=[])

    assert "faithfulness" not in result
    assert result["answer_relevancy"] == 0.5
    assert result["context_precision"] == 0.6


@pytest.mark.asyncio
async def test_compute_empty_contexts() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())

    with (
        patch.object(
            runner._faithfulness,
            "single_turn_score",
            new=AsyncMock(return_value=1.0),
        ),
        patch.object(
            runner._answer_relevancy,
            "single_turn_score",
            new=AsyncMock(return_value=0.0),
        ),
        patch.object(
            runner._context_precision,
            "single_turn_score",
            new=AsyncMock(return_value=0.0),
        ),
    ):
        result = await runner.compute(query="q", answer="a", contexts=[])

    assert result["faithfulness"] == 1.0
    assert result["answer_relevancy"] == 0.0


# ---------- Initialization ----------


def test_init_binds_llm_to_faithfulness() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())
    assert runner._faithfulness.llm is not None


def test_init_binds_embeddings_to_answer_relevancy() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())
    assert runner._answer_relevancy.embeddings is not None


def test_init_binds_llm_to_context_precision() -> None:
    runner = RagasBackend(llm=_fake_llm(), embeddings=_fake_embeddings())
    assert runner._context_precision.llm is not None
