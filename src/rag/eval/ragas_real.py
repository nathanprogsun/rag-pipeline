"""Real RAGAS metrics (ragas>=0.3,<0.4) — v2 replacement for stubs.

Per `.agents/design/2026-06-14-cross-task-contracts.md` task 19:

> Real RAGAS `faithfulness` with custom LLM judge (task 19 ships a stub
> for v2; see G-P0-2)

This module wraps real ragas 0.3.9 metrics behind a clean callable
interface that mirrors the stub API. Each metric takes the same args
as the stub but returns the same ``0-1`` float range.

Three metrics implemented (same names as stubs):
- ``faithfulness_real``: LLM-as-judge checks answer claims all in context
- ``answer_relevancy_real``: cosine(query_embed, answer_embed) averaged
- ``context_precision_real``: position-aware precision of retrieved vs reference

The ragas 0.3.9 API uses ``SingleTurnSample`` and ``single_turn_score``
async method. Each metric needs an LLM (faithfulness) or embeddings
(answer_relevancy, context_precision) initialized.

Usage::

    from rag.eval.ragas_real import RagasRealRunner

    runner = RagasRealRunner(
        llm=my_langchain_llm,
        embeddings=my_langchain_embeddings,
    )
    scores = await runner.compute(
        user_input="...",
        response="...",
        retrieved_contexts=["c1", "c2"],
        reference="ground truth answer",
    )
    # scores = {"faithfulness": 0.85, "answer_relevancy": 0.72, ...}
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from ragas.dataset_schema import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    faithfulness,
)

logger = logging.getLogger(__name__)


class LangChainLLM(Protocol):
    """Minimal LangChain BaseChatModel interface for ragas wrapping."""

    async def ainvoke(self, *args: object, **kwargs: object) -> object: ...


class LangChainEmbeddings(Protocol):
    """Minimal LangChain Embeddings interface."""

    async def aembed_query(self, text: str) -> list[float]: ...
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class RagasRealRunner:
    """Compute real RAGAS metrics over a single (query, answer, contexts) tuple.

    Wraps ragas 0.3.9 metrics with a LangChain chat model (for faithfulness
    LLM judge) and a LangChain embeddings model (for answer_relevancy +
    context_precision).

    Args:
        llm: LangChain ``BaseChatModel`` for LLM-judge metrics.
        embeddings: LangChain ``Embeddings`` for embedding-based metrics.
    """

    llm: LangChainLLM
    embeddings: LangChainEmbeddings

    def __post_init__(self) -> None:
        """Bind LLM + embeddings to all metrics (ragas 0.3 requirement)."""
        wrapped_llm = LangchainLLMWrapper(self.llm)
        wrapped_embeddings = LangchainEmbeddingsWrapper(self.embeddings)

        self._faithfulness = faithfulness
        self._faithfulness.llm = wrapped_llm

        self._answer_relevancy = answer_relevancy
        self._answer_relevancy.llm = wrapped_llm
        self._answer_relevancy.embeddings = wrapped_embeddings

        self._context_precision = context_precision
        # context_precision in ragas 0.3 is LLM-only (no embeddings attribute).
        self._context_precision.llm = wrapped_llm

    async def compute(
        self,
        *,
        user_input: str,
        response: str,
        retrieved_contexts: list[str],
        reference: str = "",
    ) -> dict[str, float]:
        """Compute all 3 metrics for one sample.

        Returns dict with keys: ``faithfulness``, ``answer_relevancy``,
        ``context_precision``. Each value in ``[0, 1]``. Missing keys if
        a metric raises (logged + skipped).
        """
        sample = SingleTurnSample(
            user_input=user_input,
            response=response,
            retrieved_contexts=retrieved_contexts,
            reference=reference,
        )
        out: dict[str, float] = {}

        for name, metric in (
            ("faithfulness", self._faithfulness),
            ("answer_relevancy", self._answer_relevancy),
            ("context_precision", self._context_precision),
        ):
            try:
                # cast: ragas types single_turn_score as returning Any (not
                # Awaitable); narrow to Awaitable[float] so mypy accepts await.
                score = await cast(
                    Awaitable[Any], metric.single_turn_score(sample)
                )
                out[name] = float(score)
            except Exception as e:
                logger.warning(
                    "RAGAS %s failed for query=%r: %r",
                    name,
                    user_input,
                    e,
                )
        return out