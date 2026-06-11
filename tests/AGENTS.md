# 测试规范（`tests/`）

- 测试函数/方法同样遵守根 `AGENTS.md` 的类型规则：参数 + `-> None` 返回值。
- fixture 使用精确类型（如 `AsyncGenerator[AsyncSession, None]`），不用裸 `Any`。
- 提交前：`make lint`、`make test`。

## 组织形式

- **优先 class**：同一被测模块/类型的用例集用 `class TestXxx:` 组织（与 `tests/integration/test_chunk_repo.py` 一致）；方法名 `test_*`，类内共享状态用 `setup_method` / `teardown_method` 或 `@pytest.mark.usefixtures(...)`。
- **函数式例外**：零散的 domain 单断言（如 `tests/unit/test_domain.py`）可保留顶层 `def test_*`，不必强行套 class。

## 单元测试（`tests/unit/`）

- 无需 Docker，快速、无网络。
- 仅测 domain 逻辑与纯函数。

## 集成测试（`tests/integration/`）

- **LLM live**（`test_llm_live.py`，`@pytest.mark.live_llm`）：调用真实 chat / embedding / rerank API；对应 Key 未配置则 `pytest.skip`。`TestChatLive` 使用 `loop_scope="class"`，避免 LangChain 缓存的 httpx 客户端跨 function-scoped event loop 复用失败。`get_structured_chat_model` 经 `with_structured_output(method="function_calling")` 直接返回 Pydantic 实例；provider 无 `tool_calls` 时 parser 返回 `None` 则 skip。仅跑 live：`uv run pytest tests/integration/test_llm_live.py -v`。
- 使用 **`settings.database_url`** 连真实 PG（见 `tests/integration/conftest.py`）。
- 需本地 PG 已启动（`make up`）；fixture 创建 extension、`create_all`，yield `AsyncSession`。
- **会写入数据** — 跑前建议 `.env` 切专用库：`DATABASE_URL=.../rag_test`。
- 测试向量维度须为 `embed_dim`（1536），例如 `[0.0] * 1535 + [1.0]`。
- 异步测试使用 `@pytest.mark.asyncio`（`pyproject.toml` 中 `asyncio_mode = auto`）。
