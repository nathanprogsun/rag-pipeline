import pytest

from rag.infra.pg.runnable_sync import run_coroutine_sync


async def _async_add(a: int, b: int) -> int:
    return a + b


def test_run_coroutine_sync_runs_coroutine() -> None:
    assert run_coroutine_sync(lambda: _async_add(1, 2)) == 3


@pytest.mark.asyncio
async def test_run_coroutine_sync_rejects_running_loop() -> None:
    with pytest.raises(RuntimeError, match="ainvoke"):
        run_coroutine_sync(lambda: _async_add(1, 2))
