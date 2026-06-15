"""LLM 生成阶段。

提供:
- ``GenStage`` Protocol: 编排器中生成阶段的回调形状
- ``GenFn`` 函数式别名 (便于测试)
- ``LLMClientLike`` Protocol: 最小 LangChain chat 接口
- ``CITE_SYSTEM_PROMPT``: 指示 LLM 插入 ``[id](CITE)`` 标记
- ``make_llm_gen(llm)``: 从 chat 模型构造 ``GenFn`` 的工厂函数

标记解析工具位于 ``rag.infra.text.citation_check``。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.search import Citation, SearchRequest

logger = logging.getLogger(__name__)


# ---------- Protocol 契约 ----------


class LLMClientLike(Protocol):
    """最小 LLM 接口, 任何 LangChain ``BaseChatModel`` 均可。

    不是 Pydantic 字段类型 (Protocol 无法作为 Pydantic 类型), 在
    ``SearchPipelineDeps`` 中使用 ``Any`` 并依赖 duck typing。
    """

    async def ainvoke(self, input: object) -> object: ...


class GenStage(Protocol):
    """生成阶段回调, 输出含 ``[id](CITE)`` 标记的 LLM 回答字符串。"""

    async def __call__(
        self,
        docs: list,  # list[ScoredDocument] — kept loose to avoid import cycles
        citations: list[Citation],
        req: SearchRequest,
    ) -> str: ...


GenFn = Callable[[list, list[Citation], SearchRequest], Awaitable[str]]


# ---------- LLM 生成实现 ----------


CITE_SYSTEM_PROMPT: str = (
    "你是一个知识库问答助手。请严格基于提供的参考资料回答问题。\n"
    "规则:\n"
    "1. 只使用参考资料中的事实, 不要引入外部知识。\n"
    "2. 在引用了具体事实的位置插入 [id](CITE) 标记, id 是参考资料的 1-based 编号。\n"
    "3. 不要捏造未在参考资料中出现的引用 id。\n"
    "4. 如果参考资料不足以回答问题, 请如实说明。\n"
    "5. 回答语言与用户提问语言保持一致。\n"
)


def make_llm_gen(llm: LLMClientLike) -> GenFn:
    """构造调用 LLM 并附带引用指令的 ``GenFn``。

    System prompt 指示 LLM 在引用位置插入 ``[id](CITE)`` 标记。
    User prompt 包含格式化好的上下文 (``[1] content\n[2] content\n...``)
    与原 query。

    Returns:
        可直接传入 ``SearchPipeline(gen=...)`` 的函数。
    """

    async def gen(
        docs: list,
        citations: list[Citation],
        req: SearchRequest,
    ) -> str:
        if not docs:
            return "no relevant content found"
        context_lines = [f"[{i + 1}] {c.content}" for i, c in enumerate(citations)]
        context = "\n\n".join(context_lines) if context_lines else "(no citations)"
        user_prompt = f"参考资料:\n{context}\n\n问题: {req.query}\n\n回答:"
        try:
            response = await llm.ainvoke(
                [
                    {"role": "system", "content": CITE_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ]
            )
        except Exception as e:
            logger.warning("LLM gen failed for query=%r: %r", req.query, e)
            return f"(LLM generation failed: {e})"
        content = getattr(response, "content", None)
        if content is None:
            content = str(response)
        return str(content)

    return gen
