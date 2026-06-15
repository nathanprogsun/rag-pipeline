import asyncio
from collections.abc import Callable, Coroutine


def run_coroutine_sync[T](make_coro: Callable[[], Coroutine[None, None, T]]) -> T:
    """在同步 ``invoke`` 中驱动 async ``ainvoke``（仅在无已运行 event loop 时可用）。

    接受 coroutine 工厂而非已创建的 coroutine, 这样在检测到 running loop 时
    不会构造未被 ``await`` 的 coroutine。

    若当前线程已有 event loop（如在 async 测试 / Jupyter 内）应改用
    ``await ainvoke()``。不要使用 ``asyncio.to_thread``——它用于在 async
    上下文里跑阻塞同步函数, 方向相反。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())
    msg = "invoke() cannot be called while an event loop is running; use ainvoke() instead."
    raise RuntimeError(msg)
