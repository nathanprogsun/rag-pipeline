import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


def run_coroutine_sync[T](make_coro: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """在同步 ``invoke`` 中驱动 async ``ainvoke``（无已运行 event loop 时）。

    接受 coroutine 工厂而非已创建的 coroutine，以便在检测到 running loop 时
    不必构造未 await 的 coroutine。

    若当前线程已有 event loop（如在 async 测试 / Jupyter 内），应改用 ``await ainvoke()``；
    不可用 ``asyncio.to_thread``——它用于在 async 上下文里跑**阻塞同步**函数，方向相反。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())
    msg = "invoke() cannot be called while an event loop is running; use ainvoke() instead."
    raise RuntimeError(msg)
