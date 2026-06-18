"""RagasBackend: 真实 RAGAS (ragas 0.3.9) backend。

- ``faithfulness``: LLM-as-judge 校验 answer claim 是否在 context 中。
- ``answer_relevancy``: query/answer embedding 余弦相似度均值。
- ``context_precision``: 位置感知的 retrieved vs reference 精度。

通过 ``SingleTurnSample`` + ``single_turn_score`` 异步接口计算。
单指标失败时该键缺失, 不抛异常, 行为与 ``NaiveBackend`` 对齐。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
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
    """供 ragas 包装的最小 LangChain ``BaseChatModel`` 接口。"""

    async def ainvoke(self, *args: object, **kwargs: object) -> object: ...


class LangChainEmbeddings(Protocol):
    """最小 LangChain ``Embeddings`` 接口。"""

    async def aembed_query(self, text: str) -> list[float]: ...
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


class RagasBackend:
    """在单个 (query, answer, contexts) 样本上计算真实 RAGAS 指标。

    使用 LangChain chat 模型驱动 faithfulness 的 LLM judge,
    LangChain embeddings 驱动 answer_relevancy 与 context_precision。

    Args:
        llm: LangChain ``BaseChatModel``, 用于 LLM-judge 类指标。
        embeddings: LangChain ``Embeddings``, 用于 embedding 类指标。
    """

    name: str = "ragas"

    def __init__(self, llm: LangChainLLM, embeddings: LangChainEmbeddings) -> None:
        wrapped_llm = LangchainLLMWrapper(llm)
        wrapped_embeddings = LangchainEmbeddingsWrapper(embeddings)

        self._faithfulness = faithfulness
        self._faithfulness.llm = wrapped_llm

        self._answer_relevancy = answer_relevancy
        self._answer_relevancy.llm = wrapped_llm
        self._answer_relevancy.embeddings = wrapped_embeddings

        self._context_precision = context_precision
        # ragas 0.3 中 context_precision 只需 LLM, 无 embeddings 字段。
        self._context_precision.llm = wrapped_llm

    async def compute(
        self,
        *,
        query: str,
        answer: str,
        contexts: list[str],
        reference: str = "",
        retrieved_chunk_ids: list[str] | None = None,  # noqa: ARG002
        ground_truth_chunk_ids: list[str] | None = None,  # noqa: ARG002
        **_: object,
    ) -> dict[str, float]:
        """计算 3 项 RAGAS 指标。失败项在返回值中缺失。

        Args:
            query: 用户原始 query。
            answer: pipeline 生成的答案。
            contexts: 喂给 LLM 的上下文文本列表。
            reference: ground truth 答案。
            retrieved_chunk_ids: 当前 backend 不使用 (LLM judge 内部处理)。
            ground_truth_chunk_ids: 当前 backend 不使用。

        Returns:
            ``{faithfulness, answer_relevancy, context_precision}`` 子集。
        """
        sample = SingleTurnSample(
            user_input=query,
            response=answer,
            retrieved_contexts=contexts,
            reference=reference,
        )
        out: dict[str, float] = {}

        for name, metric in (
            ("faithfulness", self._faithfulness),
            ("answer_relevancy", self._answer_relevancy),
            ("context_precision", self._context_precision),
        ):
            try:
                # cast: ragas 将 single_turn_score 标为返回 Any, 收窄为
                # Awaitable[float] 以便 mypy 通过 await。
                score = await cast(Awaitable[Any], metric.single_turn_score(sample))
                out[name] = float(score)
            except Exception as e:
                logger.warning(
                    "RAGAS %s failed for query=%r: %r",
                    name,
                    query,
                    e,
                )
        return out
