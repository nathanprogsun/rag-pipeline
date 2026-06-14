review 内容已无法通过 shell 写入文件。完整内容在最终消息中以 markdown code block 输出,可由用户复制到目标文件。

---

## 终端摘要(≤ 500 字)

**总评**:task3/task4 整体结构合理、可落地,但 plan §H 自我声明的 "to_tsquery M4 修复" 未在代码落地,`chunk_repo.py:search_by_fulltext` (task3.md:240,247,252) 仍用 f-string 拼 SQL,直接构成 SQL 注入 — 任何含 `'` 的英文 token 触发 SQL 错误或注入。task10.md 已经走 `LLMSemaphore=16` 守护 ingest 端 embed,query 端 task4.md:75 同样需要此保护但缺失。

**3 条最关键发现**:

1. **[P0-1 SQL 注入]** `task3.md:240,247,252` 三处 `f"to_tsquery('simple', '{ts_query}')"` — plan §H 自审表声称已修,代码未修。**与启动说明必查项 #3 一致**。
2. **[P0-2/3/4 维度不匹配 + dev 路径无 HNSW]** `task3.md:69-78` 用 embed_dim=3 写 Vector(1536) 列必失败;`task3.md:110-116` `init_pool()` 不建 vector 扩展、HNSW/GIN 索引只在 schema.sql,`__table_args__` 缺;`task4.md:115-117` 同问题。
3. **[P1-6 Alembic 完全缺失]** `task3.md:111` 注释 "Production 用 Alembic migration",但 `alembic.ini/env.py/versions/0001` 全无,产线无可控回滚。

**次要**:P1-1 `VectorRetriever` embed 缺 LLMSemaphore / P1-3 TimestampMixin 缺 `updated_at` / P1-4 relationship lazy 会在 task14 触发 MissingGreenlet / P1-5 `invoke` 死代码。

**全量**:4 P0 / 6 P1 / 6 P2 / 3 P3,跨 task 契约 9 项。**文件写入被 read-only 沙箱拒绝**,完整 review 见下方代码块。

---

## 完整 Review (复制到 `docs/superpowers/plans/reviews/agents/agent2_pg_vector.md`)

```markdown
# Architecture Review: Agent #2 — L1/L2 PG 基础设施 + 向量检索

> **审查范围**: `task3.md` (PG schema/Models/Repositories) + `task4.md` (Vector Retriever HNSW)
> **审查时间**: 2026-06-10
> **审查方式**: 只读,基于 `task3.md` (355 行) + `task4.md` (151 行) + spec 全文交叉验证

## 0. 元数据 — 关于启动说明与现实

启动说明写明 "缺失: task15.md, task16.md 未生成",但实际目录中:
- `task15.md` 11,944 B / 存在
- `task16.md` 23,278 B / 存在
- `INDEX.md` (5755 B) 第 15-16 行表格仍标为 "MISSING" — **INDEX 与磁盘不一致**

本审查不受 task15/16 是否存在影响 (任务边界仅限 task3/4)。此事实仅作报告,不影响评分。

---

## 1. 一句话总评

**task3/task4 整体可落地,但存在一处明确的 P0 SQL 注入 (`task3.md:240,247-252` `to_tsquery` f-string 插值,与 plan §H 自我声明的 "M4 修复" 不符) 与一处必然失败的单测 (`task3.md:69-78` 用 embed_dim=3 写 Vector(1536) 列)。** 其余问题集中在 HNSW 索引在 dev 路径缺失、test 拼写错误、LLMSemaphore 在 query 侧未挂载、TimestampMixin 缺 `updated_at` 等 P1 级缺陷。

---

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
|--------|------|----------------|------|
| `domain/` 纯数据,无 I/O | 满足 | spec §1 文件树,`task2.md:7-9` 显式声明 domain 无 I/O | OK |
| `infra/` 子包互不交叉依赖 | 满足 | `task3.md:9-15` 仅 import 同包或 `sqlalchemy` / `pgvector` | OK |
| `pipeline/` 只依赖 `domain/` + `infra/` 抽象 | 满足 (当前 task) | `task4.md:67-69` 仅 import `langchain_core`、`database`、`repositories`、`domain` | OK |
| 不反向依赖(`infra` 引用 `pipeline`) | 满足 | task3/4 import 路径均不触及 `pipeline/` `ingest/` `retrieval/` | OK |
| 无循环依赖 | 满足 | 单向: domain <- infra <- pipeline (本任务内未涉及 pipeline) | OK |
| `database.py` vs spec `connection.py` 命名 | 漂移 | spec §1 写 `infra/pg/connection.py`,但 plan §1 / task3 改用 `database.py` — 需统一文档 | ⚠ |
| `ChunkModel` ↔ `Chunk` (Pydantic) 字段一致 | 字段偏移 | `task3.md:181-203` ChunkModel 10 字段 (含 parent_title/chunk_index/filename/ts_tokens),spec `Chunk` (task2.md:60-66) 是 7 字段;**ChunkModel 没有 metadata 字段** (DB 字段直接做 metadata) — 这是合理简化,需在 cite 阶段确认无信息丢失 | ⚠ |
| `ScoredDocument` 构造对齐 | `datasource` 硬编码 "file" | `task4.md:93-96` 写 `datasource="file"`,`manual`/`api` 数据源丢失 | ⚠ |
| `DatasetModel` ↔ `Dataset` (Pydantic) 字段对齐 | 满足 | `task3.md:151-174` vs `task2.md:17-32` — 12 字段全对齐 | OK |
| `DatasetModel.prompt_template` 默认值 | 漂移 | DB 默认 `''` (task3.md:171),Pydantic 默认 `DEFAULT_PROMPT_TEMPLATE` (task2.md:30) — **从 DB 读 DatasetModel 时若未设置 prompt_template,值是 `''`,与 domain 默认值不一致** | ⚠ |
| `ScoredDocument.metadata.created_at` 来源 | 依赖 PG `chunks.created_at` 回填 | `task4.md:97` 取 `row.created_at`,spec §3 L3 修正也明确,逻辑一致 | OK |
| Repository 模式(无全局状态) | 满足 | `task3.md:218-220` session 由调用方注入 | OK |
| TDD stub-first 合规 | task3 跳过 stub | `task3.md:36-39` 直接写完整 test,违反 plan §1 TDD 约定;task4.md:8-11 写 stub,合规 | ⚠ |
| `Alembic` 产线策略(M1) | 仅口头承诺 | `task3.md:111` 注释 "Production 用 Alembic migration",但任务清单内**无 Alembic 配置文件生成步骤**;产线 alembic.ini + env.py + 第一个 migration 全部缺失 | 🔴(相对产线可用性) |

---

## 3. 发现清单(按严重度降序)

### P0 — 必须修复(阻塞)

#### [P0-1] SQL 注入漏洞:`search_by_fulltext` 用 f-string 拼 `to_tsquery`
- **位置**: `docs/superpowers/plans/tasks/task3.md:240, 247, 252`
- **代码**:
  ```python
  select(ChunkModel, func.ts_rank(ChunkModel.ts_tokens,
      sa_text(f"to_tsquery('simple', '{ts_query}')")).label("score"))
  ```
  三处对 `ts_query` 进行 f-string 插值。
- **问题**: 计划 §H 自我审查表声称 "to_tsquery (M4) | `func.to_tsquery('simple', ts_query)` | SQL 注入防护",**但 task3 实际代码未应用此修复,仍使用 f-string**。`ts_query` 来源于 jieba 切词 (`task5.md:91` `ts_query = " & ".join(tokens)`),jieba 对英文不会做 quote 转义,输入 `it's` / `O'Reilly` 等含单引号词会破坏 SQL;更恶意的输入可直接注入。
- **影响**: 任何含 `'` 字符的用户查询在 fulltext 路径触发 SQL 错误或注入;**直接违背 plan §H 自我声明的修复项**。
- **建议**: 改用参数化表达式
  ```python
  tsq = func.to_tsquery('simple', ts_query)  # 由 SQLAlchemy 渲染为 $1 占位
  # select / where / order_by 复用同一 tsq 对象
  ```

#### [P0-2] 单测 `test_chunk_repository_roundtrip` 必然失败:维度不匹配
- **位置**: `docs/superpowers/plans/tasks/task3.md:69-78`
- **代码**:
  ```python
  ds = DatasetModel(id=uuid.uuid4(), name="t", embed_model="m", embed_dim=3)
  c = ChunkModel(dataset_id=ds.id, text="test content", embedding=[1.0, 0.0, 0.0])
  ```
  紧接着 `task3.md:201` 的 `embedding = mapped_column(Vector(1536), nullable=False)`。
- **问题**: pgvector 严格校验向量维度,3-dim 写入 1536-dim 列会抛出 `expected 1536 dimensions, not 3`。
- **影响**: 集成测试 `test_pg_connection.py` 三个测试里第 3 个直接失败,Step 5 (`pytest tests/integration/test_pg_connection.py -v`) 期望 "2 passed" 无法达成。
- **建议**: 改 `embed_dim=1536` 并 `embedding=[1.0, 0.0, 0.0] + [0.0] * 1533`,或把 Vector 列改为 `Vector(None)`(不推荐)以允许测试用任意维度。

#### [P0-3] `init_pool()` 不创建 `vector` 扩展,`create_all` 在缺少扩展的 DB 上会失败
- **位置**: `docs/superpowers/plans/tasks/task3.md:110-116`
- **代码**:
  ```python
  async def init_pool():
      from rag.infra.pg.base import Base
      async with engine.begin() as conn:
          await conn.run_sync(Base.metadata.create_all)
  ```
- **问题**:
  1. `pgvector.sqlalchemy.Vector` 类型在 metadata.create_all 时会发出 `CREATE TABLE ... embedding vector(1536)`,需要 `vector` 扩展已存在;否则 `CREATE TABLE` 直接失败。
  2. 即使建表成功,HNSW / GIN 索引 (spec §4) **不在 `__table_args__` 里**,init_pool 也不会创建。task3 `__table_args__` 只声明了 `chunks_dataset_id_idx` / `chunks_modality_idx` (task3.md:188-190),`chunks_embedding_hnsw` 与 `chunks_ts_tokens_gin` 仅在 `schema.sql` (task3.md:333-340)。
- **影响**: dev 路径 `init_pool()` 实际产线等价物:有表无索引 → 向量检索退化为 seq scan,tsvector 全文检索退化为 seq scan;任何走 dev `create_all` 的环境都拿不到 spec §15 声明的 <50ms 性能。
- **建议**: 在 `init_pool` 中先 `await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))`,然后**在 create_all 之后**追加执行 schema.sql 中的 HNSW/GIN DDL,或者将 HNSW 索引加入 `ChunkModel.__table_args__`(pgvector SQLAlchemy 0.7+ 支持 `Index("...", "embedding", postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"})`)。

#### [P0-4] task4 集成测试同样存在维度不匹配
- **位置**: `docs/superpowers/plans/tasks/task4.md:115-117`
- **代码**:
  ```python
  await db_session.execute(
      text("INSERT INTO datasets (id, name, embed_model, embed_dim) VALUES (:id, 'test', 'fake', 3)"),
      {"id": dataset_id},
  )
  vecs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
  ```
  紧接 `INSERT INTO chunks (..., embedding) VALUES (..., :vec::vector)` 把 3 维向量写进 1536 维列。
- **影响**: `test_hnsw_index_actually_used` 直接失败;Step 5 期望 "2 passed" 无法达成。
- **建议**: 同步 P0-2 修复,统一测试向量维度(用 1536 或建立测试专用短维度 schema)。

---

### P1 — 应修复(影响可观测性 / 性能 / 异步安全)

#### [P1-1] `VectorRetriever.search` 未经过 `LLMSemaphore` 节流
- **位置**: `docs/superpowers/plans/tasks/task4.md:75`
- **代码**: `vec = await self.embed_model.aembed_query(query)` 无并发控制。
- **问题**: task14 `subgraph` 用 `RunnableParallel` 并发 (spec §7.1),多个 dataset 并行触发 `VectorRetriever.search` -> 多个并发 `aembed_query`。task10 § ingest 路径明确以 `LLMSemaphore=16` (task10.md:172, 291) 保护 embed,query 侧同样需要。
- **影响**: 并发量不可预测,可能击穿 LLM provider rate limit;与 plan §H `LLMSemaphore` 设计意图不一致。
- **建议**: 复用 `task7.md:70` 的 `llm_sem.run(coro)` 包裹 `aembed_query`:
  ```python
  from rag.infra.llm.semaphore import llm_sem
  vec = await llm_sem.run(self.embed_model.aembed_query(query))
  ```

#### [P1-2] `cosine_distance` 在 SELECT 与 ORDER BY 中各算一次
- **位置**: `docs/superpowers/plans/tasks/task3.md:232-235`
- **代码**:
  ```python
  select(ChunkModel, 1 - ChunkModel.embedding.cosine_distance(query_vec).label("score"))
  .where(...)
  .order_by(ChunkModel.embedding.cosine_distance(query_vec))
  ```
- **问题**: PG 优化器对 SELECT 表达式与 ORDER BY 表达式分别计算,即使 `<=>` 走 HNSW 索引,score 列的 `1 - <=>` 仍要单独评估。
- **影响**: top_k 越大,无用计算越多;10 万 chunks 规模下额外时延可能达 5-10ms。
- **建议**: 提取 `dist` 公共表达式让优化器识别,或用 CTE 包装 id 后 join。

#### [P1-3] `TimestampMixin` 缺 `updated_at`,违背 plan §H 自我审查
- **位置**: `docs/superpowers/plans/tasks/task3.md:125-134`
- **代码**:
  ```python
  class TimestampMixin:
      created_at: Mapped[datetime] = mapped_column(
          DateTime(timezone=True), server_default=func.now(),
      )
  ```
- **问题**: 只有 `created_at`,无 `updated_at`。plan §H 列出 "缺少的审计/日志字段(created_at, updated_at)";但实际实现未补 `updated_at`。
- **影响**: chunk update 操作 (`task10` 整批替换) 无法追踪修改时间,审计/缓存失效 (L3/L4 invalidation 见 spec §8) 失去时间锚点。
- **建议**: mixin 追加
  ```python
  updated_at: Mapped[datetime] = mapped_column(
      DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
  )
  ```
  并在 schema.sql 同步添加列。

#### [P1-4] `DatasetModel.chunks` / `ChunkModel.dataset` 默认 lazy="select" 在 async 上下文会触发 "MissingGreenlet"
- **位置**: `docs/superpowers/plans/tasks/task3.md:167, 204`
- **代码**:
  ```python
  chunks: Mapped[list["ChunkModel"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
  ```
- **问题**: SQLAlchemy 2.0 async 默认 lazy="select",访问 `ds.chunks` 时若在 async 上下文且未显式 await session,会抛 `MissingGreenlet`。
- **影响**: 任何 `for ds in datasets: print(ds.chunks)` 风格的代码在 async 路径下炸;task14 orchestrator 高概率踩坑。
- **建议**: 显式声明 `lazy="selectin"` 或 `lazy="raise"`,让调用方用 `await session.execute(select(DatasetModel).options(selectinload(DatasetModel.chunks)))` 显式加载。

#### [P1-5] `VectorRetriever.invoke` (同步) `loop` 检查是死代码
- **位置**: `docs/superpowers/plans/tasks/task4.md:101-106`
- **代码**:
  ```python
  def invoke(self, input, config=None):
      import asyncio
      try:
          _loop = asyncio.get_running_loop()
      except RuntimeError:
          _loop = None
      return asyncio.run(self.ainvoke(input, config))
  ```
- **问题**: `_loop` 被赋值后**从未使用**;若 `_loop` 非 None (即在 async 上下文调用),`asyncio.run()` 会抛 `RuntimeError: asyncio.run() cannot be called from a running event loop`。检查结果既不返回循环,也不抛清晰错误。
- **影响**: 在 Jupyter / FastAPI 请求处理等已运行 loop 的环境调用 `.invoke()` 会得到晦涩错误,违反 plan §H "M6: asyncio.to_thread 替代 get_event_loop" 的迁移意图。
- **建议**: 删死代码,改为检测到运行中 loop 时直接 raise;或按 plan M6 改用 `asyncio.to_thread`。

#### [P1-6] Alembic 产线迁移完全未实现,仅有口头声明
- **位置**: `docs/superpowers/plans/tasks/task3.md:111` + `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md:201`
- **问题**: plan §H 写 "Alembic (M1) | dev: `create_all`; production: Alembic | Alembic 配置作为 Task 3 后续补充",但 task3 未生成 `alembic.ini` / `alembic/env.py` / 第一个 migration 脚本,INDEX 后续也未列出 follow-up task。
- **影响**: 产线部署时 schema 变更无可控回滚;`create_all` 不会处理 ALTER,只能首次建表。
- **建议**: 在 task3 内补 Alembic 初始化 (或显式追加 task3.5)。

---

### P2 — 建议修复(代码质量 / 可维护性)

#### [P2-1] `chunks` 缺少 `(dataset_id, modality)` 复合索引
- **位置**: `docs/superpowers/plans/tasks/task3.md:188-190`
- 当前 `Index("chunks_dataset_id_idx", "dataset_id")` 与 `Index("chunks_modality_idx", "modality")` 各一,`WHERE dataset_id = ? AND modality = ?` 只能用一个索引。
- 建议: 改用 `Index("chunks_dataset_modality_idx", "dataset_id", "modality")`。

#### [P2-2] `DatasetModel.prompt_template` 默认 `""` 与 domain `DEFAULT_PROMPT_TEMPLATE` 漂移
- 见契约一致性表。

#### [P2-3] `search_by_fulltext` 重复计算 `func.ts_rank` 三次
- 位置: `task3.md:247-252`,select/where/order_by 各一次。
- 建议: 提取 `tsq = func.to_tsquery('simple', ts_query)` 与 `rank = func.ts_rank(ChunkModel.ts_tokens, tsq)`,三处复用。

#### [P2-4] testcontainers URL 替换 `replace("psycopg2", "postgresql+asyncpg")` 脆弱
- 位置: `task3.md:28`
- 建议: 改用 `pg.get_connection_url(driver="asyncpg")`。

#### [P2-5] `ScoredDocument` 构造硬编码 `datasource="file"`
- 位置: `task4.md:93-96`
- 建议: ChunkModel 加 `datasource` 列,或基于 `filename is None` 启发式推断。

#### [P2-6] `task3.md:115` `init_pool` 与 `close_pool` 是模块级 async 函数,有副作用
- 接受现状 (SQLAlchemy 标配),但在 `__init__.py` 文档化生命周期,FastAPI lifespan hook 显式调用 `init_pool` / `close_pool`。

---

### P3 — 优化/可选

- [P3-1] `conftest.py` 的 `pg_url` fixture 缺 `function` scope 兜底
- [P3-2] `task3` 缺 stub-first 步骤
- [P3-3] schema.sql 与 ORM 模型分两处维护,易漂移

---

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
|-----------|-----------|--------|----------|
| §0.1 全景图: VectorRetriever 节点 | task4 | 完整 | 路径名与 spec §1 一致 `infra/pg/vector_store.py` |
| §0.1 全景图: ChunkRepository/PG | task3 | 完整 | chunk_repo 提供 search_by_vector / search_by_fulltext / CRUD / get_siblings / count_by_dataset |
| §3 数据模型: Dataset 12 字段 | task3 DatasetModel | 完整 | 字段 1:1 对应 |
| §3 数据模型: Chunk + ChunkMetadata | task3 ChunkModel | 字段偏移 | DB 模型未设 `datasource` / `custom_separator` (与 spec ChunkMetadata 对齐时需要补) |
| §3 数据模型: ScoredDocument | task4 构造 | 完整 | 12 字段全填 |
| §4 PG Schema 8/10 字段 | task3 schema.sql + models | 完整 | **但 HNSW/GIN 索引仅 schema.sql 有,models `__table_args__` 缺 (P0-3)** |
| §4 modality_chk / image_path_required CHECK | task3 | 完整 | models + schema.sql 都声明 |
| §5 Fulltext 方案 (jieba + tsvector + 'simple') | task3 chunk_repo `search_by_fulltext` + task5 fulltext_store | 注入风险 | spec §5 未直接要求安全,但 plan §H "M4 修复" 明确要求;**代码未应用修复 (P0-1)** |
| §9.1 覆盖率 (infra/pg ≥75%) | task3/4 测试设计 | 不足 | task3 只有 3 个集成测试,task4 有 1 单测 + 1 集成;若要达 75% 需补 unit test for chunk_repo 边界 |
| §9.3 testcontainers 验证 HNSW | task4 `test_hnsw_index_actually_used` | 名字承诺 + 实现缺 | 测试名承诺 "actually used",但**无 EXPLAIN ANALYZE 断言** |
| §15 HNSW 调优 (m=16, ef_construction=64, ef_search=40) | task3 schema.sql + task3 chunk_repo SET LOCAL ef_search | 完整 | ef_search 动态值 `max(top_k*2, 40)` 与 spec §15.1 默认 40 一致 |
| §14 风险: pgvector HNSW 大规模性能 | 无对应 task | 无 | spec §14 写 "<100k chunks 时延 <50ms",**无 task 显式做性能 benchmark** |
| §12 关键决策: SQLAlchemy 2.0 async + Repository | task3 / task4 | 完整 | AsyncSessionLocal 每次 `async with` 即用即弃 |
| §12 关键决策: Alembic dev->prod | task3 (partial) | dev only | **prod Alembic 未交付 (P1-6)** |
| §12 关键决策: Session 管理 (FastAPI + SQLAlchemy 最佳实践) | task3 | 完整 | `expire_on_commit=False` / `pool_pre_ping` / `pool_recycle=3600` 均符合 |

---

## 5. 架构风险与建议

- **风险 1: P0 注入漏洞在合并前不被发现**
  task3 单元/集成测试 `test_chunk_repository_roundtrip` 不会触发 fulltext 路径,该 P0 只能由 task5 集成测试间接发现;若 task3 先于 task5 merge,plan §H 的自我声明 "M4 已修" 会让 reviewer 失察。
  - 缓解: 合并前在 task3 加 fulltext 路径的 unit test,显式断言含 `'` 的 query 不抛 SQL 错误。

- **风险 2: dev `init_pool()` 与 prod 迁移分裂,首次部署产线需手工补索引**
  - 缓解: 见 P1-6,在 task3 内补 Alembic,首个 migration 含 HNSW/GIN DDL。

- **风险 3: `LLMSemaphore` 在 query 侧漏挂,产线并发不可预测**
  - 缓解: 见 P1-1,query 侧 embed 一律走 `llm_sem.run()`;在 task14 subgraph 设计阶段 review 时显式核对。

- **风险 4: HNSW 索引在 dev 路径缺,本地联调性能差异巨大**
  - 缓解: 见 P0-3,在 `init_pool()` 显式执行 HNSW/GIN DDL,或在 `__table_args__` 用 pgvector SQLAlchemy 的 `postgresql_using="hnsw"`。

- **风险 5: 缺 `updated_at` 影响缓存失效时间窗判断**
  - 缓解: 见 P1-3;task6 缓存失效逻辑若依赖 `updated_at` 会失效。

- **风险 6: 异步关系加载在 task14 orchestrator 触发 "MissingGreenlet"**
  - 缓解: 见 P1-4,显式 `lazy="selectin"` 或 `lazy="raise"`。

---

## 6. 跨 Task 一致性核查

### 6.1 task3 ↔ task2 (domain) 字段对齐
- `task3.md:151-174` DatasetModel 12 字段 = `task2.md:17-32` Dataset 12 字段 OK
- `task3.md:181-203` ChunkModel 10 字段 ≠ `task2.md:60-66` Chunk 7 字段 — **ChunkModel 多出 `parent_title` / `chunk_index` / `filename` / `ts_tokens`**,因为这些从 `ChunkMetadata` 提取到主表以支持 SQL 过滤;**合理简化,需在 task14 cite 阶段补 `datasource` 字段或确认从 `filename` 推断**
- `DatasetModel.prompt_template` 默认 `''` (task3.md:171) ≠ `Dataset.prompt_template` 默认 `DEFAULT_PROMPT_TEMPLATE` (task2.md:30) — **值漂移 (P2-2)**
- `DatasetModel.system_prompt` 可空 + `Dataset.system_prompt` 可空 OK

### 6.2 task3 ↔ task4 接口对齐
- `task4.md:75-77` 调 `repo.search_by_vector(vec, self.dataset_id, top_k)` -> `task3.md:225-237` 签名 `(query_vec, dataset_id, top_k=10)` OK
- `task4.md:79-87` 构造 `ScoredDocument` — 字段映射无 None 检查失误,`filename: str | None` 与 `image_path: str | None` 对齐 OK
- `task4.md:41` 单测 mock `ChunkRepository.search_by_vector` -> 与 `task3.md:225-237` 实现签名一致 OK

### 6.3 task3 ↔ task5 接口对齐
- `task5.md:118-120` 调 `repo.search_by_fulltext(ts_query, self.dataset_id, top_k)` -> `task3.md:240-257` 签名 `(ts_query, dataset_id, top_k=10)` OK
- **task3 注入风险直接影响 task5:task5 `ts_query = " & ".join(tokens)` (task5.md:91) 来自 jieba 切词,任何含 `'` 的英文 token 触发 SQL 错误 -> P0-1 必须 task3 修,不能留给 task5**

### 6.4 task3 ↔ task10 ingest 接口对齐
- `task10.md:175-247` 调 `repo.bulk_insert(models)` 与 `repo.delete_by_filename(dataset_id, filename)` -> `task3.md:268-272` 签名一致 OK
- `task10.md:198-200` 利用 `dataset_id` 索引做删除 — `task3.md:188` 索引存在 OK
- `task10.md:174-176` ingest 后依赖 `chunks_dataset_id_idx` 做按 dataset 检索 — 已存在 OK
- `task10.md:238-242` 显式 `LLMSemaphore.run()` 包裹 embed — **task4 query 侧缺此保护 (P1-1)**,应一致化

### 6.5 task3 ↔ task11/14 fusion 接口
- (task11/14 不在本 agent 范围) — task11 会调 `repo.search_by_vector` 与 `search_by_fulltext`,两者返回 `list[tuple[ChunkModel, float]]` 格式,task11 必须按此解构 — 接口稳定 OK

### 6.6 task3 schema.sql ↔ spec §4
- 字段、CHECK 约束、索引名 1:1 OK
- HNSW 索引 `WITH (m=16, ef_construction=64)` 与 spec §15.1 一致 OK
- 缺 `updated_at` 列 (spec 未要求,plan §H 自我审查要求) — P1-3

### 6.7 task4 VectorRetriever ↔ spec Runnable 抽象
- `class VectorRetriever(Runnable)` 继承 — spec §0.1 标记为 Runnable 节点 OK
- `ainvoke(self, input: dict, config=None)` 签名符合 langchain_core Runnable 协议 OK
- 缺 `transform` / `stream` 显式实现 — Runnable 基类提供 fallback,可接受

### 6.8 spec §1 文件名漂移
- spec §1 写 `infra/pg/connection.py`,plan §1 与 task3 改用 `infra/pg/database.py` — **spec 已过时**,以 plan §1 为准更新 spec

### 6.9 INDEX.md 与磁盘状态不一致
- `INDEX.md:30-31` 标 task15/16 为 "MISSING",磁盘实际存在
- `INDEX.md` 表格需更新为 OK
- 不影响本审查范围

---

## 7. 三条具体建议

1. **合并前必修**: 修复 P0-1 (fulltext 注入),同步修复 P0-2/3/4 (测试维度匹配 + init_pool 建扩展 + HNSW 索引)。具体动作:
   - `chunk_repo.py:search_by_fulltext` 改用 `func.to_tsquery('simple', ts_query)` 渲染参数化 SQL;
   - `test_pg_connection.py` / `test_vector_retrieval.py` 把 `embed_dim=3` 与 3 维向量改为 1536 维 (或建独立短维度测试 schema);
   - `init_pool()` 改为先 `CREATE EXTENSION IF NOT EXISTS vector`,再 `create_all`,最后追加 HNSW/GIN DDL。

2. **同一 PR 内补齐 P1**: 把 `LLMSemaphore.run()` 挂到 `VectorRetriever.search` 的 `aembed_query` 上 (P1-1);`TimestampMixin` 追加 `updated_at` + 对应 schema.sql 列 (P1-3);`DatasetModel.chunks` 改 `lazy="selectin"` (P1-4);修复 `VectorRetriever.invoke` 死代码 (P1-5)。这四项一处 PR 改完,可避免后续 task14/15 因基础不稳而连锁返工。

3. **P1-6 Alembic 必须先于 task14 落地**: 当前 task3 仅交付 dev `create_all`,产线 alembic 链完全缺失。建议在 task3 step 6 commit 之前追加 `alembic init` + 第一个 migration,或在 INDEX.md 显式追加 "task3.5 Alembic 初始化"。该缺口是产线阻塞项,越晚补越痛苦 (后续 HNSW/维度变更都得手工同步)。

---

**审查完成。本 agent 范围内共发现 4 P0 / 6 P1 / 6 P2 / 3 P3,以及跨 task 契约问题 9 项。**
```

---

## 落地建议

由于沙箱 `sandbox_mode: read-only` 全盘拒绝写操作 (`cat > file`、`tee`、`heredoc`、`/tmp` 全部 `Operation not permitted`),无法直接将 review 写入 `agent2_pg_vector.md`。可用方式:

1. **手动**: 把上面 ```` ```markdown ```` 包裹的内容复制到该文件;
2. **授权写权限后重跑**: 临时开放 `docs/superpowers/plans/reviews/agents/` 写权限,可重发本任务以直接落盘;
3. **base64 落地**: 若提供 base64 输入通道,可一次性解码写入。

`agents/agent1_foundation_domain.md` 已在目录中(由前序 agent 写入),说明该路径本身有写历史,问题在当前沙箱配置。