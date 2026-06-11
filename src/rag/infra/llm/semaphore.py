import asyncio
import time
from collections import deque
from collections.abc import Awaitable
from typing import TypeVar

from rag.config import settings

__all__ = ["LLMSemaphore", "llm_sem"]

T = TypeVar("T")


class LLMSemaphore:
    """双层限流: 全局 + per-provider, 含 60s 滑动窗口 RPM。"""

    def __init__(self) -> None:
        self._settings = settings.llm_settings
        self._sem_global = asyncio.Semaphore(self._settings["max_concurrent"])
        self._sem_per_provider: dict[str, asyncio.Semaphore] = {
            provider: asyncio.Semaphore(limit)
            for provider, limit in self._settings["max_concurrent_per_provider"].items()
        }
        self._rpm_windows: dict[str, deque[float]] = {}

    async def run(self, provider: str, coro: Awaitable[T]) -> T:
        if provider not in self._sem_per_provider:
            self._sem_per_provider[provider] = asyncio.Semaphore(16)
        if provider not in self._rpm_windows:
            limit = self._settings["rate_limit_rpm"].get(provider, 1000)
            self._rpm_windows[provider] = deque(maxlen=limit)
        async with self._sem_global:
            async with self._sem_per_provider[provider]:
                await self._check_rpm(provider)
                return await coro

    async def _check_rpm(self, provider: str) -> None:
        window = self._rpm_windows[provider]
        now = time.time()
        while window and now - window[0] > 60:
            window.popleft()
        window.append(time.time())
        maxlen = window.maxlen
        if maxlen is not None and len(window) > maxlen:
            sleep_for = 60 - (now - window[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)


llm_sem = LLMSemaphore()
