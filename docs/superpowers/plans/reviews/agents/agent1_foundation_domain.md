我已收集足够证据。下面输出完整 review(由于 sandbox 写入被拒,内容以纯文本形式提供,可在 sandbox 解禁后落入 `docs/superpowers/plans/reviews/agents/agent1_foundation_domain.md`)。

---

# Architecture Review: Agent #1 — L0/L1 基础脚手架 + Domain 层 (Foundation & Domain)

> **Sandbox 状态**:本次 session filesystem 为 `read-only`,`exec_command` 写入 `docs/superpowers/plans/reviews/agents/agent1_foundation_domain.md` 被内核拒绝(`zsh:1: operation not permitted`)。完整 review 内容在本回复中以 Markdown 给出,可由用户复制落盘。
>
> **task15/16 状态修正**:共同上下文标注"task15.md, task16.md 缺失",与文件实际状态不符——两者已落盘(11944 B / 23278 B),且均含 `Step 0 stub` 修正条目。本 review 默认两者存在,引用时给 file:line。

## 1. 一句话总评

task1 脚手架可用但 `Settings` 在模块导入期即被实例化,library 模式存在"导入即报错"风险;task2 域模型与 spec §3 严重不同步(spec 13 字段 / task 19 字段,`rerank_weight`/`temperature`/`score_threshold` 三个默认值与 spec 不一致),`prompt_template` 在 Pydantic 与 SQL DDL 两端的默认值分裂将导致 DB 回灌路径必崩。

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
|---|---|---|---|
| 分层(domain / infra / pipeline / retrieval / ingest / cli)边界清晰,domain 无 I/O | OK | spec §1 L257-359 目录树;task2 全部为 Pydantic `BaseModel`,无 `import` 任何 infra | ✅ |
| 依赖方向单向(无循环) | OK | task2 内部无 infra/pipeline import;task11/14/17 均从 `rag.domain` 取类型,反向未出现 | ✅ |
| `SearchRequest` 字段集合在 plan / task / spec 一致 | 不一致 | spec §3 L478-494(13 字段) vs task2 L116-135(19 字段);plan `tasks/INDEX.md` 描述未指明字段数 | 🔴 |
| `ScoredDocument.image_path` ↔ cite.py 一致 | OK(H2 已修) | task2 L99 `image_path: str \| None = None`;task14 L370 `h.image_path if h.modality == "image_caption" else None` | ✅ |
| `SearchResult.failed_dataset_ids` / `warnings` ↔ orchestrator 引用 | OK | task2 L150-152 定义;task14 L509-513、L539-542 实际写入 | ✅ |
| `SearchRequest.query_decomposition` / `parent_doc_window` ↔ build_full_pipeline | OK(H3 已修) | task2 L130-131 定义;task16 L26-30 已声明消费 | ✅ |
| `LLMSettings` 单一定义在 `config.py` | OK(H5 已修) | task1 L131-141 定义;task7 L66 `from rag.config import LLMSettings` 显式 import | ✅ |
| `DEFAULT_PROMPT_TEMPLATE` 字符级与 spec §7.6 对齐 | OK | task2 L25-32 与 spec §7.8 L417-422 字节级一致(均含 `## 参考资料` / `{citations}` / `{query}` / `## 回答`) | ✅ |
| `Settings` library / CLI 双模式(env_file 硬编码) | OK(H2 已修) | task1 L126-128 注释明确移除 `env_file`,CLI 入口走 `Settings(_env_file=".env")` | ✅ |
| Pydantic v2 API(无 v1 残留) | OK | task2 仅 `from pydantic import BaseModel, Field`,未使用 v1 `validator` / `Config` | ✅ |
| `exceptions.py` 与 spec §8 对齐 | 不可验证 | spec §8 仅描述 Redis 缓存,未定义异常类;task1 L111-114 凭空定义 4 个异常,无 spec 引用 | ⚠ |
| `Dataset.prompt_template` Pydantic 默认值与 SQL DDL 默认值一致 | 不一致 | Pydantic = `DEFAULT_PROMPT_TEMPLATE`(task2 L49);SQLAlchemy `default=""`(task3 L164);SQL DDL `DEFAULT ''`(task3 L312) | 🔴 |
| task17 用 `Dataset(**row.__dict__)` 安全构造 | 脆弱 | task17 L124 `Dataset(**{k: v for k, v in row.__dict__.items() if not k.startswith("_")})`;会卷入 SQLAlchemy 实例状态(`_sa_instance_state` 等) | ⚠ |

## 3. 发现清单(按严重度降序)

### 🔴 P0 — 必须修复(阻塞)

- **[task1.1] `Settings` 在模块导入期即实例化,library 模式 ImportError 风险**
  - 位置:`docs/superpowers/plans/tasks/task1.md:147` `settings = Settings()  # type: ignore[call-arg]`
  - 问题:`pydantic_settings.BaseSettings` 在实例化时立即读取 `os.environ`,所有 required 字段(`openai_api_key` / `database_url` / `m3_api_key` 等)缺失即抛 `ValidationError`。该语句在模块顶层,意味着 `import rag.config` 必触发校验。
  - 影响:任何 library 用户(没有 `.env`、没有 export `OPENAI_API_KEY`)在 `from rag.config import settings` 即崩。`tests/conftest.py:168` `from rag.config import settings` 也中招——单元测试、CI 容器内、文档生成(未注入 env 时)全部受影响。
  - 建议:改为懒加载 `get_settings()` 函数或 `lru_cache` 修饰;或将 `settings` 实例化推迟到 CLI 入口(`cli/main.py` 启动时显式 `Settings()`)。

- **[task1.2] smoke test 注释与实现不一致,误导后续 reviewer**
  - 位置:`docs/superpowers/plans/tasks/task1.md:168-172`
  - 问题:注释 `# 从 .env 读出` 与 H2 修复后的实现矛盾——`env_file` 已从 `model_config` 移除,`Settings()` 只读 `os.environ`,完全不会读 `.env`。测试名 `test_settings_loads` 也只断言 `openai_api_key` truthy,无法证明 `.env` 加载逻辑。
  - 影响:CLI 入口若忘记 `Settings(_env_file=".env")`,smoke test 仍 PASS(只要 shell 有 env),线上 CLI 静默失败。契约承诺与代码实现脱节。
  - 建议:测试名改为 `test_settings_reads_os_environ`,并新增一个 CLI-only 测试断言 `Settings(_env_file=".env")` 能加载 `.env`;或在 test fixture 里 `monkeypatch.setenv` 验证 `os.environ` 路径。

- **[spec↔task2.1] spec §3 `SearchRequest` 字段集合与 task2 不一致**
  - 位置:spec §3 L478-494(13 字段);task2 L116-135(19 字段)
  - 差异:spec 缺 `image_urls` 之外还缺 6 个字段——`query_decomposition` / `parent_doc_window` / `use_global_rerank` / `audit` / `chat_bg` / `histories`。这些字段在 spec §0.1 L43-44 / L82-83 / L888-891 的"挂载点表"和"流水线全景图"中被引用,但 §3 的 `SearchRequest` 类定义本身没列出来。
  - 影响:任何按 spec §3 实现 SearchRequest 的下游(例如实现 dev 工具的 agent、生成 OpenAPI schema 的脚本)会漏掉 6 个开关,导致与 task16 `build_full_pipeline` 消费时 `TypeError`。
  - 建议:在 spec §3 L478-494 直接粘贴 task2 的 19 字段定义;或显式声明"`SearchRequest` 的完整字段集合以 task2/task16 引用为准,本节给出最小子集"。

- **[spec↔task2.2] `rerank_weight` 默认值 spec 与 task2 不一致**
  - 位置:spec §3 L489 `rerank_weight: float = 0.7`;spec §7.1 L152 `rerank_weight 0.7 混合原 score`;task2 L124 `rerank_weight: float = 0.5`;task14 L6/L26/L230/L676 已 0.5 化(B12 修复)
  - 问题:spec §3 / §7.1 仍是 0.7,代码侧已全部迁移到 0.5;reviewer 读 spec 后会得到"应为 0.7"的错误锚点。
  - 影响:RAGAS 回归时,若按 spec 0.7 重现 baseline,与本仓库 0.5 的实现不可比;后续 agent 写文档/示例时再次回滚到 0.7。
  - 建议:把 spec §3 L489 / §7.1 L152 改为 0.5,并在 spec §7.2 L152 注释里说明"对齐 FastGPT `defaultReRankWeight: 0.5`(spec §3 `rerank_weight` 同步)"。

- **[spec↔task2.3] `temperature` 默认值 spec 与 task2 不一致**
  - 位置:spec §3 L494 `temperature: float = 0.0`;task2 L129 `temperature: float = 0.1`
  - 问题:LLM 抽样温度 0.0 = 贪心解码,0.1 = 近贪心。0.0 时 RAGAS 评估稳定性更好;0.1 时 query 改写有微小变化。spec 与 task2 数值差虽小,但语义差大。
  - 影响:任何按 spec 0.0 配置的文档/示例与代码 0.1 不一致;评测时出现 query 改写不稳定(若未来用 `temperature` 控制 QueryExtension)。
  - 建议:二选一并同步。`SearchRequest` 的 `temperature` 字段实际下游并未消费(task13/14 检索侧无 temperature 概念,只有 LLM 阶段),可考虑从 SearchRequest 移除并下沉到 `LLMSettings`;若保留,spec/task 双修到统一值。

- **[spec↔task2.4] `score_threshold` 默认值 spec 与 task2 不一致**
  - 位置:spec §2 L378 `score_threshold` 默认 `0.0`;spec §3 L486 `score_threshold: float | None = None`;task2 L121 `score_threshold: float \| None = None`
  - 问题:spec §2 默认值表说 0.0,spec §3 类型签名说 `None`,代码说 `None`。三个来源两两冲突。
  - 影响:下游 agent(融合、过滤)需要决定 `None` 时怎么过滤——`None` 的语义是"不按分数过滤",`0.0` 的语义是"丢弃所有 score ≤ 0 的 chunk"。task12 `filter_by_score(hits, score_threshold)` 在 `None` vs `0.0` 下的行为不同。
  - 建议:统一为 `float | None = None`(当前代码选择),并在 spec §2 L378 显式标注"`None` = 不过滤;0.0 = 丢弃所有零分"。

- **[task2↔task3.1] `Dataset.prompt_template` Pydantic 默认 vs SQL DDL 默认分裂**
  - 位置:task2 L49 `prompt_template: str = DEFAULT_PROMPT_TEMPLATE`(多行模板);task3 L164 `prompt_template: Mapped[str] = mapped_column(Text, default="")`;task3 L312 `prompt_template TEXT NOT NULL DEFAULT ''`
  - 问题:入库新建的 `datasets` 行 `prompt_template` 默认为 `''`;task17 L124 `Dataset(**row.__dict__)` 后 `dataset.prompt_template = ""`;task14 L378-380 `build_prompt(query, citations, template=...)` 在 `template=None` 时回退到 `DEFAULT_PROMPT_TEMPLATE` 是 OK 的,但若 `template` 来自 `dataset.prompt_template`(spec §7.6 路径),空字符串会让 `tpl.format(citations=..., query=...)` 失败或返回空 prompt。
  - 影响:CLI 端 ingest 后第一次 search 命中空 prompt;前端收到空字符串无法呈现;`RAGAS faithfulness` 评估直接报"prompt 为空"。
  - 建议:三选一:(a) `DatasetModel` 显式 `default=DEFAULT_PROMPT_TEMPLATE`(需要从 `rag.domain.dataset` 反向 import,破坏分层);(b) `build_prompt` 检测空字符串后回退 `DEFAULT_PROMPT_TEMPLATE`(推荐);(c) `Dataset` 构造时 `model_validator` 把 `""` 重写为 `DEFAULT_PROMPT_TEMPLATE`(在 domain 层修补,数据契约层最干净)。

### 🟠 P1 — 应在合并前修

- **[task1.3] `Settings` 与 `LLMSettings` 字段命名分裂**
  - 位置:task1 L144 `max_concurrent_llm: int = 16`;spec §8.6 L1119 `LLMSettings.max_concurrent: int = 16`;task7 L26 `from rag.infra.llm.semaphore import LLMSettings`(LLMSettings 来自 semaphore.py,不是 config.py)
  - 问题:`Settings.max_concurrent_llm` 与 `LLMSettings.max_concurrent` 同义不同名,导致两套并发控制(全局 settings + 限流 dataclass)字段名不统一,新成员易混。
  - 影响:task7 创建 `LLMSemaphore(settings=LLMSettings(max_concurrent=...))` 时,若有人想"用 `settings.max_concurrent_llm` 当 LLMSettings 的 max_concurrent",需手动映射,无类型提示。
  - 建议:`Settings` 里把字段改名为 `llm_max_concurrent`,或在 `LLMSettings` 加 `from_settings(cls, s: Settings)` 工厂方法显式映射。

- **[task1.4] `exceptions.py` 定义了异常体系,但下游任务不用**
  - 位置:task1 L111-114 定义 `RAGError` / `NoResultsError` / `ConfigError` / `RetrievalError`;task10 L79 用 `RuntimeError`;task13 L121 用 `RuntimeError`;task14 L769/L891/L911 用 `RuntimeError`;task6 L329 用 `RuntimeError`
  - 问题:task1 自定义了 4 个 `RAGError` 子类,后续 task 全部用 `RuntimeError`,自定义异常被绕过,既无 docstring 也没 `__init__` 携带上下文。
  - 影响:caller 无法 `except RAGError` 做统一降级;`RetrievalError` / `NoResultsError` 区分意图落空;错误码/消息结构未定义,日志/告警接入困难。
  - 建议:要么 (a) 把 task1 的 4 个异常删除,统一用 `RuntimeError` 或库无关的 `Exception`,要么 (b) 强制下游 task 在 8.5.1(spec L1134) 降级路径用 `RetrievalError` + `warnings` 双通道(caller 可监控 `warnings` 计数)。

- **[task1.5] pyproject.toml 缺 `pythonpath = ["src"]` 与 `testpaths`**
  - 位置:task1 L48-51 `[tool.pytest.ini_options]` 只有 `asyncio_mode = "auto"`
  - 问题:`src/` 布局下,若忘记 `uv pip install -e ".[dev]"`,pytest 会因找不到 `rag` 包而 `ModuleNotFoundError`。`task1 step 5` 才执行 `uv pip install -e`,在 step 6 之前跑测试就会失败。
  - 影响:首次 clone 仓库后,开发者直接 `uv run pytest` 即崩;CI 镜像若不安装就直接跑测试同样崩。
  - 建议:`[tool.pytest.ini_options]` 增 `pythonpath = ["src"]` + `testpaths = ["tests"]`;或 step 5 移到 step 6 之前并显式 `make install`。

- **[task1.6] `Makefile` 缺 `install` target,首次使用门槛高**
  - 位置:task1 L91-105
  - 问题:dev 流程需要手动 `uv venv && uv pip install -e ".[dev]"`(task1 step 5),Makefile 没有 `install` / `setup` / `sync` 目标,首次使用文档与 Makefile 脱节。
  - 影响:README 若写"make dev",开发者会卡在"没有 make install"。
  - 建议:增 `install:\n\tuv venv && uv pip install -e ".[dev]"`;`dev` target 依赖 `install`。

- **[task1.7] `pyproject.toml` 缺 `langchain-community` 与 `tiktoken`**
  - 位置:task1 L10-26
  - 问题:`langchain-cohere` / `langchain-openai` 覆盖了主要 provider,但 `RecursiveCharacterTextSplitter` 在 0.1+ 已搬到 `langchain-text-splitters`(已声明 ✓);`tiktoken`(token 预算 `filter_by_token_budget` 用,spec §7.5 L946-959)未声明。`tokenizer=None` 降级路径会落到字符级估算(精度差),有 `tiktoken` 才能切到 token 精确切。
  - 影响:filter 阶段 token 预算偏离实际 LLM 上下文窗口,容易截断过多或过少。
  - 建议:`dependencies` 增 `tiktoken>=0.7`。

- **[task2.1] `SearchRequest` 缺 Pydantic validator,边界值无防护**
  - 位置:task2 L116-135
  - 问题:`top_k: int = 10` 应 `gt=0`;`max_tokens: int = 4000` 应 `gt=0`;`temperature: float = 0.1` 应 `0 <= t <= 2`;`parent_doc_window: int = 0` 应 `ge=0`;`max_query_variants: int = 3` 应 `ge=1, le=10`;`score_threshold: float | None = None` 应 `0 <= t <= 1`(None 允许)。
  - 影响:bad input 静默通过 Pydantic 构造,在下游 `intra_fusion` / `filter` 阶段报 `ZeroDivisionError` 或越界错,排障路径长。
  - 建议:`from pydantic import Field`,字段加 `Field(default, ge=0, le=...)`;补单测覆盖边界(`top_k=0` 应抛)。

- **[task2.2] `ScoredDocument.rank` 起始值未约束**
  - 位置:task2 L92 `rank: int`;task11 B4 修复后用 `enumerate(start=1)`;spec §15 主 plan 决策"rank 从 1 开始(标准 RRF)"
  - 问题:`rank: int` 不带 `ge=1` 约束,task11/14/16 需自行保证 `rank >= 1`;若传 0,RRF 公式 `1/(60+0)=1/60` 与 `1/(60+1)=1/61` 数值差虽小但语义错。
  - 建议:`rank: int = Field(..., ge=1)` + 单测覆盖 `rank=0` 抛。

- **[task2.3] `ChunkMetadata.datasource` 用 `Literal`,DB 入库前需转换**
  - 位置:task2 L66 `datasource: Literal["file", "manual", "api"]`;spec §4 L321-340 SQL `modality` 有 `CHECK`,但 `datasource` 字段在 SQL 中**没有**(只有 chunks.modality,没有 chunks.datasource)。task3 L182-198 的 `ChunkModel` 也没列 `datasource` 列。
  - 问题:domain `ChunkMetadata.datasource` 与 DB schema 字段不对应;ingest 时 `Chunk.metadata.datasource` 无法直接落库(没列)。
  - 影响:file 来源类型(用户上传 vs API 灌入 vs 手动)在库中无法区分,审计/失效策略无法按来源差异化。
  - 建议:补 spec §4 `chunks.datasource TEXT` 列(CHECK 约束三个枚举值),task3 的 `ChunkModel` 同步加列;或 Pydantic 端把 `datasource` 改为 `Optional` 并文档化"当前不入库"。

- **[task17.1] `Dataset(**row.__dict__)` 脆弱,SQLAlchemy 实例状态卷入**
  - 位置:`docs/superpowers/plans/tasks/task17.md:124` `datasets.append(Dataset(**{k: v for k, v in row.__dict__.items() if not k.startswith("_")}))`
  - 问题:`row.__dict__` 包含 SQLAlchemy 内部状态(`_sa_instance_state` 等,以 `_` 开头被过滤),但同时包含所有列字段(若新增列未在 `Dataset` 声明会 `TypeError`)。当前字段集刚好匹配,但 schema 演化时 silent break。
  - 影响:schema 升级时,`DatasetModel` 加列 → `Dataset(**row.__dict__)` 在 CLI `search` 时崩,本地能复现但易遗漏。
  - 建议:显式字段列表 `Dataset(id=row.id, name=row.name, embed_model=row.embed_model, ...)`;或写一个 `DatasetModel.to_domain()` 转换方法。

### 🟡 P2 — 建议改进

- **[task1.8] docker-compose 端口硬编码 5432/6379**
  - 位置:task1 L65 / L70
  - 问题:本地已有 PG/Redis 占用 5432/6379 时,`make up` 直接冲突失败。
  - 影响:多项目并行开发者要手动改 compose,体验差。
  - 建议:用 `${POSTGRES_PORT:-5432}` / `${REDIS_PORT:-6379}` 变量化,或 compose 加 `profiles: ["dev"]` 让用户显式启。

- **[task1.9] `tests/conftest.py` 实质只有 1 个 `test_settings_loads`,命名误导**
  - 位置:task1 L167-172
  - 问题:文件叫 `conftest.py`(pytest 共享 fixture 入口),但内容是 1 个 test function。pytest 文档约定 conftest.py 放 fixture。
  - 影响:后续任务往 conftest 加 fixture 时,`test_settings_loads` 会跟 fixture 混在一起,新成员困惑。
  - 建议:smoke test 拆到 `tests/unit/test_config.py`,conftest.py 留给 fixture。

- **[task1.10] `M3-multimodal` 字面量写死,模型名应是 env**
  - 位置:task1 L137 `m3_model: str = "M3-multimodal"`
  - 问题:模型名变化需要改代码;`.env.example` 没列 `M3_MODEL`。
  - 建议:`M3_MODEL` 加进 `.env.example`,默认值保持 `"M3-multimodal"`。

- **[task2.4] `ChunkMetadata.created_at` 与 `ChunkMetadata` 上层 `created_at` 命名冲突**
  - 位置:task2 L57-65 `ChunkMetadata.created_at: datetime | None = None`(spec §3 L441 L3 修正);但 `Chunk` 没有 `created_at`,`ScoredDocument` 也没有。
  - 问题:`created_at` 字段散落在 `ChunkMetadata` / `Dataset` 两处,`Chunk` / `ScoredDocument` 没有,调用方(例如 assemble_citations → Citation.update_time)需要从 metadata 回填,接口不直观。
  - 建议:在 `Chunk` / `ScoredDocument` 顶层补 `created_at: datetime | None = None`,从 `ChunkMetadata` 复制;或显式文档化"`created_at` 只在 metadata 上"。

- **[task2.5] `Citation` 缺 `dataset_name` 字段,前端难展示**
  - 位置:task2 L138-147 `Citation` 字段集
  - 问题:`Citation.source_name: str` 是自由文本,没有 `dataset_id → dataset_name` 反查;前端想显示"来自哪个知识库"需额外 join。
  - 影响:UI 需要在 search 端预 join `DatasetModel.name` 才能填 `source_name`,domain 模型没表达这一约束。
  - 建议:`Citation` 加 `dataset_name: str` 字段(由 `assemble_citations` 在 join `DatasetModel` 时填),或文档化"`source_name` 包含 dataset 名前缀"。

- **[spec§1↔task1] spec §1 L268 列 `exceptions.py` 但 spec body 全文搜不到异常类定义**
  - 位置:spec L268 `├── exceptions.py` 在目录树中;spec L1-L1661 全文无 `RAGError` / `RetrievalError` 等定义
  - 问题:异常体系无 spec 来源,task1 自定义 4 个类后无人复核是否合理。
  - 建议:spec §8(异常/降级章节)补完整异常类树与触发条件,或 task1 的 exceptions.py 删到 1 个 `RAGError` 兜底。

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
|---|---|---|---|
| §1 项目结构 | task1 + plan L40-129 目录树 | ✅ | task1 创建 8 个文件全部对齐 spec §1 L266-269;`exceptions.py` 创建但 spec body 未定义异常 |
| §2 默认值(对齐 FastGPT) | task2(`Dataset` 字段默认) + task3(SQL DEFAULT) | ⚠ | `chunk_size=1000` / `rrf_k=60` / `vector_weight=0.7` / `fulltext_weight=0.3` / `top_k=10` / `embedding_dim=1536` 一致;`score_threshold` spec 写 0.0,task2 写 `None` |
| §3 数据模型 | task2 | 🔴 | SearchRequest 字段数 13 vs 19;`rerank_weight` 0.7 vs 0.5;`temperature` 0.0 vs 0.1;`Dataset` / `Chunk` / `ScoredDocument` / `Citation` / `SearchResult` 字段集一致 |
| §4 PostgreSQL Schema | task3 | ✅ | SQL DDL + SQLAlchemy ORM 双重定义,CHECK 约束齐全;`prompt_template` DEFAULT '' 与 Pydantic DEFAULT_PROMPT_TEMPLATE 冲突 |
| §7.6 prompt 拼接 | task14 L376-385 | ✅ | `build_prompt(query, citations, template)` 实现与 spec §7.6 字节级一致 |
| §7.7 CitationChecker | task15(存在,11,944 B) | ✅ | stub 优先,见 task15.md |
| §7.8 prompt 模板管理 | task2 L47-50 | ✅ | `Dataset.prompt_template` / `system_prompt` 字段在 domain |
| §8.1-8.3 Redis 缓存层级 | task6 | ✅ | L1/L2/L3/L4 TTL 与 key 模式对齐 |
| §8.5.1 Redis 降级 | task16 Step 3 cache_decorator | ✅ | `try/except: pass` throwaway 模式,加 warnings |
| §8.6 LLMSettings 并发控制 | task1 + task7 | ⚠ | task1 定义 `Settings.max_concurrent_llm` 与 spec §8.6 `LLMSettings.max_concurrent` 字段名不统一;`from rag.config import LLMSettings` 已统一 import 源(H5) |
| §8.7 可观测性 | task16(json_handler.py) | ✅ | `JsonLoggingHandler` stub 已在 task16 |
| §11 启动流程 | task1 Makefile + task17 CLI | ✅ | `make up` / `make dev` / `uv run rag search` 与 spec §11 L1461-1480 步骤一致 |
| §15 HNSW 调优 | task3 / task4 | ✅ | `m=16, ef_construction=64` 默认已写入 schema.sql,`ef_search` 走 `SET LOCAL`(H7 修复) |
| §16 Gold Set 格式 | task18 | ✅ | `ground_truth_chunks` / `ground_truth_answer` / `tags` 字段对齐 |
| §17 RAG 评测维度 | task18/19 | ✅ | 鲁棒性 + 幻觉防御本期实施;其余 4 项留作评估期 |

## 5. 架构风险与建议

- **风险 1: spec 与代码长期不同步,reviewer 反复冲突**
  - 表现:spec §3 SearchRequest 13 字段 vs task2 19 字段;`rerank_weight` / `temperature` / `score_threshold` 三处默认值 spec 全部过期。
  - 缓解:把 spec §3 标 "DEPRECATED, see task2.md L116-135 for authoritative definition",或在 spec 顶部加"本 spec 是设计意图,字段细节以 task2/task14 引用为准"的 disclaimer。**根本解决**:task 实施完成后,跑一个 diff 脚本(spec §3 vs task2)自动标注不一致点。

- **风险 2: library 模式导入即校验,破坏"library 不应耦合 env"原则**
  - 表现:`from rag.config import settings` 在 OPENAI_API_KEY 未设时崩。
  - 缓解:懒加载 `get_settings() -> Settings` + `@lru_cache(maxsize=1)`;CLI 入口处显式 `Settings(_env_file=".env")`;library 用户用 `Settings()` 显式构造。
  - 当前 task1 已有 H2 修复(env_file 移除),但模块顶层 `settings = Settings()` 仍是单例陷阱。

- **风险 3: `prompt_template` / `system_prompt` 等 dataset 级配置入库后丢失**
  - 表现:SQL DEFAULT '' 让新建 dataset 的 `prompt_template` 为空,`Dataset(**row.__dict__)` 后 `build_prompt` 用空模板 format 失败。
  - 缓解:`build_prompt` 检测 `template==""` 回退 `DEFAULT_PROMPT_TEMPLATE`;或 schema 改 `DEFAULT DEFAULT_PROMPT_TEMPLATE`(SQL 需 escape 多行字符串,实施成本高);或在 `Dataset` 加 `model_validator(mode='before')` 把 `""` 重写。

- **风险 4: 异常体系死代码化**
  - 表现:`RAGError` / `RetrievalError` 等 4 个自定义异常定义后,下游 task10/13/14/6 全部用 `RuntimeError`。
  - 缓解:在 spec §8.5.1 L1134-1139 显式列出"降级路径必走 `warnings` + 自定义异常,禁止 `RuntimeError`";或在 task1 删 exceptions.py,统一用 `RuntimeError` 配 `warnings`,文档化"语义错误看 warnings,系统错误看 traceback"。

- **风险 5: 跨进程/跨机器的 dataset 缓存失效**
  - 表现:Redis 缓存 L3 失效靠 `dataset_version` 字段(task16 引入),但 `Dataset` / `SearchRequest` 没有 `dataset_version` 字段,task16 通过 `deps["dataset_versions"]` 字典透传——接口隐藏在依赖注入。
  - 缓解:把 `dataset_version: str = "v0"` 加进 `SearchRequest` 或 `Dataset`,让 caller 显式传;或文档化"`dataset_version` 是 deps 内部状态,library caller 不可见"。

## 6. 跨 Task 一致性核查

| 契约点 | 涉及 Task | 状态 | 证据 |
|---|---|---|---|
| `ScoredDocument.image_path` | task2 L99 ↔ task3 ChunkModel.image_path L197 ↔ task14 cite.py L370 ↔ task14 parent_doc.py L444 | ✅ 全部一致 | 4 处均 `image_path: str \| None` |
| `SearchRequest.query_decomposition` 默认 `False` | task2 L130 ↔ task16 build_full_pipeline 消费 | ✅ | task2 默认 False,task16 透传 |
| `SearchRequest.parent_doc_window` 默认 `0` | task2 L131 ↔ task16 透传 ↔ task14 parent_doc.py L401 | ✅ | 0 = 不扩展,与 spec §0.1 L800-802 一致 |
| `SearchRequest.use_global_rerank` 默认 `False` | task2 L132 ↔ task16 G1 节点条件启用 ↔ task14 global_rerank.py L574-620 | ✅ | False 默认,G1 仅在 True 时挂载 |
| `SearchResult.failed_dataset_ids` | task2 L150 ↔ task14 orchestrator.py L509-512 | ✅ | task14 异常时填入 `failed_ids` 列表 |
| `SearchResult.warnings` | task2 L151 ↔ task14 orchestrator.py L513,541 ↔ task6 cache L219(degraded 模式)↔ task16 cache_decorator throwaway | ✅ | 跨层 warnings 收集路径清晰 |
| `LLMSettings` 单一定义源 | task1 L131-141 config.py ↔ task7 L66 显式 import | ✅(H5 修) | 不会重复定义 |
| `Dataset.prompt_template` 默认值 | task2 L49 `DEFAULT_PROMPT_TEMPLATE` ↔ task3 L164 SQLAlchemy `default=""` ↔ task3 L312 SQL DDL `DEFAULT ''` ↔ task14 L378-380 build_prompt 消费 | 🔴 | 三处分裂,DB 回灌必崩 |
| `ChunkMetadata.datasource` Literal | task2 L65 ↔ task3 ChunkModel 缺列 ↔ spec §4 缺列 | 🔴 | 字段无 DB 落点 |
| `search_result.image_path` 传递路径 | task2 `ScoredDocument.image_path` ↔ task14 cite.py L370 读 `h.image_path` ↔ task14 parent_doc.py L444 复制 | ✅ | H2 修复后链路完整 |
| `RAGError` 异常使用 | task1 L111-114 定义 ↔ task6/10/13/14 全用 `RuntimeError` | 🔴 | 定义与使用脱节 |
| `env_file` 移除(H2) | task1 L126-128 ↔ task17 CLI 启动路径 | ⚠ | task17 step 1 未显式 `Settings(_env_file=".env")`,需补 |
| `parent_doc_max_tokens` 字段 | spec §0.1 L128 `默认 2000` ↔ task2 SearchRequest 缺 ↔ task14 parent_doc L401 硬编码 | ⚠ | 字段未在 SearchRequest 暴露,task16 透传缺契约 |

## 7. 3 条具体建议

1. **立即同步 spec §3 `SearchRequest` 到 task2(task1 不动)**:在 spec §3 L478-494 直接粘贴 task2 的 19 字段定义,加注释"本节定义以 task2.md L116-135 为准",并把 §3 L489 `rerank_weight: float = 0.7` / L494 `temperature: float = 0.0` 改 0.5 / 0.1,§2 L378 `score_threshold` 0.0 改 `None`。**耗时 ≤ 15 分钟,消除 4 个 P0 跨 task 冲突源头**。

2. **重写 task1 的 `Settings` 导出为懒加载**:`config.py` 删模块顶层 `settings = Settings()`,改为:
   ```python
   from functools import lru_cache
   @lru_cache(maxsize=1)
   def get_settings() -> Settings: return Settings()
   ```
   CLI 入口(`cli/main.py`)显式 `settings = Settings(_env_file=".env")`;library 用户 `from rag.config import get_settings; get_settings()`。**修复 task1.1 P0 阻塞,提升 library 友好度**。

3. **在 task2 补 `Dataset.model_validator` 修补 DB 端空模板**:
   ```python
   from pydantic import model_validator
   class Dataset(BaseModel):
       ...
       @model_validator(mode='before')
       @classmethod
       def _default_prompt_template(cls, data: Any) -> Any:
           if isinstance(data, dict) and not data.get('prompt_template'):
               data['prompt_template'] = DEFAULT_PROMPT_TEMPLATE
           return data
   ```
   配合 `build_prompt` 在 `template` 为空时也回退到 `DEFAULT_PROMPT_TEMPLATE`,**双层防御,DB 端 `''` 与 Pydantic 端多行模板分裂问题彻底解**。同步在 `Chunk` 加 `datasource` 列(对齐 spec §4 缺漏)。

---

## 摘要(终端)

**1 句总评**:task1 脚手架可用但 `Settings` 模块导入期实例化有 library 模式崩溃风险,task2 与 spec §3 在 3 个字段默认值上不同步,`prompt_template` 在 Pydantic / SQLAlchemy / SQL DDL 三端分裂将导致 DB 回灌路径必崩。

**3 条最关键发现**:
1. **🔴 task1.1**:`settings = Settings()` 在模块顶层(task1.md:147),library 用户导入即崩——`from rag.config import settings` 触发 env 校验。
2. **🔴 spec↔task2**:spec §3 SearchRequest 13 字段 / task2 19 字段,`rerank_weight` spec 0.7 / task 0.5,`temperature` spec 0.0 / task 0.1,`score_threshold` spec 0.0 / task `None`——4 处默认值 spec 全部过期,reviewer 读 spec 必被误导。
3. **🔴 task2↔task3**:`Dataset.prompt_template` Pydantic 默认 = 多行模板,SQLAlchemy `default=""`,SQL DDL `DEFAULT ''`——新建 dataset 入库后 `build_prompt` 用空模板 format 必崩,task17 的 `Dataset(**row.__dict__)` 会把空字符串一路传到 `SearchResult.prompt`。

> **写入状态**:本应写入 `docs/superpowers/plans/reviews/agents/agent1_foundation_domain.md`,因 sandbox `read-only` 被内核拒绝(`zsh:1: operation not permitted`)。完整 Markdown 内容已在上面给出,可由用户复制保存。

---

**Sandbox 状态确认**:写入已被内核拒绝(只读),文件保持空目录。需要解除 sandbox 后才能落盘。

(已尝试命令:`cat > /Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent1_foundation_domain.md` → `zsh:1: operation not permitted`,目录保持空。)

参考资料:
- [plan](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md)
- [spec](/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md)
- [INDEX](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/INDEX.md)
- [task1](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task1.md)
- [task2](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task2.md)
- [task14](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task14.md) (cross-task 引用)
- [task15](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task15.md) (存在,本 review 默认)
- [task16](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task16.md) (存在,本 review 默认)
- [task17](/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task17.md) (cross-task 引用)