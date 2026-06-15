"""Generation stage 10 per Contract 8.

Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 8:

  10. generation (optional) — LLM call with citation instruction prompt

This module provides:
- ``GenStage`` Protocol: orchestrator's stage 10 callback shape
- ``GenFn`` functional alias for test ergonomics
- ``LLMClientLike`` Protocol: minimal LangChain chat interface
- ``CITE_SYSTEM_PROMPT``: instructs the LLM to insert ``[id](CITE)`` markers
- ``make_llm_gen(llm)``: factory that builds a ``GenFn`` from a chat model

Marker parsing utilities used by this stage live in
``rag.infra.text.citation_check``; this module is self-contained otherwise.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from rag.domain.search import Citation, SearchRequest

logger = logging.getLogger(__name__)


# ---------- Protocol contracts ----------


class LLMClientLike(Protocol):
    """Minimal LLM interface — any LangChain BaseChatModel works.

    Not a Pydantic field type (Protocols aren't valid Pydantic types);
    use ``Any`` in SearchPipelineDeps and rely on duck typing.
    """

    async def ainvoke(self, input: object) -> object: ...


class GenStage(Protocol):
    """Stage 10 callback. Produces LLM answer string with ``[id](CITE)`` markers."""

    async def __call__(
        self,
        docs: list,  # list[ScoredDocument] — kept loose to avoid import cycles
        citations: list[Citation],
        req: SearchRequest,
    ) -> str: ...


GenFn = Callable[[list, list[Citation], SearchRequest], Awaitable[str]]


# ---------- LLM gen implementation ----------


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
    """Build a GenFn that calls LLM with citation instruction.

    System prompt instructs the LLM to insert ``[id](CITE)`` markers at
    cited positions. User prompt contains the formatted context
    (``[1] content\n[2] content\n...``) plus the original query.

    Returns a function suitable for ``SearchPipeline(gen=...)``.
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
