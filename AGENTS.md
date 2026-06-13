# rag-pipeline — Agent 编码规范

Python 3.13 RAG 流水线。目录：`src/rag/`（domain + infra）、`tests/unit/`、`tests/integration/`。

权威参考：`pyproject.toml`、`src/rag/infra/pg/base.py`、`src/rag/infra/pg/schema.sql`。

当 Agent 反复犯同类错误时，迭代更新本文件及子目录下的 `AGENTS.md`（见文末 **任务收尾**）。

## 项目当前状态

- **已完成**：DTO 修复 / Chunker 重写 / Normalizer 拆层 / 入口收敛 / 格式补全（txt/md/html/pdf/docx/pptx/xlsx/csv/json/api）/ CLI / 测试。
- **全栈 async 主干**：`IngestPipeline.ingest` / `Normalizer.normalize` / `html_to_md` / `html_adapter` / `docx_adapter` 均为 `async def`，CLI 顶层用 `asyncio.run` 驱动；不再有 `asyncio.run` 嵌套与 sync fallback 反模式。
- 静态 `structure/` 不再是独立流水线阶段：`TextDoc.structure` 字段保留供 reader 填充, Heading / DocumentStructure 类型已合并进 `types.py`。
- Ingest 单一入口：`async IngestPipeline.ingest(IngestSource) -> IngestResult`；CLI 入口 `uv run rag-ingest`（见 `src/rag/ingest/cli.py`）。

---

## 必须遵守

- **import 放在文件顶部**（stdlib → 第三方 → `rag.*`），组间空一行。`src/` 由 ruff **`PLC0415`** 强制；禁止 `TYPE_CHECKING` 块内 import 规避类型（见下方）。
- **所有函数/方法**（含测试、`__init__`、fixture）必须有 **参数类型 + 返回值类型**；无返回值写 `-> None`。
- **禁止裸 `Any`**（ruff **`ANN401`** 在源码层强制；mypy 查未注解/不完整签名，不用 `disallow_any_explicit` 以免误伤 Pydantic `extras`）。确需使用时：
  1. 优先换具体类型、`Protocol`、`TypeVar`；
  2. 不得已则保留 `Any` 并在**同一行**写清原因，例如：`value: Any  # type: 第三方回调签名未提供存根`。
- 使用 **async SQLAlchemy 2.0**（`AsyncSession`、`select()`、`Mapped[]`、`mapped_column()`）。
- **domain**（`src/rag/domain/`）不得引入 SQLAlchemy，仅 Pydantic 模型。
- **infra**（`src/rag/infra/pg/`）为唯一 SQLAlchemy 层；通过 **repository** 访问 DB，handler 中不写裸 SQL。
- **向量维度** 与 schema 一致：`1536`（见 `schema.sql`、`ChunkModel.embedding`、测试向量）。
- 完成前运行：`make lint`、`make test`（或等价命令，见下方工具链）。
- **最小 diff**：命名、import、注释风格与周边代码一致。
- **不用模块级 `__all__`**：公开 API 靠显式 import / re-export；包 barrel（`**/__init__.py`）的 re-export 若触发 ruff F401，已在 `pyproject.toml` 按路径忽略。
- **async 契约**：任何在已有 event loop 中会触达的入口（Normalizer 子类、reader adapter、helper）必须 `async def`；sync 函数内不得嵌套 `asyncio.run`。sync 上下文（如 worker thread、Runnable.sync 包装）需跨 async 边界时，统一使用 `src/rag/infra/pg/runnable_sync.py` 的 `run_coroutine_sync(coro_factory)`（coroutine factory + running-loop 检测）。

---

## 禁止事项

- **函数内 lazy import**（如在方法中间 `from sqlalchemy import text`），除非有文档说明的循环依赖例外，并附一行注释。`src/` 由 ruff **`PLC0415`**（`import-outside-top-level`）在 CI 拦截。
- **`# type: ignore` 必须带规则码**（ruff **`PGH003`**），禁止裸 `# type: ignore`。
- **`DatasetModel` 与 `ChunkModel` 之间使用 `relationship()`** — 仅用 `dataset_id` 外键列，避免模型文件循环 import。
- 用 **`TYPE_CHECKING` 规避类型检查** — 应正常 import；确有循环依赖须注明原因。
- **物理删除** 用户数据 — 使用软删除（`deleted_at`）。
- **domain → infra import** — domain 不得依赖 PG 模型或 repository。
- 在未同步修改 models + `schema.sql` 的情况下 **手改迁移/生成产物**。
- **猜测环境变量** — 查阅 `src/rag/config.py` 与 `.env.example`。
- **不要在注释中引用 FastGPT / Dify 等第三方 RAG 平台名**（用作对齐参考时），注释只描述技术行为；历史溯源由 git blame + 任务记录承担。
- **LangChain 例外**：作为项目实际依赖（`pyproject.toml` 中的 SDK），`LangChain` / `LCEL` / `LangSmith` 等名称可在 docstring / 注释中提及（描述 SDK 行为 / 选型原因），不视为违反上一条。
- **嵌套 `asyncio.run`**：sync 函数内不得 `asyncio.run(...)`；CLI / pipeline 顶层已经有 event loop，嵌套会抛 `RuntimeError` 并留下 orphan coroutine。sync 上下文需驱动 async 必须用 `run_coroutine_sync`（已有 helper）。
- **sync Normalizer 子类**：`Normalizer.normalize` 已是 `async def` 基类，任何子类（包括 `NoOpNormalizer`、`StructureNormalizer`）必须 `await` 透传；不允许写 sync 版本（破坏 Liskov 替换 + 触发嵌套 loop）。

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
  config.py             # pydantic-settings
  exception.py          # RAGError（单一全局）
  error_codes.py        # ErrorCode 字面量
  domain/               # Pydantic DTO（无 SQLAlchemy）→ 见 domain/AGENTS.md
  infra/                # pg/ + cache/ + llm/
    pg/                 # PG 基础设施 → 见 infra/pg/AGENTS.md
      base.py           # Base、TimestampMixin、SoftDeleteMixin
      database.py       # engine、AsyncSessionLocal
      models/           # DatasetModel、ChunkModel（仅 FK，无 relationship）
      repositories/     # ChunkRepository 等
      schema.sql        # DDL 参考，须与 models 同步
  ingest/               # reader → normalizer → chunker 三段
    pipeline.py         # IngestPipeline.ingest — 单一 async 入口
    source.py           # IngestSource tagged union（File / Url / Buffer）
    types.py            # DocMeta、TextDoc、Chunk、ChunkMetadata、IngestResult、Heading、DocumentStructure
    cli.py              # `rag-ingest` console script（typer）
    reader/             # bytes + ext → TextDoc；adapters/ 下 10 个格式适配器
    normalizer/         # 可选 LLM 段落改写（NoOp / StructureNormalizer）
    chunker/            # 12-rule 递归切分 + finalize + overlap
tests/                  # 见 tests/AGENTS.md
  unit/
  integration/          # settings.database_url 真实 PG
  data/                 # 共享 fixture（txt/md/html/pdf/docx/pptx/xlsx/csv/json）
```

---

## 异常约定

- 全局仅 **`RAGError`**（`src/rag/exception.py`），字段 **`code` + `message`**，无子类、无 `reason`/`recoverable`/`source` 属性。
- **`code`** 须来自 **`ErrorCode`**（`src/rag/error_codes.py`），格式 `{area}.{detail}`。
- **`message`** 写本次人类可读原因；reader 层惯例 `{source}: {detail}`（路径/URL 进 message，不单独存字段）。
- 链式原因用 `raise RAGError(...) from e`，分支用 `err.code == ErrorCode.XXX`，勿新增领域异常类。

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
