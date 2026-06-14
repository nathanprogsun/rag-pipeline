> ⚠️ **DEPRECATED (2026-06-14)** — 本 plan 已被 [`2026-06-14-rag-pipeline-delivery.md`](./2026-06-14-rag-pipeline-delivery.md) 替代。
> 任务 1-10 已按本文交付,任务 11-20 见新 plan 的 M1-M4 里程碑。
> **本文档不再更新**,内容仅保留 git history 可查。
>
> 新 plan 配套文档:
> - [交付 plan](./2026-06-14-rag-pipeline-delivery.md) — 当前 plan
> - [test plan](./test-plan.md) — 覆盖率/集成测试/Eval 启动条件
> - [9 个跨 task 契约](../../../.agents/design/2026-06-14-cross-task-contracts.md) — 接口签名锁定
> - [10 份 audit 报告](./audit/) — 2026-06-14 阶段审计
> - [FastGPT 对齐矩阵](./audit/2026-06-14-task11-20-fastgpt-alignment.md) — input/output/logic 对照
>
> 任务 11-20 已加 `## Open P0s (2026-06-14 audit)` 表格(共 38 个 P0),见各 task 文件顶部。

---

# Python RAG Pipeline Implementation Plan (DEPRECATED)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python library + CLI that replicates FastGPT's query-side RAG pipeline using LangChain (LCEL), PostgreSQL+pgvector, and Redis multi-level cache, with full Eval (L1-L3) and RAGAS support.

**Architecture:** Subgraph-per-dataset parallel + RunnableParallel orchestrator + WRRF fusion + LCEL chain. Domain layer (Pydantic) is pure data, infra layer (PG/Redis/LLM) is dependency-isolated, pipeline layer composes them. CLI is a thin typer wrapper.

**Ingest 三段流水线** (原计划 4 段 Reader → Normalizer → Structure → Chunker): **实际实现 3 段** **Reader → Normalizer (可选) → Chunker**,output `IngestResult` (含 `chunks / title / doc_meta / warnings`)。**Structure 独立段在 Phase 8 已删除**: `src/rag/ingest/structure/` 目录被移除,doc-level `DocumentStructure` 静态抽取改为 chunker 内部 per-chunk regex (`_MD_HEADING_RE` / `_HTML_HEADING_RE` / `_TABLE_RE` / `_CODE_FENCE_RE`) 现场重算 `heading_stack / has_code / has_table / image_refs`。`DocumentStructure` / `Heading` 类型保留在 `types.py` 中以兼容 `ChunkMetadata.heading_path`,但没有独立 stage。

**Tech Stack:** Python 3.12, LangChain 0.3.x, langchain-openai, asyncpg, redis-py asyncio, pydantic v2, jieba, typer, pytest, testcontainers, RAGAS.

**Spec:** `docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` (17 章节, ~1600 行)

**已知偏差 (Approved Divergences)**: 见末尾 "已知偏差表"。这些偏差均在 task 8/9/10 执行期间经 FastGPT 调研后批准, 详见各 task 文件末尾 "Deviation Notes"。

---

## File Structure (实际实现版)

```
/Users/jung/pro/rag-pipeline/
├── pyproject.toml              # uv 依赖管理
├── Makefile                    # dev / test / lint / eval
├── docker-compose.yml          # pgvector + redis
├── .env.example                # OPENAI_API_KEY, M3_BASE_URL 等
│
├── src/rag/
│   ├── __init__.py
│   ├── config.py               # pydantic-settings, LLMSettings / CacheSettings
│   ├── exception.py            # ★ RAGError + NoResultsError + ConfigError + RetrievalError
│   │                          #   + ReaderError + NormalizerError + ChunkerError
│   │
│   ├── domain/                 # 纯数据模型
│   │   ├── dataset.py          # Dataset, DEFAULT_PROMPT_TEMPLATE
│   │   ├── document.py         # ChunkMetadata, Chunk, ScoredDocument
│   │   └── search.py           # SearchRequest, Citation, SearchResult
│   │
│   ├── infra/
│   │   ├── pg/
│   │   │   ├── database.py           # engine + AsyncSessionLocal
│   │   │   ├── base.py               # DeclarativeBase + TimestampMixin
│   │   │   ├── schema.sql
│   │   │   ├── models/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── dataset.py
│   │   │   │   └── chunk.py
│   │   │   ├── repositories/
│   │   │   │   ├── __init__.py
│   │   │   │   └── chunk_repo.py
│   │   │   ├── vector_store.py
│   │   │   └── fulltext_store.py
│   │   ├── cache/
│   │   │   ├── connection.py
│   │   │   ├── keys.py
│   │   │   └── invalidation.py
│   │   └── llm/
│   │       ├── chat.py             # ChatOpenAI(base_url) + get_m3_chat_model() + get_structured_chat_model()
│   │       ├── embed.py            # OpenAIEmbeddings
│   │       ├── rerank.py           # Cohere / BGE / Jina stub
│   │       └── semaphore.py        # LLMSemaphore 并发控制
│   │
│   ├── ingest/                  # ★ 实际三段流水线 (Reader → Normalizer → Chunker)
│   │   ├── __init__.py
│   │   ├── types.py             # DocMeta, TextDoc, Chunk, ChunkMetadata, IngestResult
│   │   ├── source.py            # IngestSource = FileSource | UrlSource | BufferSource | ApiSource
│   │   ├── pipeline.py          # IngestPipeline.ingest(source: IngestSource) -> IngestResult (async)
│   │   ├── cli.py               # `rag-ingest ingest / ingest-url / ingest-buffer`
│   │   │
│   │   ├── reader/              # 段 ① Reader (bytes + ext -> TextDoc, 全 async)
│   │   │   ├── __init__.py      # 公共 API: read_file, read_url, dispatch_bytes
│   │   │   ├── types.py         # FormatReaderResult, UploadFileHandler
│   │   │   ├── dispatch.py      # EXTENSION_ADAPTERS + async dispatch_bytes (8 槽位)
│   │   │   ├── file.py          # sync read_file (asyncio.run 包装 dispatch_bytes)
│   │   │   ├── url.py           # async read_url (httpx)
│   │   │   ├── raw_text.py      # 编码探测 + ascii 降级 + base64 图抽取
│   │   │   ├── html2md.py       # Turndown-like markdown 转换
│   │   │   ├── parse_office.py  # OOXML 解压 (docx/pptx/xlsx 共用)
│   │   │   ├── pdf_text_postprocess.py
│   │   │   └── extensions/      # 8 个 async adapter (md + htm 是 html alias)
│   │   │       ├── base.py      # FormatReaderResult 公共类型
│   │   │       ├── text.py      # text_adapter (txt + md 共用)
│   │   │       ├── html.py      # html_adapter (htm alias)
│   │   │       ├── pdf.py       # pdf_adapter
│   │   │       ├── docx.py      # docx_adapter
│   │   │       ├── pptx.py      # pptx_adapter
│   │   │       ├── csv.py       # csv_adapter (+ format_text: md table)
│   │   │       └── xlsx.py      # xlsx_adapter (+ format_text: md table)
│   │   │
│   │   ├── normalizer/          # 段 ② Normalizer (可选, 仅 LLM 段落改写 + NoOp)
│   │   │   ├── __init__.py      # 公共 API
│   │   │   ├── base.py          # Normalizer 基类 (async)
│   │   │   ├── no_op.py         # NoOpNormalizer (FORBID 等价)
│   │   │   └── structure.py     # ★ StructureNormalizer (3 闸门 + 失败降级)
│   │   │
│   │   │                        # ★ 原计划还有 api_normalizer / json_normalizer /
│   │   │                        #   url_normalizer, 已在清理 phase 删除;
│   │   │                        #   这些职责下沉到 reader adapter (BufferSource 走
│   │   │                        #   dispatch_bytes, ApiSource 走 pipeline._fetch_api)
│   │   │
│   │   │                        # ★ 原计划还有独立 structure/ 子包, 已在 Phase 8
│   │   │                        #   删除; chunker 内部 per-chunk regex 现场算
│   │   │                        #   heading_stack / has_code / has_table / image_refs
│   │   │
│   │   └── chunker/             # 段 ③ Chunker (11 级分隔符, 收敛于业内常见)
│   │       ├── __init__.py      # 公共 API
│   │       ├── types.py         # ★ ChunkContext (frozen dataclass) + per-chunk regex
│   │       ├── settings.py      # ChunkSettings
│   │       ├── core.py          # Chunker (split -> list[Chunk], split_str -> list[str] 兼容)
│   │       ├── rules.py         # ★ 11 级 Rule (custom_sign + H1-H5 + code + html_table + md_table + \n\n + \n + punct_merged)
│   │       ├── recursive.py     # common_split
│   │       ├── code_block.py
│   │       ├── table.py
│   │       ├── overlap.py
│   │       ├── finalize.py
│   │       ├── quality.py
│   │       └── utils.py
│   │
│   ├── pipeline/                # query-side (separate concern)
│   │   ├── subgraph.py
│   │   ├── orchestrator.py
│   │   ├── fusion.py
│   │   ├── rerank.py
│   │   ├── filter.py
│   │   ├── query_ext.py
│   │   ├── image_caption.py
│   │   ├── parent_doc.py
│   │   ├── cache_decorator.py
│   │   ├── full.py
│   │   └── cite.py
│   │
│   ├── retrieval/
│   │   ├── decomposition.py    # Query Decomposer
│   │   ├── lazy_greedy.py      # Submodular Query Selection (spec §0.1)
│   │   ├── audit.py            # Retrieval Audit
│   │   └── citation_check.py   # Citation Validator
│   │
│   └── cli/
│       └── main.py
│
├── tests/
│   ├── conftest.py             # testcontainers + tests/data fixtures
│   ├── data/                   # ★ 真实 fixture (txt/md/html/htm/csv/json/pdf/docx/pptx/xlsx + sample_chat_export.md)
│   │   ├── __init__.py
│   │   └── sample.*
│   ├── unit/
│   │   ├── test_reader_*.py
│   │   ├── test_adapter_*.py
│   │   ├── test_normalizer_*.py
│   │   ├── test_chunker_*.py
│   │   ├── test_ingest_*.py
│   │   └── ...
│   ├── integration/
│   ├── e2e/
│   └── eval/
│       ├── goldset.jsonl
│       ├── synthetic.py
│       ├── retrieval_metrics.py
│       ├── regression.py
│       └── run_ragas.py
│
└── docs/superpowers/{specs,plans}/
```

---

## Task Ordering Rationale

按依赖图执行:foundation → storage → LLM/cache → ingest → pipeline → eval → CLI → 集成。每一阶段产出可独立测试的子集,避免后期大规模返工。

**TDD 执行约定 (stub-first)**:

每个 task 的 step 顺序遵循 **stub → test → implement → verify** 模式:

1. **Step 0: Stub** — 先写最小 stub（空类/函数签名/返回 `...`），确保模块可 import
2. **Step 1: Test** — 写测试，此时 stub 已存在、测试可运行但断言失败（真正的 RED）
3. **Step 2: Run (fail)** — 确认测试因逻辑缺失而失败，而非 ImportError
4. **Step 3: Implement** — 写完整实现
5. **Step 4: Run (pass)** — 确认测试通过
6. **Commit**

理由: 若测试文件 import 不存在的模块，pytest 在收集阶段即崩溃，不会执行任何 test 函数。此失败不含信息量。先 stub 后 test 确保测试在"有意义的断言失败"阶段进入 RED。

---

## Task 索引

所有 20 个 task 已拆分到独立文件,详见 [`tasks/INDEX.md`](./tasks/INDEX.md)。

| #  | 标题                                                       | 文件                                       | 状态      |
| -- | ---------------------------------------------------------- | ------------------------------------------ | --------- |
| 1  | 项目脚手架 + Docker Compose + 验证                         | [task1.md](./tasks/task1.md)               | OK        |
| 2  | Domain Models (Pydantic v2)                                | [task2.md](./tasks/task2.md)               | OK        |
| 3  | PG — database.py + base.py + Models + Repositories         | [task3.md](./tasks/task3.md)               | OK        |
| 4  | Vector Retriever (HNSW cosine)                             | [task4.md](./tasks/task4.md)               | OK        |
| 5  | Fulltext Retriever (jieba + tsvector)                      | [task5.md](./tasks/task5.md)               | OK        |
| 6  | Cache Layer (Redis + keys + invalidation)                  | [task6.md](./tasks/task6.md)               | OK        |
| 7  | LLM Clients + Semaphore (并发控制)                         | [task7.md](./tasks/task7.md)               | OK        |
| 8  | ★ Reader (dispatch + 8 async adapters, 含 docx/pptx/xlsx/csv) | [task8.md](./tasks/task8.md)               | REFACTOR  |
| 9  | ★ Chunker (11-level, split -> list[Chunk])                | [task9.md](./tasks/task9.md)               | REFACTOR  |
| 10 | ★ IngestPipeline (reader → normalizer → chunker, async)   | [task10.md](./tasks/task10.md)             | REFACTOR  |
| 11 | Fusion (intra + inter WRRF)                                | [task11.md](./tasks/task11.md)             | OK        |
| 12 | Filter Pipeline (去重 / 阈值 / token 预算)                 | [task12.md](./tasks/task12.md)             | OK        |
| 13 | Query Extension + Image Caption + Decomposition            | [task13.md](./tasks/task13.md)             | OK        |
| 14 | Subgraph + Orchestrator + Rerank + Cite + Parent Doc       | [task14.md](./tasks/task14.md)             | OK        |
| 15 | Retrieval Audit + Citation Checker                         | [task15.md](./tasks/task15.md)             | OK        |
| 16 | Build Full Pipeline + JSON Logging                        | [task16.md](./tasks/task16.md)             | OK        |
| 17 | CLI (typer) — search / ingest / eval / audit / cache / chunk | [task17.md](./tasks/task17.md)             | OK        |
| 18 | Eval L2 — Gold Set + Synthetic + Retrieval Metrics         | [task18.md](./tasks/task18.md)             | OK        |
| 19 | Eval L3 — RAGAS Run + Regression Testing                   | [task19.md](./tasks/task19.md)             | OK        |
| 20 | CI + Final Integration + Coverage Report                  | [task20.md](./tasks/task20.md)             | OK        |

**任务依赖图(高层)**:

```
L0:  Task 1
L1:  Task 2, Task 3
L2:  Task 4, Task 5, Task 6, Task 7
L3:  Task 8 (Reader, 8 async adapters), Task 9 (Chunker)
L4:  Task 10 (IngestPipeline 串三段)
L5:  Task 11, Task 12
L6:  Task 13, Task 14
L7:  Task 15, Task 16
L8:  Task 17, Task 18
L9:  Task 19, Task 20
```

详见 `tasks/INDEX.md §二` 的逐 task 依赖关系。

---

## Self-Review Checklist

- [x] **Spec coverage**: spec 17 章节都有 task 对应(Task 1-20); 新增 rerank / robustness / L1 eval / CLI eval/cache/chunk 子命令
- [x] **Placeholder scan**: 全文搜索 "TBD" "TODO" "implement later" — 0 命中
- [x] **Type consistency**:
  - `ScoredDocument.image_path` (Task 2) ← `cite.py` (Task 14) ✓ (H2 修正)
  - `SearchRequest.query_decomposition` (Task 2) ↔ `build_full_pipeline` (Task 16) ✓ (H3 修正)
  - `SearchRequest.parent_doc_window` (Task 2) ↔ `build_full_pipeline` (Task 16) ✓
  - `SearchResult.failed_dataset_ids` (Task 2) ↔ `orchestrator.py` (Task 14) ✓
  - `LLMSettings` 单一定义在 `config.py`, `semaphore.py` import ✓ (H5 修正)
  - `itemgetter("query")` → `RunnableLambda` 修正 ✓ (C3 修正)
  - ★ `ChunkMetadata` 扩展 4 字段 (source / file_type / page_count / encoding) — Task 10
  - ★ `DocMeta.extra` 字段允许 reader 预抽 structure 塞入 — Task 8
  - ★ `ReaderError.source` (was `path`) + 顶层 `rag/exception.py` — Task 8
- [x] **File paths**: 所有 path 与 spec §1 目录树对齐; 新增 `ingest/reader/extensions/` (8 async adapter), `ingest/normalizer/structure.py` (LLM 段落改写), `chunker/types.py:ChunkContext`
- [x] **TDD**: 每个 task 都先写测试,确认 fail,再实现,确认 pass
- [x] **DRY**: 共享工具(`build_tsvector`, `Chunker`, `LLMSemaphore`, `ChunkContext`)在 Task 中只定义一次
- [x] **YAGNI**: 没有 spec 外的能力(无 graphrag, 无 multi-tenant, 无 AB)
- [x] **Frequent commits**: 每个 task 至少 1 个 commit

## 实施时已知 trade-off

| 决策 | 选择 | 理由 |
|------|------|------|
| 测试隔离 | testcontainers 起真 PG/Redis | 避免 mock 漂移,与 spec §9.3 对齐 |
| Embed 延迟 | E2E 用 mock embed | CI 不依赖真实 LLM,降本 |
| 默认值 | 1000 chunk_size / 1536 dim / rrf_k=60 | rrf_k per-dataset 可配 (spec §0.1) |
| pytest-asyncio | 全部 async 测试 | 流水线原生 async |
| LangChain 版本 | 0.3.x 锁定 | spec §14 风险,避免 API 漂移 |
| coverage 阈值 | 80% | spec §9.1 全局阈值 |
| Redis URL | localhost:6379/0 默认 | 与 docker-compose 一致 |
| CI 容器管理 | testcontainers 自动启停 | 避免 docker compose 端口冲突 (L6) |
| ★ Chunker 收敛 | 11 级 (从 17 级) | 5 punct 合并 1 + H1-H5 保留 + html_table / md_table 各自独立 rule (旧版的 `html_table → md_table` 二级拆分已废,两表规则并行) |
| ★ Chunker.split 签名 | 返回 `list[Chunk]` (主) + `split_str` 返回 `list[str]` (兼容) | Pipeline 注入 ctx,旧测试零修改 |
| ★ Reader 架构 | dispatch + **8 个 async buffer-based adapter** (txt/md/html/htm/pdf/docx/pptx/csv/xlsx) | 调研: buffer 是唯一输入;md + htm 共享 text/html 适配器,docx/pptx/xlsx 各占一槽,json 不支持 |
| ★ Normalizer | 三道闸门 (FORBID / AUTO / FORCE) | 业内常见 LLM 段落改写对位设计 |
| ★ Normalizer 失败 | 降级到 raw_text (vs 原计划硬失败) | 用户友好 + 库不依赖外部 worker |
| ★ tests/data 真实 fixture | 11 个文件 (txt/md/html/htm/csv/json/pdf/docx/pptx/xlsx + sample_chat_export.md) | 端到端验证 reader 全链路,json 用于非 reader 路径测试 |
| BGE/Jina rerank | Cohere 实现, BGE/Jina stub | spec 提及, 二期补全 (M1) |
| DB 层架构 | SQLAlchemy 2.0 async + Repository 模式 | AsyncSessionLocal 每次创建新 session, 自动回收到 pool |
| Session 管理 | 无全局状态, `AsyncSessionLocal()` 即用即弃 | 参考 FastAPI + SQLAlchemy 最佳实践 |
| vlm.py | 移除, 用 ChatOpenAI(M3) 替代 | 多模态 LLM 就是 chat model, 不需要独立封装 |
| Alembic (M1) | dev: `create_all`; production: Alembic | Alembic 配置作为 Task 3 后续补充 |
| Circuit breaker (L1) | Redis 1s timeout + degradation | 中规模满足; v2 加 circuit breaker |
| RRF rank 起始值 (M3) | rank 从 1 开始 (标准 RRF) | 与学术界/Elasticsearch 一致 |
| with_structured_output (H3) | `method="function_calling"` | MiniMax M3 兼容; json_schema 不可用 |
| CitationChecker regex (H6) | `\[([\d,\s]+)\]` | 兼容 [1] [1,2,3] [1, 2, 3] 格式 |
| ef_search (H7) | 查询前 `SET LOCAL hnsw.ef_search` | top_k > 40 时防止静默截断 |
| to_tsquery (M4) | `func.to_tsquery('simple', ts_query)` | SQL 注入防护 |
| asyncio.to_thread (M6) | 替代 deprecated `get_event_loop().run_in_executor` | Python 3.12+ 推荐 |
| env_file (H2) | 移除 library 硬编码; CLI 显式传入 | library/CLI 双模式兼容 |
| CI coverage (H10) | `coverage report --fail-under=80` 合并 unit+integration | 全局覆盖率阈值 |
| ★ Exception 合并 | `rag/exception.py` (顶层) 替代 `ingest/exceptions.py` | 与 `RAGError` 基类统一, 7 个异常集中 |
| ★ DocMeta.extra | ~~reader 预抽 structure 塞入 (HTML heading 需在 strip 前抽)~~ → **已删除**: structure/ 目录在 Phase 8 删除后,reader 不再预抽,`DocMeta.extras` 字段保留为通用扩展位但当前 ingest 路径不主动填充 | 原计划是为了解 strip 后无法抽 heading 的问题,后续 chunker per-chunk regex 不再依赖 doc-level 静态抽取,extras 字段保留仅为未来扩展 |

---

## 已知偏差表 (Approved Divergences from Original Plan)

> 2026-06-12 调研 FastGPT 源码后批准,执行 task 8/9/10 期间生效。

| #  | 原 plan 设计 | 实际实现 | 批准理由 | 批准日期 |
|---|------------|---------|---------|----------|
| D1 | Reader 用 path-based registry | 改用 **dispatch + 8 个 async buffer-based adapter** (txt/md/html/htm/pdf/docx/pptx/csv/xlsx) | 调研: `readRawTextByLocalFile` 把 path 读成 buffer 后再分发,buffer 是唯一输入;后扩 docx/pptx/xlsx 三槽位,json 不在支持范围 | 2026-06-12 |
| D2 | Document Structure 是独立 stage | ~~合并为 **structure/ 子包**~~ → **Phase 8 进一步删除** `src/rag/ingest/structure/` 整目录,改为 chunker 内部 per-chunk regex 现场重算 heading_stack / has_code / has_table / image_refs;`DocumentStructure` / `Heading` 类型保留兼容 | 调研: heading/code/table 抽取是 reader 的副作用,不需要独立 stage;后期发现 doc-level 静态抽取与 chunk 视角不一一对应,直接下移到 chunker 内部 regex | 2026-06-12 (追加 2026-06-13) |
| D3 | Chunker 17 级分隔符 | 收敛到 **11 级** (5 punct 合并, html_table 与 md_table 保留为两条独立 rule) | 5 punct 合并 overlap 行为一致;**原计划"html_table 删除"是误判**,实际 html_table 与 md_table 是不同分隔对象,各自独立 rule 并行 | 2026-06-12 |
| D4 | Chunker.split() 返回 `list[str]` | 改返回 `list[Chunk]`,保留 `split_str()` 兼容 | Pipeline 需要 ctx 注入 DocMeta 到每块,Chunker 必须返回 Chunk 对象 | 2026-06-12 |
| D5 | Reader 输出 `tuple[str, DocMeta]` | ~~改返回 `RawDoc` (含 text + meta + 结构 hint)~~ → **后续 RawDoc 合并到 TextDoc**,reader 直接产 TextDoc (含 `text / format_text / meta / images`),Pipeline 链路只传一种类型 | `MongoDatasetData` 风格: text + meta 打包;后清理 phase 直接合并 RawDoc / TextDoc 二层类型,消除重复 | 2026-06-12 (追加 2026-06-13) |
| D6 | 无 Normalizer stage | **新增 Normalizer (3 闸门)** 作为独立段 | FastGPT `requestLLMPargraph` (datasetParse.ts:40-94): rawText → LLM 改写 → resultText,三道闸门 (FORBID/AUTO-md-skip/FORCE) | 2026-06-12 |
| D7 | `ingest/exceptions.py` 单独 | 合并到顶层 **`rag/exception.py`** | 与 `RAGError` / `NoResultsError` / `ConfigError` / `RetrievalError` 统一,7 个异常集中 | 2026-06-12 |
| D8 | `ChunkMetadata` 仅 8 字段 | 扩展 4 字段 (**source / file_type / page_count / encoding**) | 来自 DocMeta 注入,审计/缓存/检索需要 | 2026-06-12 |
| D9 | 单一 `ingest_url` 入口 | 新增 `ingest_url` + `ingest_buffer` + `ingest_file` 三入口 | FastGPT `readDatasetSourceRawText` 区分 3 source type;库场景不引入 worker pool,但 public API 完整 | 2026-06-12 |
| D10 | Reader 假设路径存在 | 增 `ingest_buffer` 接受 bytes + file_type + source | 库场景常见 (webhook / API 接收上传) | 2026-06-12 |
| D11 | 测试用 `tmp_path` 即时写 | **tests/data/ 8 个真实 fixture** (含 PDF 三页 + DOCX 中文 + HTML 标签) | 端到端覆盖 reader 全链路,避免 mock 漂移 | 2026-06-12 |

---

## Ingest 段交付状态 (2026-06-12)

| 段 | 文件 | 状态 | 测试数 | 覆盖率 |
|---|------|------|-------|--------|
| Reader | `src/rag/ingest/reader/` | ✅ 重构完成 | 48 | 98% |
| Normalizer | `src/rag/ingest/normalizer/` | ✅ 三闸门实现 | 16 | 100% |
| Chunker | `src/rag/ingest/chunker/` | ✅ 11 级 + list[Chunk] | 65+ | 100% |
| Pipeline | `src/rag/ingest/pipeline.py` | ✅ 串三段 | 16 | 100% |
| 真实 fixture | `tests/data/` | ✅ 8 文件 | — | — |
| 合并: Ingest 总覆盖率 | | | 221 测试 | **97%** |

---

## 实际交付状态 (2026-06-12 同步)

> 以下反映 `refactor/chunker-reader` 分支上当前实际落地状态,包括 plan 制定后陆续完成的 7 个实施 phase + 6 个清理任务。所有"历史溯源"内容(D1-D11 偏差表、Ingest 段交付表)均保留在原位置,本节只追加"实际 vs 计划"差异,不改写。

### 1. 七个实施 phase (顺序完成)

| # | Phase 名 | 一句话交付 |
|---|---------|-----------|
| 1 | DTO 修复 / Domain 收紧 | `rag.domain` 下的 Pydantic v2 模型按 task2 落盘,`Document` / `Chunk` / `ScoredDocument` / `SearchRequest` / `SearchResult` 字段冻结;`ChunkMetadata` 在 Phase 2 之后扩展 4 字段 (D8)。 |
| 2 | Chunker 重构 (17→11 级 + list[Chunk]) | `src/rag/ingest/chunker/` 子包 (10 文件): `core / rules / recursive / code_block / table / overlap / finalize / utils / types / settings / __init__`。`Chunker.split(text, ctx) -> list[Chunk]`,`split_str` 兼容旧 API。11 级 Rule 见 task9.md。 |
| 3 | Reader dispatch + buffer-based adapter | `src/rag/ingest/reader/` 子包: `dispatch / file / url / types / adapters/(10 个) / __init__`。**实际 11 个 dispatch 槽位** (txt/md/html/htm/pdf/docx/csv/json/api/pptx/xlsx),`htm` 是 `html` 别名。 |
| 4 | Normalizer 三闸门 | `src/rag/ingest/normalizer/` 子包 (3 文件): `base / no_op / structure / __init__`。**只保留 LLM 段落改写 + NoOp**;旧的 `api_normalizer / json_normalizer / url_normalizer` 已在清理 phase 删除,这些职责下沉到 reader adapter。 |
| 5 | IngestPipeline 串三段 | `src/rag/ingest/pipeline.py` (146 行): 单一 `async ingest(IngestSource) -> IngestResult` 入口,内部 reader → normalizer → chunker;`DocMeta` 通过 `ChunkContext.from_meta_and_structure` 注入每 chunk。 |
| 6 | CLI 入口 | `src/rag/ingest/cli.py` (132 行) + `pyproject.toml [project.scripts]`: `rag-ingest ingest FILE / ingest-url URL / ingest-buffer FILE TYPE` 三个子命令,共享 `_run_ingest(source)` 帮助函数,doc-level 输出 + chunk 预览。 |
| 7 | E2E 真实数据 + 端到端集成 | `tests/data/` 8 个真实 fixture (txt/md/html/htm/csv/json/pdf/docx/pptx/xlsx) + `tests/unit/{reader,chunker,normalizer,ingest}/` 子目录按段组织,`test_reader_e2e.py` + `test_chunker_e2e.py` + `test_ingest_pipeline_*.py` 覆盖全链路。 |

### 2. 六个清理任务 (commit 序列,按时间顺序)

| # | 任务 | 关键动作 |
|---|------|---------|
| A | **移除 `src/rag/ingest/structure/` 冗余** | `DocumentStructure` 静态抽取从 reader 阶段彻底下移,chunker 内部 per-chunk regex (_MD_HEADING_RE / _HTML_HEADING_RE / _TABLE_RE / _CODE_FENCE_RE) 现场重算 `heading_stack / has_code / has_table / image_refs`。`structure/` 目录删除,`pipeline._ensure_structure` 兜底也删除。`DocumentStructure` / `Heading` 类型保留以兼容 `ChunkMetadata.heading_path`。 |
| B | **移除 FastGPT 黑话注释** | 全量 `grep` 文件头 docstring / 行内注释,删除 `FastGPT` / `MongoDatasetData` / `requestLLMPargraph` / `readRawTextByLocalFile` 等内部代号,改为通用库语言 ("FastGPT 对位" → "业界常见 RAG 平台的对位设计"),`deviation` 历史溯源只保留在 `docs/superpowers/plans/` 内的 plan/task 文档。 |
| C | **更新 README + AGENTS 文档** | `README.md` 顶部加 quickstart (CLI + library 两段),`AGENTS.md` 增"Ingest 三段流水线"章节,IngestSource / IngestResult / Chunk 公共 API 全部上 README 的"Library API"小节。 |
| D | **测试重组织 (按子模块分文件夹)** | `tests/unit/ingest/ / reader/ / chunker/ / normalizer/` 四子目录对应源码 4 段,`tests/unit/core/` 保留 domain + infra + pipeline 共享测试;`test_adapter_*.py` 14 个文件按格式拆开,`test_ingest_pipeline_*.py` 按入口 (file/csv/docx/json/...) 拆开。 |
| E | **ingest 端到端测试 (真实 LLM + 真实数据)** | 已有 fixture 复用,`test_reader_e2e.py` 走 10 个 adapter 全链路,`test_chunker_e2e.py` 跑真实 MD/HTML/PDF/DOCX 文本经 11 级 Rule 出 chunk 列表,`test_ingest_pipeline_*.py` 走 `IngestPipeline.ingest(FileSource(...))` 完整路径。**真实 LLM 调用不在默认测试集**(走 mock,见 `test_normalizer_structure.py`),E2E 真实 LLM 跑通走手测脚本,默认 CI 不依赖外部 key。 |
| F | **同步 plan + tasks 文档 (本任务)** | `2026-06-10-python-rag-pipeline.md` 末尾追加本"实际交付状态"段;`tasks/INDEX.md` 重写状态列;`tasks/task8.md / task9.md / task10.md` 顶部加"## 状态: 已完成"段;其余 task 文件保留原始描述作为历史溯源,不删不改。 |

### 3. 文件统计 (本次同步时快照)

| 维度 | 数量 | 备注 |
|------|----:|------|
| `src/rag/` Python 源文件 | 67 | 跨 `domain/ infra/ ingest/ pipeline/ retrieval/ cli/` |
| `src/rag/ingest/` Python 源文件 | 36 | 覆盖 reader(15) + chunker(10) + normalizer(4) + pipeline + types + source + cli + __init__ |
| `tests/` Python 文件 | 60 | 含 conftest + 子目录 `__init__` |
| `tests/test_*.py` 测试文件 | 49 | 跨 unit / integration / e2e / eval |
| `tests/unit/ingest/` 测试文件 | 6 | 含 `test_ingest_exceptions.py` + 5 个 `test_ingest_pipeline_*.py` |
| `tests/unit/reader/` 测试文件 | 14 | `test_adapter_*.py` (10) + dispatch/url/fixtures/e2e/errors (4) |
| `tests/unit/chunker/` 测试文件 | 9 | 每条 rule 路径 1 文件 + settings + e2e |
| `tests/unit/normalizer/` 测试文件 | 1 | `test_normalizer_structure.py`(16 测试) |
| `tests/data/` 真实 fixture | 10 | txt / md / html / htm / csv / json / pdf / docx / pptx / xlsx |
| 单元 + 集成测试 (本分支实际跑通) | **281 passed** | mypy 0 错,ruff 全过,见 `make test` 输出 |
| Ingest 段覆盖率 | 97% | reader 98% / normalizer 100% / chunker 100% / pipeline 100% |

### 4. 入口示例 (当前 `refactor/chunker-reader` 分支)

#### 4.1 Library API

```python
import asyncio
from pathlib import Path
from rag.ingest import IngestPipeline, Chunker, ChunkSettings
from rag.ingest.normalizer import NoOpNormalizer
from rag.ingest.source import FileSource, UrlSource, BufferSource

# 1) 默认 pipeline (NoOp normalizer + 默认 chunker)
pipeline = IngestPipeline(chunker=Chunker(settings=ChunkSettings()))
result = asyncio.run(pipeline.ingest(FileSource(Path("tests/data/sample.md"))))
print(result.title, len(result.chunks), result.doc_meta.source)

# 2) URL 入口
result = asyncio.run(pipeline.ingest(UrlSource("https://example.com/page.html")))

# 3) Buffer 入口 (webhook / API 接收上传常见)
result = asyncio.run(pipeline.ingest(
    BufferSource(buf=open("tests/data/sample.pdf", "rb").read(), file_type="pdf", source="upload:42")
))

# 4) 启用 LLM 段落改写 (三闸门 AUTO 模式,失败降级)
from rag.ingest.normalizer import StructureNormalizer, StructureMode
pipeline = IngestPipeline(
    chunker=Chunker(settings=ChunkSettings()),
    normalizer=StructureNormalizer(mode=StructureMode.AUTO),
)
```

#### 4.2 CLI

```bash
# 文件 ingest
uv run rag-ingest ingest tests/data/sample.md
uv run rag-ingest ingest tests/data/sample.pdf
uv run rag-ingest ingest tests/data/sample.docx

# URL ingest
uv run rag-ingest ingest-url https://example.com/article.html

# Buffer 入口 (强制 file_type,常见 webhook 场景)
uv run rag-ingest ingest-buffer tests/data/sample.json json
```

输出格式 (doc-level + chunk 预览):

```
[1/3] tests/data/sample.md: title=...
[1/3] tests/data/sample.md: 12 chunks
[1/3] tests/data/sample.md: [0/12] source=tests/data/sample.md file_type=md: # FastGPT 对位设计 | FastGPT 调研显示 buffer 是唯一输入,dispatch 走 ...
```

### 5. 实际与计划的关键差异 (汇总)

| # | 计划 | 实际 | 原因 |
|---|------|------|------|
| 1 | reader 7 个 adapter | **实际 11 个 dispatch 槽位** (`htm` 共享 `html` adapter) | 调研补 pptx/xlsx/api_response,htm 是 html 的别名入口 |
| 2 | normalizer 含 `api_normalizer / json_normalizer / url_normalizer` 4 段 | **只保留 `StructureNormalizer + NoOpNormalizer`** | 这些职责下沉到 reader adapter,职责单一化 |
| 3 | `src/rag/ingest/structure/` 独立子包 | **目录已删** | chunker 内部 per-chunk regex 现场算 heading_stack 更直接,doc-level 静态抽取是冗余 |
| 4 | `IngestPipeline.ingest_file / ingest_url / ingest_buffer` 三方法 | **统一 `async ingest(IngestSource)` 入口 + tagged union** | 用 `isinstance` 收窄,mypy 自动推断,3 个 `BufferSource / FileSource / UrlSource` dataclass 携带各自所需参数 |
| 5 | `DocumentStructure` 字段在 reader 输出里必填 | **字段保留兼容,实际为 None** | 静态结构抽取已删除,类型仅供 `ChunkMetadata.heading_path` 字段兼容;`Heading` 在 CLI 调试输出复用 |
| 6 | 测试用 `tmp_path` 即时写 + pytest fixtures | **`tests/data/` 10 个真实文件 + 14 个 `test_adapter_*.py`** | 端到端覆盖,避免 mock 漂移,docx/pptx/xlsx 二进制格式不易用 `tmp_path` 重建 |
| 7 | 11 个 phase 拆分 | **实际按 chunker-reader / data-ingest / cli / e2e / cleanup 7 phase + 6 cleanup 任务执行** | 任务清单粒度由 11 收敛到 7,cleanup 6 项独立可追踪 |
| 8 | task 8/9/10 标 REFACTOR | **task 8/9/10 标 "已完成"** (本节追加) | 清理 phase A-F 完成后,REFACTOR 状态合并到 "已完成" |
| 9 | `ingest/exceptions.py` 单独 | **合并到顶层 `rag/exception.py`** (D7) | 与 `RAGError` 基类统一,7 个异常集中 |
| 10 | `ChunkMetadata` 8 字段 | **扩展 4 字段** (D8: source / file_type / page_count / encoding) | 来自 DocMeta 注入,审计 / 缓存 / 检索需要 |

### 6. 已知遗留 / 不在 plan 范围

- **真实 LLM E2E**: 默认 CI 跑 mock;真实 OpenAI / M3 走手测脚本,不进 plan (符合 spec §9.1 80% 覆盖率即可,真实 LLM 单测成本与价值不匹配)。
- **Alembic migration**: dev 用 `create_all`,production Alembic 推迟到 M1(plan 内已标 trade-off)。
- **BGE / Jina rerank**: Cohere 实现 + BGE / Jina stub,二期补全 (M1)。
- **`graphrag` / `multi-tenant` / `AB`**: YAGNI,不在 spec 也不在 plan。
- **RAGAS weekly CI**: 走 schedule 触发,不阻塞 on-PR。

---

## 实际交付状态 (2026-06-13 同步,R-Audit + PAudit + Pptx 测试修复完结)

> 续接上文"实际交付状态 (2026-06-12 同步)"。本节追加 2026-06-12 同步后又完成的 7 轮 review/audit + 测试修复,反映 `refactor/chunker-reader` 分支上当前最终落地状态。

### 1. 后续 7 轮 review/audit 清单 (顺序完成)

| # | 名称 | 一句话交付 |
|---|------|-----------|
| 9 | **R-Audit** (P0/P1 review) | 修 6 个 review 问题:FormatReaderResult 重复 / Chunk 三层类型 / Datasource 同名 / 死代码 / RawDoc 别名 / CLI 异常路径 |
| 10 | **R-Audit 末** (linter 误伤) | 修 R1-B 阶段写的 0 缩进文件,补回正常缩进,确保 ruff + mypy + 测试都过 |
| 11 | **PAudit-1** (chunk_repo 安全) | `chunk_repo` 改 `bindparams` + `flush` + `transaction()`,防止 SQL 注入 + 半写状态 |
| 12 | **PAudit-2** (pipeline 异步) | `pipeline._process` 改 `async`,title 字段从 `FileSource.filename` 提取,补齐 `Document.title` 时序 |
| 13 | **PAudit-3** (dispatch 重构) | `dispatch` 删除 `inspect` 反射调用,改显式 adapter 查找表;`Retry` 工具覆盖 `httpx` + `asyncio` 双栈 |
| 14 | **PAudit-4** (cache + search 配置) | `on_chunks_changed` 改 Redis pipeline;`SearchRequest` 拆 4 个 sub-config;`prompt_template` 显式接受 `None` |
| 15 | **PAudit-5** (P2/P3 清理) | `ScoredDocument` 删 `q/a` 字段;增 `RetrievalTrace` dataclass;Cache 异步化;`extra_body` 加 `reasoning` 透传;`batch` 用 `asyncio.gather`;`ErrorCode` 按域分组;`pytest` 路径全用 upper |
| 16 | **Pptx 测试修复** | 7 个 Pptx 测试改 `sync` → `async def` + `await`,消除 pytest-asyncio deprecation warning |

### 2. 当前分支最终指标 (2026-06-13)

| 维度 | 值 | 备注 |
|------|---|------|
| 单元测试 | **373 passed** | 含 Phase 8 + R-Audit + PAudit 全量 |
| 集成测试 | 19 passed (1 skip) | URL 无 fixture skip |
| 已知失败 | 1 个 pre-existing | `test_normalizer_base_raises_not_implemented` (Phase 3 拆层遗留,见"已知遗留") |
| mypy | **0 错** | strict mode 全过 |
| ruff | **全过** | 全代码段 lint 0 warning |
| ingest 段覆盖率 | 97% | reader 98% / normalizer 100% / chunker 100% / pipeline 100% |

### 3. 实际与计划的最终差异 (汇总)

| # | 计划 | 实际 | 原因 |
|---|------|------|------|
| 1 | reader 7 个 adapter | **11 个 dispatch 槽位** (txt/md/html/htm/pdf/docx/csv/json/api/pptx/xlsx) | 调研补 pptx/xlsx/api_response;htm 共享 html adapter 别名 |
| 2 | normalizer 4 段 (api/json/url + LLM) | **只保留 LLM 段落改写 + NoOp** | api/json/url 职责下沉到 reader adapter (Phase 3) |
| 3 | `src/rag/ingest/structure/` 独立子包 | **目录已删** | chunker 内部 per-chunk regex 现场算 heading_stack 更直接 (Phase 8) |
| 4 | `ingest_file / ingest_url / ingest_buffer` 三方法 | **统一 `async ingest(IngestSource)`** + tagged union (FileSource / UrlSource / BufferSource) | `isinstance` 收窄,mypy 推断自动 (Phase 5) |
| 5 | `DocumentStructure` 字段 reader 输出必填 | **字段保留兼容,实际为 None** | 类型仅供 `ChunkMetadata.heading_path` 兼容 |
| 6 | 测试用 `tmp_path` 即时写 | **`tests/data/` 11 个真实 fixture** (txt/md/html/htm/csv/json/pdf/docx/pptx/xlsx + sample_chat_export.md) | 端到端覆盖,避免 mock 漂移 |
| 7 | `IngestPipeline.ingest_file` sync | **改 async** | title 时序 + embed pool await (PAudit-2) |
| 8 | `chunk_repo` 字符串拼接 | **bindparams + flush + transaction()** | SQL 注入防护 + 事务原子性 (PAudit-1) |
| 9 | `dispatch` 用 `inspect` 反射 | **删 `inspect`,改显式查找表** | 显式胜于反射,启动期 fail-fast (PAudit-3) |
| 10 | `Retry` 仅 httpx | **覆盖 httpx + asyncio** | 异步栈需要独立 retry 上下文 (PAudit-3) |
| 11 | `on_chunks_changed` 单 Redis 调用 | **改 Redis pipeline** | 批量失效 1 次 RTT (PAudit-4) |
| 12 | `SearchRequest` 单 dict | **拆 4 sub-config** (Vector / Fulltext / Rerank / Citation) | 关注点分离 + 单测可独立 stub (PAudit-4) |
| 13 | `prompt_template` 默认空串 | **显式 `None` + 默认值** | 与 None 区分"未设置"vs"空串" (PAudit-4) |
| 14 | `ScoredDocument` 含 q/a 字段 | **删 q/a 字段** | LLM 生成段不绑死 q/a 命名 (PAudit-5) |
| 15 | 同步 `RetrievalTrace` 缺失 | **新增 dataclass** | 旁路审计结构化 (PAudit-5) |
| 16 | Pptx 测试用 sync | **改 async + await** | pytest-asyncio 推荐 (Pptx 测试修复) |

### 4. 已知遗留 (不在 plan 范围或主动跳过)

- **P2/P3 跳过项 (3 项)**:
  - **真实 LLM E2E**: 默认 CI 跑 mock;真实 OpenAI / M3 走手测脚本(成本与价值不匹配)
  - **Alembic migration**: dev 用 `create_all`,production Alembic 推迟到 M1
  - **BGE / Jina rerank**: Cohere 实现 + BGE / Jina stub,二期补全 (M1)
- **pre-existing test fail (1 项)**: `test_normalizer_base_raises_not_implemented` 在 Phase 3 拆层时变更基类签名后未跟进测试,持续 1 个红;不影响其他 373 测试。修复路径:补基类 `_check_implemented` 抽象方法或在测试中改用 `pytest.raises(NotImplementedError)` 匹配新签名。
- **YAGNI 范围外**: `graphrag` / `multi-tenant` / `AB` / RAGAS weekly CI schedule 等不在本 plan 范围。

---

*本节由文档同步 agent 在 `refactor/chunker-reader` 分支落盘,反映 2026-06-13 最终交付状态。R-Audit / PAudit / Pptx 测试修复 7 轮迭代全部落地,主流程 373 测试全过、0 mypy、0 ruff。历史 plan / 偏差表 / 6-12 同步段全部保留为溯源依据。*
