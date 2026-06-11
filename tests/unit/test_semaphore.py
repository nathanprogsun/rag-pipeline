import asyncio

import pytest

from rag.config import settings
from rag.infra.llm.semaphore import LLMSemaphore


@pytest.mark.asyncio
async def test_semaphore_limits_global_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_max_concurrent", 2)
    sem = LLMSemaphore()
    in_flight = 0
    max_in_flight = 0

    async def task() -> int:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return 1

    results = await asyncio.gather(*[sem.run("openai", task()) for _ in range(5)])
    assert max_in_flight <= 2
    assert sum(results) == 5
