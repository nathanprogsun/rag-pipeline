import asyncio

import pytest

from rag.config import LaneSettings, LLMConcurrencySettings
from rag.infra.llm.semaphore import LLMSemaphore


def _concurrency(
    *,
    chat: int = 8,
    embedding: int = 12,
    rerank: int | None = 4,
) -> LLMConcurrencySettings:
    rerank_lane = LaneSettings(max_concurrent=rerank) if rerank is not None else None
    return LLMConcurrencySettings(
        chat=LaneSettings(max_concurrent=chat),
        embedding=LaneSettings(max_concurrent=embedding),
        rerank=rerank_lane,
    )


@pytest.mark.asyncio
async def test_chat_lane_limits_concurrency() -> None:
    sem = LLMSemaphore(_concurrency(chat=2))
    in_flight = 0
    max_in_flight = 0

    async def task() -> int:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return 1

    results = await asyncio.gather(*[sem.run("chat", task()) for _ in range(5)])
    assert max_in_flight <= 2
    assert sum(results) == 5


@pytest.mark.asyncio
async def test_lanes_are_independent() -> None:
    sem = LLMSemaphore(_concurrency(chat=1, embedding=2))
    chat_in_flight = 0
    embed_in_flight = 0
    max_embed_in_flight = 0

    async def chat_task() -> None:
        nonlocal chat_in_flight
        chat_in_flight += 1
        await asyncio.sleep(0.1)
        chat_in_flight -= 1

    async def embed_task() -> None:
        nonlocal embed_in_flight, max_embed_in_flight
        embed_in_flight += 1
        max_embed_in_flight = max(max_embed_in_flight, embed_in_flight)
        await asyncio.sleep(0.05)
        embed_in_flight -= 1

    chat_blocker = asyncio.create_task(sem.run("chat", chat_task()))
    await asyncio.sleep(0.01)
    await asyncio.gather(*[sem.run("embedding", embed_task()) for _ in range(2)])
    await chat_blocker

    assert max_embed_in_flight == 2


@pytest.mark.asyncio
async def test_rerank_lane_passthrough_when_unconfigured() -> None:
    sem = LLMSemaphore(_concurrency(rerank=None))
    in_flight = 0
    max_in_flight = 0

    async def task() -> int:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return 1

    results = await asyncio.gather(*[sem.run("rerank", task()) for _ in range(5)])
    assert max_in_flight == 5
    assert sum(results) == 5


@pytest.mark.asyncio
async def test_rerank_lane_limits_concurrency_when_configured() -> None:
    sem = LLMSemaphore(_concurrency(rerank=2))
    in_flight = 0
    max_in_flight = 0

    async def task() -> int:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return 1

    results = await asyncio.gather(*[sem.run("rerank", task()) for _ in range(5)])
    assert max_in_flight <= 2
    assert sum(results) == 5
