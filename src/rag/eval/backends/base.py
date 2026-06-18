"""生成侧指标 backend 协议。

``GenMetricsBackend`` 是 UnifiedEvalRunner 调用生成指标时的统一接口。
不同实现 (naive / ragas / skip) 走同一协议, 由 ``EvalConfig.gen_backend`` 决定
激活哪一个, 避免在 runner 主体里出现 if-else 分支。
"""

from __future__ import annotations

from typing import Protocol


class GenMetricsBackend(Protocol):
    """生成侧指标 backend 接口。

    Implementations:
        - ``NaiveBackend``: 无 LLM, token 启发式, 供 CI smoke。
        - ``RagasBackend``: 真实 RAGAS, LLM-as-judge。
        - ``SkipBackend``: 不计算生成指标 (纯检索评估场景)。
    """

    name: str

    async def compute(
        self,
        *,
        query: str,
        answer: str,
        contexts: list[str],
        reference: str = "",
        retrieved_chunk_ids: list[str] | None = None,
        ground_truth_chunk_ids: list[str] | None = None,
    ) -> dict[str, float]:
        """计算该 backend 支持的指标子集, 返回 ``{metric_name: score ∈ [0,1]}``。

        Args:
            query: 用户原始 query。
            answer: pipeline 生成的回答。
            contexts: 喂给 LLM 的上下文 chunk 文本列表。
            reference: ground truth 答案 (用于 answer_relevance / context_precision)。
            retrieved_chunk_ids: pipeline 召回的 chunk_id 列表 (字符串化)。
            ground_truth_chunk_ids: ground truth chunk_id 列表 (字符串化)。

        Returns:
            指标名到分数的映射。任何指标计算失败, 该键直接缺失 (不抛异常)。
        """
        ...


class SkipBackend:
    """占位 backend: 永远返回空 dict, 用于纯检索评估。"""

    name: str = "skip"

    async def compute(self, **_: object) -> dict[str, float]:
        return {}
