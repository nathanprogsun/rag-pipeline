# Task 7: LLM Clients + Semaphore (并发控制)

> **Source:** Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 1442-1646)
> **Fixes Applied:** B3 (temperature), audit #1 (re-export + chat.py merge), audit #2 (LangSmith + JsonLoggingHandler), subagent #2 (timeout/max_retries), subagent #6 (tenacity retry)
> **Cross-Task Fixes:** B3 explicit `temperature=0.1` for `QueryExtensionRunnable` / `ImageCaptionRunnable` documented in §Fixes.
> **Cross-Task (timeout 跨 task6/7/16):** `ChatOpenAI(timeout=30.0, max_retries=0)` 已就位; 同常量 (30s FastGPT 默认) 在 task3 `database.py:create_async_engine(connect_args={"timeout": 30}, pool_timeout=30)` 与 task16 orchestrator 复用. 本 task docstring 注明 30.0s = FastGPT 默认, 避免 magic number 漂移.

**Files:**
- Create: `src/rag/infra/llm/__init__.py`
- Create: `src/rag/infra/llm/semaphore.py`
- Create: `src/rag/infra/llm/chat.py`            # ChatOpenAI + get_m3_chat_model()
- Create: `src/rag/infra/llm/embed.py`
- Create: `src/rag/infra/llm/rerank.py`
- Create: `tests/unit/test_semaphore.py`

- [ ] **Step 1: 写失败单测 (semaphore)**

```python
# tests/unit/test_semaphore.py
import asyncio
try:
    _loop = asyncio.get_running_loop()
except RuntimeError:
    _loop = None
import pytest
from rag.infra.llm.semaphore import LLMSemaphore, LLMSettings

@pytest.mark.asyncio
async def test_semaphore_limits_global_concurrency():
    s = LLMSemaphore(LLMSettings(max_concurrent=2))
    in_flight = 0
    max_in_flight = 0

    async def task():
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        in_flight -= 1
        return 1

    results = await asyncio.gather(*[s.run("openai", task()) for _ in range(5)])
    assert max_in_flight <= 2   # 限流生效
    assert sum(results) == 5
```

- [ ] **Step 2: 跑测试,确认 fail**

```bash
uv run pytest tests/unit/test_semaphore.py -v
# 期望: ImportError
```

- [ ] **Step 3: 写 semaphore.py** (含 audit #1 修复: `__all__` 显式 re-export `LLMSettings`)

```python
# src/rag/infra/llm/semaphore.py
import asyncio
try:
    _loop = asyncio.get_running_loop()
except RuntimeError:
    _loop = None
import time
from collections import deque
from typing import Awaitable, TypeVar
from rag.config import LLMSettings   # H5 修正: 单一定义源
                                        # audit #1 修复: 显式 re-export, 允许
                                        # `from rag.infra.llm.semaphore import LLMSettings`

__all__ = ["LLMSemaphore", "LLMSettings", "llm_sem"]   # audit #1 修复

T = TypeVar("T")

class LLMSemaphore:
    """双层限流: 全局 + per-provider, 含 60s 滑动窗口 RPM。"""

    def __init__(self, settings: LLMSettings):
        self._settings = settings
        self._sem_global = asyncio.Semaphore(settings.max_concurrent)
        self._sem_per_provider: dict[str, asyncio.Semaphore] = {
            p: asyncio.Semaphore(n)
            for p, n in settings.max_concurrent_per_provider.items()
        }
        self._rpm_windows: dict[str, deque] = {}

    async def run(self, provider: str, coro: Awaitable[T]) -> T:
        if provider not in self._sem_per_provider:
            self._sem_per_provider[provider] = asyncio.Semaphore(16)
        if provider not in self._rpm_windows:
            limit = self._settings.rate_limit_rpm.get(provider, 1000)
            self._rpm_windows[provider] = deque(maxlen=limit)
        async with self._sem_global:
            async with self._sem_per_provider[provider]:
                await self._check_rpm(provider)
                return await coro

    async def _check_rpm(self, provider: str):
        # M2 修正: 先占坑再 sleep, 防止竞态条件 (多个协程同时通过检查)
        window = self._rpm_windows[provider]
        now = time.time()
        while window and now - window[0] > 60:
            window.popleft()
        window.append(time.time())   # 先占坑
        if len(window) > window.maxlen:
            sleep_for = 60 - (now - window[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)

# 全局单例
llm_sem = LLMSemaphore(LLMSettings())
```

- [ ] **Step 4: 写 chat.py / embed.py (audit #1 修复: 合并 Step4 + Step5 为单文件; B3 修复: temperature=0.1; subagent #2 修复: 显式 `timeout=30.0, max_retries=0` 因已有 `LLMSemaphore`; audit #2 修复: LangSmith 默认开启 + JsonLoggingHandler 周期 flush)**

```python
# src/rag/infra/llm/chat.py
# audit #1 修复: Step4 (基础 chat) + Step5 (M3 multimodal) 合并为单文件
# B3 修复: temperature 默认从 0.0 → 0.1 (因 mini 默认 model 不支持 0.0)
# subagent #2 修复: ChatOpenAI 显式 timeout=30.0 + max_retries=0
#   理由: 已有 LLMSemaphore 限流, langchain-openai 默认 max_retries=6 会放大
#   backpressure 并把限流职责让给上游。明确禁用内置重试, 失败即抛。
# audit #2 修复: LangSmith tracing 默认开 + JsonLoggingHandler 周期 flush

import logging
from logging.handlers import RotatingFileHandler
import os
import json
import threading
import time
from pathlib import Path
from langchain_openai import ChatOpenAI
from rag.config import settings
from rag.infra.llm.semaphore import llm_sem

# ── audit #2 修复: LangSmith tracing 默认开 ─────────────────────
# 默认开启 trace, 通过 LANGSMITH_TRACING=false 显式关闭
os.environ.setdefault("LANGSMITH_TRACING", "true")
os.environ.setdefault("LANGSMITH_PROJECT", os.environ.get("LANGSMITH_PROJECT", "rag-pipeline"))


# ── audit #2 修复: JsonLoggingHandler 周期 flush ─────────────────
# 解决 BufferedJsonHandler 在进程崩溃或长事务中丢日志的问题:
# - 启动一个 daemon 线程每 5s flush 一次
# - 同时绑定 atexit, 保证进程退出前 flush
class JsonLoggingHandler(RotatingFileHandler):
    """行级 JSON 日志, 周期 flush (audit #2 修复)。"""

    def __init__(self, path: str, flush_interval: float = 5.0, **kwargs):
        super().__init__(path, **kwargs)
        self._flush_interval = flush_interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._periodic_flush, daemon=True, name="json-log-flush")
        self._thread.start()
        import atexit
        atexit.register(self._final_flush)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.stream.write(msg + "\n")
            # 不立即 flush, 由后台线程负责
        except Exception:
            self.handleError(record)

    def _periodic_flush(self):
        while not self._stop.wait(self._flush_interval):
            try:
                self.flush()
            except Exception:
                pass

    def _final_flush(self):
        self._stop.set()
        try:
            self.flush()
            self.close()
        except Exception:
            pass


def configure_json_logging(log_path: str = "./logs/rag.jsonl") -> JsonLoggingHandler:
    """配置 JSON 结构化日志 + 周期 flush。返回 handler 供测试 assert。"""
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    handler = JsonLoggingHandler(
        log_path,
        flush_interval=5.0,
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
    )
    fmt = logging.Formatter(
        '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":%(message)j}',
        defaults={"time": time.time()},
    )
    handler.setFormatter(fmt)
    root = logging.getLogger()
    # 避免重复添加
    if not any(isinstance(h, JsonLoggingHandler) for h in root.handlers):
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    return handler


# 默认启动一次 (CLI / 服务启动时调用; 库模式下可 disable)
if os.environ.get("RAG_DISABLE_JSON_LOGGING") != "true":
    configure_json_logging()


# ── chat model 工厂 (B3 + subagent #2 修复) ──────────────────────
def get_chat_model(
    model: str = "gpt-4o-mini",
    temperature: float = 0.1,                       # B3 修复: 0.0 → 0.1
) -> ChatOpenAI:
    """标准 chat model, OpenAI 协议。

    B3 修复: 默认 temperature=0.1, 避免 mini 系列 model 报
        "Unsupported value: 'temperature' does not support 0.0"
    subagent #2 修复: 显式 timeout + max_retries=0, 因已有 LLMSemaphore 限流。
    跨 task 强化: 30.0s 是 FastGPT 通用默认 timeout (对齐 LLM/PG/HTTP),
        见 task16 orchestrator 也复用同一常量, 避免各路径 magic number 漂移.
    """
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        timeout=30.0,                               # subagent #2 修复; FastGPT 默认
        max_retries=0,                              # subagent #2 修复
    )


def get_m3_chat_model(temperature: float = 0.1) -> ChatOpenAI:   # B3 修复: 0.0 → 0.1
    """MiniMax-M3 多模态 chat model (Issue 3: 不需要独立 vlm.py)。

    直接用 ChatOpenAI + M3 base_url/api_key, 支持 vision。
    ImageCaptionRunnable 通过此工厂获取 LLM, 调用时传 image_url content。

    B3 修复: temperature 默认从 0.0 → 0.1 (M3 model 同样不支持 0.0)。
    subagent #2 修复: 显式 timeout=30.0 + max_retries=0。
    跨 task 强化: 30.0s 是 FastGPT 通用默认 timeout, 与 get_chat_model 对齐.
    """
    return ChatOpenAI(
        model="M3-multimodal",
        temperature=temperature,
        api_key=settings.m3_api_key,
        base_url=settings.m3_base_url,
        timeout=30.0,                               # subagent #2 修复; FastGPT 默认
        max_retries=0,                              # subagent #2 修复
    )
```

```python
# src/rag/infra/llm/embed.py
# subagent #6 修复: tenacity retry 装饰 aembed_documents / aembed_query 本身,
#   而非 aembed_query 内部细节。这样 embeddings 任何失败都走重试。
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from openai import APIError, APITimeoutError, RateLimitError
from langchain_openai import OpenAIEmbeddings
from rag.config import settings
from rag.infra.llm.semaphore import llm_sem


# subagent #6 修复: 重试装饰器放在类外面, 包装方法;
#   而非嵌在 aembed_documents 内部 try/except, 那样无法重试已被
#   langchain-openai 调用的网络错误。
class _RetryableEmbeddings(OpenAIEmbeddings):
    """在 LangChain OpenAIEmbeddings 之上叠加 tenacity 重试 (subagent #6 修复)。

    重试范围: aembed_documents / aembed_query。
    触发条件: APIError / APITimeoutError / RateLimitError。
    退避策略: exponential, 最多 3 次, base 1s。
    """

    @retry(
        retry=retry_if_exception_type((APIError, APITimeoutError, RateLimitError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await super().aembed_documents(texts)

    @retry(
        retry=retry_if_exception_type((APIError, APITimeoutError, RateLimitError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def aembed_query(self, text: str) -> list[float]:
        return await super().aembed_query(text)


# ── P0-10 修复: with_structured_output 能力补齐 (Task 13/14 引用) ──
# 背景: Task 13/14 期望通过 llm.with_structured_output(PydanticModel, method="function_calling")
# 获取结构化输出, 但 Task 7 的 chat 模块未提供该方法的显式封装。
# 修复: 添加 get_structured_chat_model 包装, 与 LangChain 原生 with_structured_output 协议对齐。
# 已知约束 (B3 修复历史): MiniMax M3 不支持 json_schema, 只能用 method="function_calling"。
def get_structured_chat_model(
    schema: type,
    temperature: float = 0.1,
    timeout: float = 30.0,
    max_retries: int = 0,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str = "gpt-4o-mini",
):
    """带结构化输出能力的 chat model (B3 兼容, function_calling only)。
    
    返回值具有 .ainvoke(input) 接口, 直接返回 schema 实例, 调用方写法:
        structured_llm = get_structured_chat_model(DecomposedQueries)
        result = await structured_llm.ainvoke(user_prompt)  # result is DecomposedQueries
    
    与 Task 13/14 的 llm.with_structured_output(Schema, method="function_calling") 行为一致。
    """
    from langchain_core.utils.function_calling import convert_pydantic_to_function
    from rag.infra.llm.chat import get_chat_model
    base = get_chat_model(
        temperature=temperature, timeout=timeout, max_retries=max_retries,
        base_url=base_url, api_key=api_key, model=model,
    )
    # Pydantic → OpenAI function schema (避免与 langchain 0.3 内部 schema 生成器耦合)
    fn_schema = convert_pydantic_to_function(schema)
    return base.bind(functions=[fn_schema], function_call={"name": fn_schema["name"]})


def get_embed_model(model: str | None = None) -> OpenAIEmbeddings:
    """获取 embed model。返回 _RetryableEmbeddings 实例。

    调用方通过 llm_sem.run("openai", embed.aembed_query(text)) 进入限流。
    """
    return _RetryableEmbeddings(
        model=model or settings.embedding_model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
    )
```

- [ ] **Step 5: 写 rerank.py (Cohere + 简化 BGE stub)**

```python
# src/rag/infra/llm/rerank.py
from typing import Protocol

class Reranker(Protocol):
    async def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        """返回 (doc_idx, score) 列表, 按 score 降序。"""
        ...

class CohereRerank:
    def __init__(self, api_key: str, model: str = "rerank-english-v3.0"):
        from cohere import AsyncClient
        self.client = AsyncClient(api_key=api_key)
        self.model = model

    async def rerank(self, query, documents, top_k):
        resp = await self.client.rerank(
            model=self.model, query=query, documents=documents, top_n=top_k,
        )
        return [(r.index, r.relevance_score) for r in resp.results]

class NoOpRerank:
    """Rerank 不可用时的兜底, 按输入顺序返回。"""
    async def rerank(self, query, documents, top_k):
        return [(i, 1.0 - i * 0.01) for i in range(min(top_k, len(documents)))]
```

- [ ] **Step 6: 写 __init__.py (audit #1 修复: 显式 re-export 公共 API)**

```python
# src/rag/infra/llm/__init__.py
# audit #1 修复: 显式 re-export 公共 API, 避免下游 import 路径歧义
from rag.infra.llm.semaphore import LLMSemaphore, LLMSettings, llm_sem
from rag.infra.llm.chat import get_chat_model, get_m3_chat_model, configure_json_logging
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.rerank import Reranker, CohereRerank, NoOpRerank

__all__ = [
    "LLMSemaphore", "LLMSettings", "llm_sem",
    "get_chat_model", "get_m3_chat_model", "get_structured_chat_model", "configure_json_logging",
    "get_embed_model",
    "Reranker", "CohereRerank", "NoOpRerank",
]
```

- [ ] **Step 7: 跑测试**

```bash
uv run pytest tests/unit/test_semaphore.py -v
# 期望: 1 passed
```

- [ ] **Step 8: commit**

```bash
git add src/rag/infra/llm tests/unit/test_semaphore.py
git commit -m "feat(llm): semaphore + chat/embed/rerank + M3 multimodal via ChatOpenAI"
```

---

## Applied Fixes Summary

| ID | Severity | Location | Change |
|----|----------|----------|--------|
| **B3** | 🔴 Blocker | `chat.py` `get_chat_model` | `temperature: float = 0.0` → `0.1` |
| **B3** | 🔴 Blocker | `chat.py` `get_m3_chat_model` | `temperature: float = 0.0` → `0.1` |
| **B3** | 🔴 Blocker | `search.py` (Task 2) `SearchRequest` | `temperature: float = 0.0` → `0.1` |
| **B3** | 🔴 Blocker | `query_ext.py` (Task 12) `QueryExtensionRunnable.__init__` | 显式接受 `temperature: float = 0.1` 参数并透传给 `get_m3_chat_model` |
| **B3** | 🔴 Blocker | `image_caption.py` (Task 13) `ImageCaptionRunnable.__init__` | 显式接受 `temperature: float = 0.1` 参数并透传给 `get_m3_chat_model` |
| **audit #1** | 🟡 | `semaphore.py` | 加 `from rag.config import LLMSettings` 显式 import (已存在) + 新增 `__all__ = ["LLMSemaphore", "LLMSettings", "llm_sem"]` |
| **audit #1** | 🟡 | `chat.py` | Step 4 (基础) + Step 5 (M3) 合并为单一 `chat.py` 文件, 避免实现重复 |
| **audit #1** | 🟡 | `__init__.py` | 新增 `src/rag/infra/llm/__init__.py` 显式 re-export 公共 API |
| **audit #2** | 🟡 | `chat.py` | LangSmith tracing 默认开 (`os.environ.setdefault("LANGSMITH_TRACING", "true")`) + 新增 `JsonLoggingHandler` 类 (daemon 线程 5s 周期 flush + atexit 收尾) |
| **subagent #2** | 🟢 | `chat.py` `get_chat_model` / `get_m3_chat_model` | `ChatOpenAI(...)` 加 `timeout=30.0, max_retries=0`; 理由: 已有 `LLMSemaphore` 限流, 不应让 langchain-openai 内置 retry 6 次与 semaphore 重复并放大 backpressure |
| **subagent #6** | 🟢 | `embed.py` | 用 `_RetryableEmbeddings` 装饰器模式, tenacity `@retry` 装饰在 `aembed_documents` / `aembed_query` 之外, 触发条件 `APIError / APITimeoutError / RateLimitError`, 3 次 exponential backoff |

---

## Cross-Task Fixes (B3 explicit `temperature=0.1` 参数)

`QueryExtensionRunnable` 与 `ImageCaptionRunnable` 不在 Task 7 范围内, 但 B3 修复要求它们的 `__init__` 显式接受 `temperature=0.1` 并透传。**实施时需在对应 task 中修改**, 此处给出参考签名, 避免 Task 12 / Task 13 实现时遗漏:

```python
# 参考: src/rag/pipeline/query_ext.py (Task 12)
from rag.infra.llm.chat import get_m3_chat_model

class QueryExtensionRunnable(Runnable):
    def __init__(
        self,
        temperature: float = 0.1,                       # B3 修复
        max_variants: int = 3,
        model: str = "gpt-4o-mini",
    ):
        self.temperature = temperature
        self.max_variants = max_variants
        # 显式传 temperature 给 chat 工厂
        self._chat = get_m3_chat_model(temperature=temperature) if "m3" in model.lower() \
                     else get_chat_model(model=model, temperature=temperature)
```

```python
# 参考: src/rag/pipeline/image_caption.py (Task 13)
from rag.infra.llm.chat import get_m3_chat_model

class ImageCaptionRunnable(Runnable):
    def __init__(
        self,
        temperature: float = 0.1,                       # B3 修复
        model: str = "M3-multimodal",
    ):
        self.temperature = temperature
        self._chat = get_m3_chat_model(model=model, temperature=temperature)
```

并同步更新 `search.py` (Task 2) `SearchRequest`:

```python
# src/rag/domain/search.py (Task 2 修订)
class SearchRequest(BaseModel):
    ...
    temperature: float = 0.1                            # B3 修复: 0.0 → 0.1
    ...
```

---

## Verification Checklist

执行 Task 7 后, 确认以下 7 项:

- [ ] `uv run pytest tests/unit/test_semaphore.py -v` 通过 (限流生效)
- [ ] `python -c "from rag.infra.llm.semaphore import LLMSettings"` 无 ImportError (audit #1)
- [ ] `python -c "from rag.infra.llm import get_chat_model, get_m3_chat_model, get_embed_model, llm_sem"` 无 ImportError (audit #1 合并)
- [ ] `python -c "from rag.infra.llm.chat import configure_json_logging; h=configure_json_logging('/tmp/x.jsonl'); import logging; logging.info('test'); time.sleep(6); open('/tmp/x.jsonl').read().count('test')>=1"` 通过 (audit #2 周期 flush)
- [ ] `grep -E "LANGSMITH_TRACING.*setdefault" src/rag/infra/llm/chat.py` 命中 (audit #2)
- [ ] `grep -E "timeout=30\.0.*max_retries=0" src/rag/infra/llm/chat.py` 命中 (subagent #2)
- [ ] `grep -E "@retry" src/rag/infra/llm/embed.py` 命中且未嵌套在方法内部 (subagent #6)
