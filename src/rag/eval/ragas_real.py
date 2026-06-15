"""真实 RAGAS 指标 (ragas 0.3.9) 包装, 接口与 stub 对称。

- ``faithfulness``: LLM-as-judge 校验 answer claim 是否在 context 中
- ``answer_relevancy``: query/answer embedding 余弦相似度均值
- ``context_precision``: 位置感知的 retrieved vs reference 精度

通过 ``SingleTurnSample`` + ``single_turn_score`` 异步接口计算。
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
    """供 ragas 包装的最小 LangChain ``BaseChatModel`` 接口。"""

    async def ainvoke(self, *args: object, **kwargs: object) -> object: ...


class LangChainEmbeddings(Protocol):
    """最小 LangChain ``Embeddings`` 接口。"""

    async def aembed_query(self, text: str) -> list[float]: ...
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]: ...


@dataclass
class RagasRealRunner:
    """在单个 (query, answer, contexts) 样本上计算真实 RAGAS 指标。

    使用 LangChain chat 模型驱动 faithfulness 的 LLM judge,
    LangChain embeddings 驱动 answer_relevancy 与 context_precision。

    Args:
        llm: LangChain ``BaseChatModel``, 用于 LLM-judge 类指标。
        embeddings: LangChain ``Embeddings``, 用于 embedding 类指标。
    """

    llm: LangChainLLM
    embeddings: LangChainEmbeddings

    def __post_init__(self) -> None:
        """将 LLM 与 embeddings 绑定到各指标 (ragas 0.3 要求)。"""
        wrapped_llm = LangchainLLMWrapper(self.llm)
        wrapped_embeddings = LangchainEmbeddingsWrapper(self.embeddings)

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
        user_input: str,
        response: str,
        retrieved_contexts: list[str],
        reference: str = "",
    ) -> dict[str, float]:
        """计算单个样本的 3 项指标。

        Returns:
            ``{faithfulness, answer_relevancy, context_precision}`` 字典,
            各值范围 ``[0, 1]``。指标异常时该键缺失 (记录 warning 后跳过)。
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
                # cast: ragas 将 single_turn_score 标为返回 Any, 收窄为
                # Awaitable[float] 以便 mypy 通过 await。
                score = await cast(Awaitable[Any], metric.single_turn_score(sample))
                out[name] = float(score)
            except Exception as e:
                logger.warning(
                    "RAGAS %s failed for query=%r: %r",
                    name,
                    user_input,
                    e,
                )
        return out
