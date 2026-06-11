# rag-pipeline — Agent 编码规范

Python 3.13 RAG 流水线。目录：`src/rag/`（domain + infra）、`tests/unit/`、`tests/integration/`。

权威参考：`pyproject.toml`、`src/rag/infra/pg/base.py`、`src/rag/infra/pg/schema.sql`。

当 Agent 反复犯同类错误时，迭代更新本文件及子目录下的 `AGENTS.md`（见文末 **任务收尾**）。

---

## 必须遵守

- **import 放在文件顶部**（stdlib → 第三方 → `rag.*`），组间空一行。
- **所有函数/方法**（含测试、`__init__`、fixture）必须有 **参数类型 + 返回值类型**；无返回值写 `-> None`。
- **禁止裸 `Any`**（ruff `ANN401` 在源码层强制；mypy 查未注解/不完整签名）。确需使用时：
  1. 优先换具体类型、`Protocol`、`TypeVar`；
  2. 不得已则保留 `Any` 并在**同一行**写清原因，例如：`value: Any  # type: 第三方回调签名未提供存根`。
- 使用 **async SQLAlchemy 2.0**（`AsyncSession`、`select()`、`Mapped[]`、`mapped_column()`）。
- **domain**（`src/rag/domain/`）不得引入 SQLAlchemy，仅 Pydantic 模型。
- **infra**（`src/rag/infra/pg/`）为唯一 SQLAlchemy 层；通过 **repository** 访问 DB，handler 中不写裸 SQL。
- **向量维度** 与 schema 一致：`1536`（见 `schema.sql`、`ChunkModel.embedding`、测试向量）。
- 完成前运行：`make lint`、`make test`（或等价命令，见下方工具链）。
- **最小 diff**：命名、import、注释风格与周边代码一致。

---

## 禁止事项

- **函数内 lazy import**（如在方法中间 `from sqlalchemy import text`），除非有文档说明的循环依赖例外，并附一行注释。
- **`DatasetModel` 与 `ChunkModel` 之间使用 `relationship()`** — 仅用 `dataset_id` 外键列，避免模型文件循环 import。
- 用 **`TYPE_CHECKING` 规避类型检查** — 应正常 import；确有循环依赖须注明原因。
- **物理删除** 用户数据 — 使用软删除（`deleted_at`）。
- **domain → infra import** — domain 不得依赖 PG 模型或 repository。
- 在未同步修改 models + `schema.sql` 的情况下 **手改迁移/生成产物**。
- **猜测环境变量** — 查阅 `src/rag/config.py` 与 `.env.example`。

---

## 工具链

| 工具 | 用途 |
|------|------|
| `uv` | 依赖与运行（`uv run pytest`、`uv sync --extra dev`） |
| `ruff` | lint + format（`ANN` 规则强制类型注解，见 `pyproject.toml`） |
| `mypy` | 对 `src/` + `tests/` 做 strict 类型检查 |
| `pytest` + `pytest-asyncio` | 测试 |
| `pre-commit` | 提交前：ruff-check (--fix) → ruff-format → mypy；首次执行 `uv run pre-commit install` |

开发依赖安装：`uv sync --extra dev`

```bash
make lint                              # ruff check + format --check + mypy
make test                              # unit + integration
uv run ruff check --fix . && uv run ruff format .   # 自动修复后重跑 make lint
uv run pre-commit run --all-files
make up                                # 本地 PG/Redis
```

类型与 lint 的**单一事实来源**是 `pyproject.toml`（`[tool.ruff.lint]`、`[tool.mypy]`）；本文件只写行为约定，不重复罗列规则细节。

---

## 项目结构

```
src/rag/
  config.py          # pydantic-settings
  domain/            # Pydantic DTO（无 SQLAlchemy）→ 见 domain/AGENTS.md
  infra/pg/          # PG 基础设施 → 见 infra/pg/AGENTS.md
    base.py          # Base、TimestampMixin、SoftDeleteMixin
    database.py      # engine、AsyncSessionLocal
    models/          # DatasetModel、ChunkModel（仅 FK，无 relationship）
    repositories/    # ChunkRepository 等
    schema.sql       # DDL 参考，须与 models 同步
tests/               # 见 tests/AGENTS.md
  unit/
  integration/       # settings.database_url 真实 PG
```

---

## 分层规范（跨工具通用）

按目录拆分的 **`AGENTS.md`** 是业界通用的作用域方案，Cursor、Claude Code 等均支持**嵌套 `AGENTS.md`**：编辑某目录下文件时，自动合并该目录及父级 `AGENTS.md`。

| 文件 | 作用域 |
|------|--------|
| `AGENTS.md`（本文件） | 全局 |
| `src/rag/infra/pg/AGENTS.md` | PG / SQLAlchemy 层 |
| `src/rag/domain/AGENTS.md` | Domain 层 |
| `tests/AGENTS.md` | 测试 |

`.cursor/rules/*.mdc` 为 **Cursor 私有格式**（YAML frontmatter + glob），其他工具无法识别。本项目以嵌套 `AGENTS.md` 为唯一分层规范来源，不维护重复的 `.mdc` 文件。

---

## 任务收尾（Harness 如何保持最新）

每个 task / 功能块完成前，Agent **必须**执行：

1. **`make lint` + `make test` 通过**
2. **回顾本次是否出现**：新约定、反复错误、工具链变更、分层边界问题
3. **有则更新 harness**（只写 agent 需要的行为指引，规则细节留在 `pyproject.toml`）：
   - 全局 → 根 `AGENTS.md`
   - PG / domain / tests → 对应子目录 `AGENTS.md`
4. **向用户简要说明** harness 是否有改动（改了什么、为什么）

**为何能“实时”生效**：Cursor / Claude Code 等在编辑某路径时会**自动合并**该目录及父级 `AGENTS.md`，无需重启；下次对话编辑同目录文件即读到最新规范。
