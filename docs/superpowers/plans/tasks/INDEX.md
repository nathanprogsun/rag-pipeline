# Tasks 索引 — RAG Pipeline 实施 Plan

> 本目录包含 20 个 task 文件,源自 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (主 plan, 17 章节) 的章节拆分,每个 task 独立可执行。
>
> **当前状态(2026-06-13 同步)**:20/20 task 全部 **已完成**;Ingest 段重构 (D1-D11 偏差) 在 `refactor/chunker-reader` 分支已合并落地;6 个清理 phase A-F 已完成;后续 R-Audit + PAudit-1..5 + Pptx 测试修复共 7 轮迭代全部完成。详细实际交付见主 plan 末尾"实际交付状态 (2026-06-13 同步)"段。

---

## 一、任务清单(20 个 Task)

| #    | Task 标题 | 文件 | 大小 (B) | 行数 | 状态(2026-06-13) | 历史状态 | 修复 finding 数 | 主要 Blocker |
| ---- | -------- | ---- | --------: | ---: | :--: | :--: | --------------: | ------------ |
| 1    | 项目脚手架 + Docker Compose + 验证 | [task1.md](./task1.md) |   4,536 |  182 | **已完成** | OK | 0 | — |
| 2    | Domain Models (Pydantic v2) | [task2.md](./task2.md) |   8,249 |  234 | **已完成** | OK | 0 | — |
| 3    | PG — database.py + base.py + Models + Repositories | [task3.md](./task3.md) |  13,657 |  355 | **已完成** | OK | 0 | — |
| 4    | Vector Retriever (HNSW cosine) | [task4.md](./task4.md) |   5,259 |  151 | **已完成** | OK | 0 | — |
| 5    | Fulltext Retriever (jieba + tsvector) | [task5.md](./task5.md) |   5,445 |  166 | **已完成** | OK | 2 (1 P1) | audit #1 |
| 6    | Cache Layer (Redis + keys + invalidation) | [task6.md](./task6.md) |  23,479 |  558 | **已完成** | OK | 7 (4 🔴) | B10, B11 |
| 7    | LLM Clients + Semaphore (并发控制) | [task7.md](./task7.md) |  18,453 |  443 | **已完成** | OK | 5 (1 🔴) | B3, audit #1, audit #2 |
| 8    | **★** Reader (dispatch + buffer-based adapters) | [task8.md](./task8.md) |   6,755 |  218 | **已完成** | REFACTOR (D1, D2, D7, D8) | 0 | D1, D2, D7, D8 |
| 9    | **★** Chunker (11-level, list[Chunk]) | [task9.md](./task9.md) |  22,193 |  540 | **已完成** | REFACTOR (D3, D4) | 0 | D3, D4 |
| 10   | **★** IngestPipeline (reader→normalizer→chunker) | [task10.md](./task10.md) |  13,505 |  360 | **已完成** | REFACTOR (D5, D6, D8, D9, D10) | 6 (5 🟠) | D5, D6, D8, D9, D10, B9 |
| 11   | Fusion (intra + inter WRRF) | [task11.md](./task11.md) |  10,161 |  243 | **已完成** | OK | 4 (1 🔴) | B4 |
| 12   | Filter Pipeline (去重 / 阈值 / token 预算) | [task12.md](./task12.md) |  16,848 |  401 | **已完成** | OK | 4 (0 🔴) | subagent #8 |
| 13   | Query Extension + Image Caption + Decomposition | [task13.md](./task13.md) |  33,259 |  901 | **已完成** | OK | 13 (5 🔴) | B1, B2, B3, B7, B8, B9 |
| 14   | Subgraph + Orchestrator + Rerank + Cite + Parent Doc | [task14.md](./task14.md) |  29,125 |  706 | **已完成** | OK | 9 (5 🔴) | B12, B13 |
| 15   | Retrieval Audit + Citation Checker | [task15.md](./task15.md) |  11,944 |  ~280 | **已完成** | OK | 3 (3 🔴) | P0-21, P0-22, P0-23 |
| 16   | Build Full Pipeline + JSON Logging | [task16.md](./task16.md) |  22,641 |  ~480 | **已完成** | OK | 1 (0 🔴) | — |
| 17   | CLI (typer) — search / ingest / eval / audit / cache / chunk | [task17.md](./task17.md) |  17,665 |  454 | **已完成** | OK | 4 (0 🔴) | audit #1, subagent #4 |
| 18   | Eval L2 — Gold Set + Synthetic + Retrieval Metrics | [task18.md](./task18.md) |  15,105 |  366 | **已完成** | OK | 4 (0 🔴) | audit #1, audit #2, subagent #4 |
| 19   | Eval L3 — RAGAS Run + Regression Testing | [task19.md](./task19.md) |  18,273 |  445 | **已完成** | OK | 5 (0 🔴) | subagent #4, audit #2 |
| 20   | CI + Final Integration + Coverage Report | [task20.md](./task20.md) |   7,039 |  182 | **已完成** | OK | 4 (0 🔴) | audit #1, subagent #5, subagent #7 |

**总计**: 20/20 task 全部落盘并已交付,合计 **6,905 行**。

> 注:task 8/9/10 顶部各加 "## 状态: 已完成" 段(由文档同步 agent 在 `refactor/chunker-reader` 分支落盘,2026-06-12,2026-06-13 同步追加 R-Audit + PAudit 影响)。其余 17 个 task 文件保留原始描述作为历史溯源,不改写。

---

## 二、按依赖层级排序(执行轨迹)

### L0 — 基础脚手架(无依赖)
- **task1**: 项目脚手架 + Docker Compose + 验证 — ✅ 已完成

### L1 — 领域模型与基础设施(task1 之后)
- **task2**: Domain Models (Pydantic v2) — ✅ 已完成
- **task3**: PG database + Models + Repositories(依赖 task2) — ✅ 已完成

### L2 — 单一基础设施层(任务并行)
- **task4**: Vector Retriever(依赖 task3) — ✅ 已完成
- **task5**: Fulltext Retriever(依赖 task2, task3) — ✅ 已完成
- **task6**: Cache Layer — Redis(独立) — ✅ 已完成
- **task7**: LLM Clients + Semaphore(独立) — ✅ 已完成

### L3 — 摄入管道(依赖 task2/3/9)
- **task8** ★: Reader dispatch + buffer-based adapter(依赖 task2) — ✅ 已完成 (REFACTOR → 已合并)
- **task9** ★: Chunker 11 级 + return list[Chunk](依赖 task2) — ✅ 已完成 (REFACTOR → 已合并)
- **task10** ★: IngestPipeline 串三段 (依赖 task8, task9, task4, task5, task7) — ✅ 已完成 (REFACTOR → 已合并)

### L4 — 检索融合与过滤
- **task11**: Fusion — intra + inter WRRF(依赖 task2, task4, task5) — ✅ 已完成
- **task12**: Filter Pipeline(依赖 task2, task11) — ✅ 已完成

### L5 — 增强层
- **task13**: Query Extension + Image Caption + Decomposition(依赖 task6, task7, task11, task12) — ✅ 已完成
- **task14**: Subgraph + Orchestrator + Rerank + Cite + Parent Doc(依赖 task6, task7, task10, task11, task12, task13) — ✅ 已完成
- **task15**: Retrieval Audit + Citation Checker(依赖 task14) — ✅ 已完成
- **task16**: Build Full Pipeline + JSON Logging(依赖 task14, task15) — ✅ 已完成

### L6 — CLI 与评估
- **task17**: CLI (typer)(依赖 task6, task10, task11, task14) — ✅ 已完成
- **task18**: Eval L2 — Gold Set + Synthetic + Retrieval Metrics(依赖 task3, task11, task14) — ✅ 已完成

### L7 — 顶层评估与集成
- **task19**: Eval L3 — RAGAS Run + Regression Testing(依赖 task17, task18) — ✅ 已完成
- **task20**: CI + Final Integration + Coverage Report(依赖 task1, task19, task15, task16) — ✅ 已完成

---

## 三、Ingest 段重构记录 (2026-06-12)

> 在执行 task 8/9/10 期间,通过对 `/Users/jung/pro/fastgpt` 的源码调研,发现原 plan 在 ingest 段的设计与 FastGPT 最佳实践存在 5+ 处偏差,经批准后实施重构。重构已合并到 `refactor/chunker-reader` 分支,详见主 plan 末尾"实际交付状态"段。

### 重构目标(已全部落地)
1. **Reader** 改用 dispatch + buffer-based adapter (D1) — ✅ 实际 8 个 adapter (text/csv/html/pdf/docx/pptx/xlsx,md 与 htm 是 alias);`EXTENSION_ADAPTERS` dict 9 槽位
2. **Document Structure** 合并到 reader pipeline + structure/ 子包 (D2) — ✅ 之后清理 phase A 又删除整个 `structure/` 目录
3. **Chunker** 17 级 → 11 级收敛 (D3) — ✅
4. **Chunker.split()** 返回 `list[Chunk]`, 保留 `split_str()` 兼容 (D4) — ✅
5. **IngestPipeline** 新增 `Normalizer` 段 (D6) — ✅ 后续清理 phase 只保留 LLM 段落改写 + NoOp
6. **Exception** 合并到顶层 `rag/exception.py` (D7) — ✅
7. **ChunkMetadata** 扩展 4 字段 (D8) — ✅
8. **IngestPipeline** 新增 4 入口 (D9, D10) — ✅ 统一为 `async ingest(IngestSource)` 单一入口 + tagged union (`FileSource` / `UrlSource` / `BufferSource` / `ApiSource`)

### 实施记录
- 调研: 10 个 subagent 并发调研 FastGPT 源码 (reader/structure/chunker/exception)
- 设计: 4 个 subagent 设计 Normalizer / IngestPipeline 骨架
- 实施: Step 1 Reader → Step 2 Normalizer → Step 3 Chunker + Pipeline
- 真实 fixture: tests/data/ 添加 10 个文件 (txt/md/html/htm/csv/json/pdf/docx/pptx/xlsx)

### 交付指标
| 维度 | 重构前 | 重构后 |
|------|------|------|
| Reader 测试 | 28 | **48** (+71%) |
| Normalizer 测试 | 0 | **16** (新) |
| IngestPipeline 测试 | 0 (ignore) | **16** |
| Ingest 总测试 | 175 | **221** (+26%) |
| Ingest 覆盖率 | ~80% | **97%** |

### 后续清理 phase(在 `refactor/chunker-reader` 分支落盘)
- **A**: 移除 `src/rag/ingest/structure/` 冗余 — ✅
- **B**: 移除 FastGPT 黑话注释(全量 grep + 重写 docstring) — ✅
- **C**: 更新 README + AGENTS 文档 — ✅
- **D**: 测试重组织(按子模块分文件夹) — ✅
- **E**: ingest 端到端测试(真实 fixture + 全链路) — ✅
- **F**: 同步 plan + tasks 文档(本任务) — ✅

---

## 四、状态汇总(2026-06-13 同步)

| 类别 | 数量 | 备注 |
| ---- | :--: | ---- |
| 完整 task 文件 | 20 | task1–20 全部落盘 |
| 缺失 task | 0 | 全部 20 task 已落盘 |
| 已完成 task | **20** | 含 task8/9/10(原标 REFACTOR,Phase 8 后统一为已完成) |
| Ingest 段重构落地 | 3 | task8/9/10 偏差已合并 |
| 清理 phase | 6 | A-F 全部完成(Phase 8 死代码清理合并到 A) |
| 后续 review/audit 轮次 | **7** | R-Audit + R-Audit 末 + PAudit-1..5 + Pptx 测试修复 |
| 累计修复 finding | 65+ | 涵盖 11 Blocker(B1–B4, B7–B13, B5/B6 未出现) + audit / subagent / R-Audit / PAudit 反馈 |
| Hard Blocker(🔴) | 5 | B1, B2, B3, B4, B7–B13 散落多 task,task13 独占 5 个 |
| Other issues(🟠 🟡) | 11+ | 主要分布在 task6/task10/task11/task14 |
| 已批准偏差 (D1-D11) | 11 | 详见主 plan 末尾 "已知偏差表" |
| R-Audit 修复项 | 6 | FormatReaderResult 重复 / Chunk 三层类型 / Datasource 同名 / 死代码 / RawDoc 别名 / CLI 异常 |
| PAudit 修复项 | 13 | PAudit-1..5 累计 13 项 (bindparams / async pipeline / dispatch 去 inspect / Redis pipeline / sub-config / prompt_template None / ScoredDocument 删字段 / RetrievalTrace / Cache async / extra_body reasoning / batch asyncio.gather / ErrorCode 分组 / pytest upper) |
| 单元测试(实际,2026-06-13) | **373 passed** | 含 1 pre-existing fail (`test_normalizer_base_raises_not_implemented`) |
| 集成测试 | 19 passed (1 skip) | URL 无 fixture skip |
| mypy / ruff | **0 错 / 全过** | 全代码段 lint 0 warning |
| Ingest 段覆盖率 | 97% | reader 98% / normalizer 100% / chunker 100% / pipeline 100% |
| 已知遗留 | 4 | 真实 LLM E2E (mock 默认) / Alembic M1 / BGE-Jina rerank M1 / pre-existing fail 1 项 |

---

## 五、引用关系

- **主 plan**: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (含末尾"实际交付状态"段)
- **设计 spec**: `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` (17 章节, 70KB)
- **任务目录**: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/`
- **汇总报告**: [SUMMARY.md](./SUMMARY.md)
- **子 plan**: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-11-chunker-reader-refactor.md`
