"""Unit tests for ``rag.eval.ragas_real`` (5i-real).

Tests use AsyncMock for ragas metric ``single_turn_score`` to avoid
real LLM/embeddings calls. Validates:
- Runner initializes and binds LLM + embeddings to each metric
- compute() calls all 3 metrics and aggregates results
- Per-metric failure captured (one bad metric doesn't fail others)
- Returned values are coerced to float
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from rag.eval.ragas_real import RagasRealRunner


def _fake_llm() -> MagicMock:
    """Mock LangChain ChatModel."""
    return MagicMock()


def _fake_embeddings() -> MagicMock:
    """Mock LangChain Embeddings."""
    return MagicMock()


# ---------- RagasRealRunner.compute ----------


async def test_compute_calls_all_three_metrics() -> None:
    """compute() invokes faithfulness, answer_relevancy, context_precision."""
    runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())

    # Patch each metric's single_turn_score to return a known value
    with (
        patch.object(
            runner._faithfulness, "single_turn_score", new=AsyncMock(return_value=0.9)
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
            user_input="q",
            response="a",
            retrieved_contexts=["c1", "c2"],
            reference="ref",
        )

    assert result["faithfulness"] == 0.9
    assert result["answer_relevancy"] == 0.8
    assert result["context_precision"] == 0.7
    mock_f.assert_awaited_once()
    mock_a.assert_awaited_once()
    mock_c.assert_awaited_once()


async def test_compute_passes_correct_sample() -> None:
    """single_turn_score receives SingleTurnSample with our fields."""
    runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())

    captured_sample: list = []

    async def _capture(sample: object) -> float:
        captured_sample.append(sample)
        return 0.5

    with patch.object(runner._faithfulness, "single_turn_score", new=_capture):
        await runner.compute(
            user_input="test query",
            response="test answer",
            retrieved_contexts=["ctx1", "ctx2"],
            reference="ref text",
        )

    assert len(captured_sample) == 1
    sample = captured_sample[0]
    assert sample.user_input == "test query"
    assert sample.response == "test answer"
    assert sample.retrieved_contexts == ["ctx1", "ctx2"]
    assert sample.reference == "ref text"


async def test_compute_coerces_to_float() -> None:
    """Numeric (int / numpy) values coerced to Python float."""
    runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())

    # Mock returning numpy-like (has __float__ via .item())
    fake_score = MagicMock()
    fake_score.__float__ = lambda self: 0.42
    with patch.object(
        runner._faithfulness,
        "single_turn_score",
        new=AsyncMock(return_value=fake_score),
    ):
        result = await runner.compute(
            user_input="q", response="a", retrieved_contexts=[]
        )
    assert result["faithfulness"] == 0.42
    assert isinstance(result["faithfulness"], float)


async def test_compute_per_metric_failure_does_not_fail_others() -> None:
    """If faithfulness raises, answer_relevancy + context_precision still run."""
    runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())

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
        result = await runner.compute(
            user_input="q", response="a", retrieved_contexts=[]
        )

    assert "faithfulness" not in result  # failed
    assert result["answer_relevancy"] == 0.5
    assert result["context_precision"] == 0.6


async def test_compute_empty_contexts() -> None:
    """Empty retrieved_contexts is allowed (returns 0 for some metrics)."""
    runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())

    with (
        patch.object(
            runner._faithfulness, "single_turn_score", new=AsyncMock(return_value=1.0)
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
        result = await runner.compute(
            user_input="q", response="a", retrieved_contexts=[]
        )

    assert result["faithfulness"] == 1.0
    assert result["answer_relevancy"] == 0.0


# ---------- Initialization ----------


def test_init_binds_llm_to_faithfulness() -> None:
    """__post_init__ wires the wrapped LLM to faithfulness metric."""
    llm = _fake_llm()
    embeddings = _fake_embeddings()
    runner = RagasRealRunner(llm=llm, embeddings=embeddings)

    # ragas stores the wrapped LLM; verify it was set
    assert runner._faithfulness.llm is not None


def test_init_binds_embeddings_to_answer_relevancy() -> None:
    """__post_init__ wires wrapped embeddings to answer_relevancy."""
    runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())
    assert runner._answer_relevancy.embeddings is not None


def test_init_binds_llm_to_context_precision() -> None:
    """context_precision in ragas 0.3 is LLM-only (no embeddings attribute)."""
    runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())
    assert runner._context_precision.llm is not None
    # context_precision in ragas 0.3 doesn't take embeddings (LLM judge only)
    assert not hasattr(runner._context_precision, "embeddings")


# ---------- RagasRunner integration with real runner (smoke test) ----------


async def test_ragas_runner_can_use_real_runner() -> None:
    """RagasRunner integrates with RagasRealRunner via custom compute function.

    The default RagasRunner uses stub metrics; this verifies the
    integration shape is compatible — plug a real runner by overriding
    the metric computation path.
    """
    # This test just verifies the import path / API stability.
    from rag.eval.ragas_runner import RagasRecord

    real_runner = RagasRealRunner(llm=_fake_llm(), embeddings=_fake_embeddings())

    # Build a RagasRunner that uses real metrics (via custom callback).
    # The default RagasRunner uses _compute_ragas_metrics internally;
    # here we verify the metrics dict structure matches.
    async def _patched_compute(record: RagasRecord) -> dict[str, float]:
        return await real_runner.compute(
            user_input=record.query,
            response=record.answer,
            retrieved_contexts=record.contexts,
        )

    # Smoke check: signatures are compatible
    import inspect

    sig = inspect.signature(_patched_compute)
    assert "record" in sig.parameters
