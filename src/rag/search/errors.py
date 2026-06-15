"""Search 层错误类型。"""

from __future__ import annotations


class SearchError(Exception):
    """Search 层异常的基类。

    用于区分业务编排错误与底层 (LLM/retriever) 异常, 便于 caller
    决定降级策略。链式原因通过 ``raise ... from e`` 保留根因。
    """


class GenerationError(SearchError):
    """LLM 生成阶段失败。

    抛出本异常而非返回错误字符串, caller 收到后应将失败记入
    ``SearchResult.warnings`` 并将 ``response`` 置空。
    """
