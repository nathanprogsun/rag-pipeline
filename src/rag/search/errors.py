"""Search 层错误类型。

自定义异常由 caller 决定降级路径(例如 Orchestrator 写入 warnings),
而非污染 ``SearchResult.response`` 文本。
"""

from __future__ import annotations


class SearchError(Exception):
    """Search 层异常的基类。

    用于区分业务编排错误与底层 (LLM/retriever) 异常, 便于 caller
    决定降级策略。链式原因通过 ``raise ... from e`` 保留根因。
    """


class GenerationError(SearchError):
    """Stage 10 LLM generation 失败。

    make_llm_gen 在 LLM 抛错时抛出本异常而非返回错误字符串, caller
    (orchestrator) 收到后应将失败记入 ``SearchResult.warnings``,
    ``response`` 置空, 让上游 client 自行决定渲染策略。
    """
