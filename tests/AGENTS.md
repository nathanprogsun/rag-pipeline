# 测试规范（`tests/`）

- 测试函数同样遵守根 `AGENTS.md` 的类型规则：参数 + `-> None` 返回值。
- fixture 使用精确类型（如 `AsyncGenerator[AsyncSession, None]`），不用裸 `Any`。
- 提交前：`make lint`、`make test`。

## 单元测试（`tests/unit/`）

- 无需 Docker，快速、无网络。
- 仅测 domain 逻辑与纯函数。

## 集成测试（`tests/integration/`）

- 使用 **`settings.database_url`** 连真实 PG（见 `tests/integration/conftest.py`）。
- 需本地 PG 已启动（`make up`）；fixture 创建 extension、`create_all`，yield `AsyncSession`。
- **会写入数据** — 跑前建议 `.env` 切专用库：`DATABASE_URL=.../rag_test`。
- 测试向量维度须为 `embed_dim`（1536），例如 `[0.0] * 1535 + [1.0]`。
- 异步测试使用 `@pytest.mark.asyncio`（`pyproject.toml` 中 `asyncio_mode = auto`）。
