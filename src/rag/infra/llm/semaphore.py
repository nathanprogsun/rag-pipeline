import asyncio
from collections.abc import Awaitable
from typing import Literal, TypeVar

from rag.config import LLMConcurrencySettings, settings

__all__ = ["LLMLane", "LLMSemaphore", "llm_sem"]

T = TypeVar("T")

LLMLane = Literal["chat", "embedding", "rerank"]


class LLMSemaphore:
    """按能力通道 (chat / embedding / rerank) 独立并发控制；rerank 未配置时直通。"""

    def __init__(self, concurrency: LLMConcurrencySettings | None = None) -> None:
        cfg = concurrency if concurrency is not None else settings.llm_concurrency
        self._lanes: dict[LLMLane, asyncio.Semaphore] = {
            "chat": asyncio.Semaphore(cfg.chat.max_concurrent),
            "embedding": asyncio.Semaphore(cfg.embedding.max_concurrent),
        }
        if cfg.rerank is not None and cfg.rerank.enabled:
            self._lanes["rerank"] = asyncio.Semaphore(cfg.rerank.max_concurrent)

    async def run(self, lane: LLMLane, coro: Awaitable[T]) -> T:
        sem = self._lanes.get(lane)
        if sem is None:
            return await coro
        async with sem:
            return await coro


llm_sem = LLMSemaphore()
