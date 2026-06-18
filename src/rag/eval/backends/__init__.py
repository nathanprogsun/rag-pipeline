"""生成侧 backend factory。

按 ``EvalConfig.gen_backend`` 名称装配 backend 实例, 屏蔽实现细节。
"""

from __future__ import annotations

from typing import Literal, cast

from .base import GenMetricsBackend, SkipBackend
from .naive import NaiveBackend
from .ragas import LangChainEmbeddings, LangChainLLM, RagasBackend

BackendName = Literal["naive", "ragas", "skip"]


def get_backend(name: BackendName, **deps: object) -> GenMetricsBackend:
    """按名称构造 backend。

    Args:
        name: ``naive`` / ``ragas`` / ``skip``。
        **deps: backend 构造所需依赖:
            - ``ragas`` 需要 ``llm`` 与 ``embeddings``。
            - 其他 backend 忽略。

    Returns:
        ``GenMetricsBackend`` 实例。

    Raises:
        ValueError: 未知 backend 名称。
    """
    if name == "naive":
        return NaiveBackend()
    if name == "skip":
        return SkipBackend()
    if name == "ragas":
        try:
            llm = deps["llm"]
            embeddings = deps["embeddings"]
        except KeyError as e:
            msg = f"ragas backend requires deps={e.args[0]!r}"
            raise ValueError(msg) from e
        # cast: deps 是 dict[str, object], RagasBackend 要求具体 Protocol 类型。
        # 调用方 (UnifiedEvalRunner / CLI) 保证传入正确类型, 此处 cast 收窄。
        return RagasBackend(
            llm=cast(LangChainLLM, llm),
            embeddings=cast(LangChainEmbeddings, embeddings),
        )
    msg = f"unknown gen_backend: {name!r}"
    raise ValueError(msg)


__all__ = [
    "BackendName",
    "GenMetricsBackend",
    "NaiveBackend",
    "SkipBackend",
    "get_backend",
]
