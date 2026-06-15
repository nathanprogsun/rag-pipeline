# rag-pipeline

基于 Python 3.13 的 RAG(Retrieval-Augmented Generation)流水线,覆盖**文档接入 → 切块 → 检索 → 生成 → 评估**全链路。
采用 Pydantic v2 域模型、异步 SQLAlchemy 2.0 基础设施、可选 LLM 段落重写,支持 9 种文件格式与多数据集并发检索。

---

## 一、功能概览

| 模块 | CLI 入口 | 核心能力 |
|---|---|---|
| 文档接入 | `rag-ingest` | 9 种格式解析(txt / md / html / htm / pdf / docx / pptx / csv / xlsx)+ URL 抓取 + Buffer / API 接入;`--normalize {off,auto,force}` 可选 LLM 段落改写;`--chunk-stats` 输出切块质量指标 |
| 检索 / 生成 | `rag-search` | 多 dataset 并行检索(向量 + 全文双路 → RRF 融合 → rerank → filter);token 预算 + 引用去重;LLM 生成带 `[id](CITE)` 行内引用;支持 audit NDJSON |
| 评估 | `rag-eval` | JSONL 数据集跑 EvalRunner;产出 `recall@k` / `precision@k` / `hit_rate@k` / `mrr` / `ndcg@k` 聚合指标(mean / std / min / max / median / count);可选 RAGAS 真实指标 |
| 缓存 | `infra/cache` | 4 级 cache key(embedding / query_ext / search / rerank);基于 dataset_version 的失效 pattern;Redis 不可用时降级 |
| 观测 | `retrieval/audit` | `AuditTap` 写 NDJSON 审计流;`sample_rate` 采样;关闭 / 错误均不阻塞主流程 |

### 支持的文件格式

| 格式 | 扩展名 | 适配器 | 备注 |
|---|---|---|---|
| 纯文本 | `txt` | `text_adapter` | 编码由 `DocMeta.encoding` 决定 |
| Markdown | `md`,`markdown` | `text_adapter` | 抽取 heading_stack |
| HTML | `html`,`htm` | `html_adapter` | html2md 转 markdown,再切块 |
| PDF | `pdf` | `pdf_adapter` | 按页抽取,`page_count` 入 DocMeta |
| DOCX | `docx` | `docx_adapter` | 抽取内嵌图片 |
| PPTX | `pptx` | `pptx_adapter` | — |
| XLSX | `xlsx` | `xlsx_adapter` | 默认走 markdown table 视图 |
| CSV | `csv` | `csv_adapter` | 默认走 markdown table 视图 |
| Buffer | — | `dispatch_bytes` | 入参为 `bytes + file_type`,用于把已读取的字节流送入 pipeline |

`json` 不在 reader 范围内。

---

## 二、部署与启动

### 1. 环境要求

| 组件 | 版本 |
|---|---|
| Python | 3.13 |
| 包管理 | `uv`(0.10+) |
| 数据库(可选,检索链路需要)| PostgreSQL 16 + pgvector 0.8+ |
| 缓存(可选)| Redis 7+ |
| LLM 接入 | 任意 OpenAI 兼容端点(chat / embedding / rerank 可独立配置) |

### 2. 安装

```bash
git clone <repo>
cd rag-pipeline
uv sync --extra dev
uv run pre-commit install   # 可选,装 git hook
```

### 3. 配置文件

```bash
cp .env.example .env
vim .env   # 填入 API Key 等
```

`.env` 关键字段(完整列表见 `.env.example`):

| 变量 | 用途 |
|---|---|
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | Chat LLM(章节改写、生成回答) |
| `OPENAI_EMBEDDING_*` | Embedding 服务(检索向量) |
| `OPENAI_RERANK_*` | Rerank 服务(`OPENAI_RERANK_API_KEY` 留空则跳过 rerank 阶段) |
| `DATABASE_URL` | `postgresql+asyncpg://...`,集成测试建议用专用库 |
| `REDIS_URL` | `redis://...`;含密码时用 `redis://:password@host:port/db` |
| `LANGSMITH_*` | 可选,LangSmith 观测 |

### 4. 启动基础设施

```bash
make up                     # docker compose up -d(本地 pgvector + redis)
# 或对接已有实例:直接改 .env 里的 DATABASE_URL / REDIS_URL
```

### 5. 验证安装

```bash
uv run rag-ingest tests/data/sample.txt
uv run rag-ingest --mode url https://example.com/article.html
uv run rag-search -q "Python 列表推导式" --dataset-id <UUID>
uv run rag-eval -d data/eval.jsonl
```

---

## 三、开发

### 1. 工具链

| 工具 | 用途 |
|---|---|
| `uv` | 依赖与运行(`uv run pytest` / `uv sync --extra dev`) |
| `ruff` | lint + format(`ANN` 系列规则强制类型注解) |
| `mypy` | strict 模式检查 `src/` + `tests/` |
| `pytest` + `pytest-asyncio` | 测试;`asyncio_mode=auto` |
| `pre-commit` | 提交前 ruff-check → ruff-format → mypy |

### 2. 常用命令

```bash
make lint                 # ruff check + format --check + mypy
make test                 # unit + integration(需 PG)
make test-unit            # 仅 unit,无需 Docker
make test-integration     # 真实 PG/Redis,可加 @pytest.mark.live_llm
make test-cache           # cache 子系统(单元 + 集成)

uv run pytest tests/unit/test_pipeline_full.py -v
uv run ruff check --fix . && uv run ruff format .
uv run pre-commit run --all-files
```

### 3. 代码组织约定(摘自 `AGENTS.md`,改前必读)

- **导入顺序**:stdlib → 第三方 → `rag.*`,组间空行;`src/` 由 ruff `PLC0415` 强制顶部 import
- **类型注解**:所有函数 / 方法(测试、`__init__`、fixture 同样)必须有参数 + 返回值类型
- **异常**:全局仅 `RAGError(code, message)`,`code` 取自 `ErrorCode`,无子类
- **domain 层**:`src/rag/domain/` 不得引入 SQLAlchemy
- **infra 层**:`src/rag/infra/pg/` 是唯一 SQLAlchemy 层,handler 不写裸 SQL
- **向量维度**:`1536`,与 `schema.sql` / `ChunkModel.embedding` / 测试向量一致
- **async 契约**:Normalizer 子类、reader adapter 必须 `async def`;sync 上下文跨异步用 `run_coroutine_sync`

子目录 `AGENTS.md` 进一步约束各自层级(`src/rag/domain/AGENTS.md` / `src/rag/infra/pg/AGENTS.md` / `tests/AGENTS.md`)。

### 4. 提交流程

1. 改完后 `make lint && make test`
2. 写 commit:`<type>: <description>`(`feat` / `fix` / `refactor` / `docs` / `test` / `chore` / `perf` / `ci`)
3. 推 PR 前确认 CI 全绿、解决冲突、rebase 到目标分支
4. 若引入新约定,同步更新 `AGENTS.md`(全局 / 子层)

---

## 四、项目架构

### 1. 目录结构

```
src/rag/
  config.py            # pydantic-settings(OpenAI / DB / Redis / Cache)
  exception.py         # RAGError(单一全局异常)
  error_codes.py       # ErrorCode 字面量(reader.* / chunker.* / ...)
  domain/              # Pydantic DTO(无 SQLAlchemy、无 I/O)
    document.py        # Chunk / ScoredDocument / ChunkMetadata
    search.py          # SearchRequest / SearchResult / Citation
    dataset.py         # Dataset
    enums.py           # IngestDatasource / StoredDatasource / 映射
  infra/
    pg/                # SQLAlchemy 2.0 异步基础设施
      base.py          # Base / TimestampMixin / SoftDeleteMixin
      database.py      # engine / AsyncSessionLocal / init_pool / close_pool
      schema.sql       # DDL,与 models 同步
      models/          # ChunkModel / DatasetModel(仅 FK,无 relationship)
      repositories/    # ChunkRepository
      vector_store.py  # VectorRetriever(pgvector)
      fulltext_store.py# FulltextRetriever(tsvector)
      chinese_tokenizer.py # jieba 词典 + tsquery 包装
      runnable_sync.py # run_coroutine_sync(同步上下文跨异步桥)
    cache/             # L1-L4 cache key + 失效
      keys.py
      connection.py    # Cache + init_cache / close_cache
      invalidation.py
    llm/               # chat / embed / rerank client + tokenizer
      chat.py          # get_chat_model / get_structured_chat_model
      embed.py         # get_embed_model
      rerank.py        # get_rerank_model
      semaphore.py     # 能力通道并发控制
      tokenizer.py     # MiniMax-M3 BPE token 计数
  ingest/              # 文档接入(三段流水线)
    pipeline.py        # IngestPipeline.ingest — 单一 async 入口
    source.py          # IngestSource = FileSource | UrlSource | BufferSource
    types.py           # DocMeta / TextDoc / Chunk / ChunkMetadata / IngestResult
    cli.py             # rag-ingest(typer)
    reader/
      dispatch.py      # dispatch_bytes(bytes, ext) -> TextDoc
      url.py           # read_url(httpx + 格式推断)
      extensions/      # 8 个 async adapter(txt / md / html / pdf / docx / pptx / csv / xlsx)
    normalizer/
      base.py          # Normalizer(async 基类)
      no_op.py         # NoOpNormalizer(透传)
      structure.py     # StructureNormalizer(三道闸门)
    chunker/
      settings.py      # ChunkSettings
      core.py          # Chunker.split
      rules.py / recursive.py / overlap.py / finalize.py
      code_block.py / table.py / quality.py
  search/              # 检索 / 生成(10 阶段, pipeline 的实现)
    orchestrator.py    # SearchPipeline.ainvoke(Contract 8)
    factory.py         # SearchPipelineDeps / build_search_pipeline
    cli.py             # rag-search(typer)
    extension/
      query_ext.py     # QueryExtensionRunnable
    retrieve/
      subgraph.py      # SearchSubgraph(vector + fulltext 并行)
      fusion.py        # intra_fusion(RRF)
      rerank.py        # RerankStageAdapter / NoOpRerankStage
    post/
      filter.py        # filter_by_score / filter_by_token_budget
      parent_doc.py    # NoOpParentDoc(占位,真实实现 pending session 池重构)
      cite.py          # SimpleCite(1-based 编号)
    generate/
      answer.py        # make_llm_gen / GenStage Protocol
  infra/               # 非业务基础层(I/O 适配器 + 横切工具)
    cache/             # Redis
    llm/               # LangChain 适配
    pg/                # PostgreSQL + pgvector
    observability/     # 横切: AuditRecord/AuditTap, RetrievalTrace/remove_duplicates
    text/              # 横切: parse_inline_citations / CitationChecker
  eval/                # 评估
    metrics.py         # recall@k / precision@k / hit_rate@k / mrr / ndcg@k / aggregate
    runner.py          # EvalRunner(JSONL → 并发 → 聚合)
    ragas_runner.py    # 桩指标
    ragas_real.py      # RagasRealRunner(真实 ragas>=0.3,<0.4)
    ragas_metrics.py
    cli.py             # rag-eval(typer)

tests/
  unit/                # 纯逻辑,无网络 / DB
  integration/         # 真实 PG / Redis / LLM(@pytest.mark.live_llm)
  data/                # 9 种格式共享 fixture

docs/                  # architecture.md / dev.md
project-template/      # 5k 新项目骨架
.agents/design/        # 跨任务设计文档
.github/workflows/     # CI(ruff + mypy + pytest + codecov)
```

### 2. 接入流水线

```
IngestSource → Reader(8 个 async adapter) → Normalizer(NoOp / LLM 三道闸门) → Chunker(12-rule 递归) → IngestResult
```

- 全栈 `async def`:`IngestPipeline.ingest` / `Normalizer.normalize` / `html_to_md` / `html_adapter` / `docx_adapter` 均为 async;CLI 顶层 `asyncio.run` 驱动
- `FileSource` 路径不调 `read_file` 包装(避免嵌套 `asyncio.run`),直接 `await dispatch_bytes`
- doc-level `title`:优先 `#` / `<h1>` 首项,兜底 `meta.filename`
- heading_stack / has_code / has_table / image_refs 由 chunker per-chunk regex 现场重算
- 同步回调(如 `mammoth.images.img_element`)跨异步用 `run_coroutine_sync`

### 3. 检索 / 生成流水线(Contract 8,10 阶段)

```
1. QueryExtension (可选)     LLM 查询改写
        │
2. SearchSubgraph × N         每 dataset 独立:VectorRetriever ∥ FulltextRetriever → intra_fusion
        │
3. InterVariant Fusion        多 query 变体结果 RRF
        │
4-5. Rerank (可选)            RerankStageAdapter,rerank_weight 调节
        │
6. InterDataset Fusion        (已嵌入 2-3 步,subgraph 内部 per-dataset 完成)
        │
7. Filter                     dedup(chunk_id) → score threshold → token budget
        │
8. ParentDoc Expand (可选)    NoOpParentDoc(真实实现 pending)
        │
9. Cite                       SimpleCite 1-based 编号
        │
10. Gen                       LLM 系统提示词要求输出 [id](CITE) 行内引用
```

构建入口:

```python
from rag.search.factory import SearchPipelineDeps, build_search_pipeline
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.chat import get_structured_chat_model
from rag.infra.llm.rerank import get_rerank_model

deps = SearchPipelineDeps(
    embedder=get_embed_model(),
    llm=get_structured_chat_model(),
    rerank_client=get_rerank_model(),  # None 表示跳过 rerank
    audit_tap=None,                    # 配置后开启 NDJSON
    top_k=10, rerank_weight=0.7,
    vector_weight=0.7, fulltext_weight=0.3, rrf_k=60,
)
pipeline = build_search_pipeline(deps)
```

### 4. 评估流水线

```bash
# 数据格式 (data/eval.jsonl,每行一条):
# {"query":"...","dataset_ids":["<uuid>"],"ground_truth_chunk_ids":["<uuid>"],"k":10}

uv run rag-eval -d data/eval.jsonl --output json --output-path summary.json --k 10 --concurrency 4
```

输出指标:`recall@k` / `precision@k` / `hit_rate@k` / `mrr` / `ndcg@k`,聚合 `mean` / `std` / `min` / `max` / `median` / `count`。

### 5. Docker 部署

```bash
docker compose up -d                              # pgvector + redis 后台启动
docker compose --profile cli run --rm rag rag-ingest tests/data/sample.pdf
docker compose --profile cli run --rm rag rag-search -q "test" --dataset-id <UUID>
docker compose --profile cli run --rm rag rag-eval -d /data/eval.jsonl
```

`Dockerfile` 多阶段:`uv` + Python 3.13 + libpq;`.dockerignore` 排除 tests / coverage / docs 源。

---

## 五、核心配置

### 1. `ChunkSettings`

| 字段 | 默认 | 含义 |
|---|---|---|
| `chunk_size` | 1000 | 目标 chunk 大小(字符) |
| `max_chunk_size` | 8000 | 硬上限,finalize 阶段强制 |
| `overlap_ratio` | 0.10 | 重叠比例,自动 clamp 到 `[0, 0.5]` |
| `min_chunk_size` | 256 | 短 chunk 合并阈值 |
| `paragraph_chunk_deep` | 5 | 段落递归深度 |
| `paragraph_chunk_min_size` | 200 | 段落最小尺寸 |
| `custom_separator` | None | 可选首切分隔符 |

### 2. LLM 段落重写(`StructureNormalizer`)

- **闸门 1**:`mode=forbid` 或 `chat_model` 为 `None` → 跳过
- **闸门 2**:`mode=auto` 且 markdown 标题数 `>= 2` → 跳过(已结构化)
- **闸门 3**:`asyncio.wait_for(chat_model.ainvoke(prompt), timeout=600s)`;任意异常降级回原文

CLI:`--normalize {off,auto,force}`,默认 `off`。`auto` / `force` 需 `OPENAI_API_KEY`。

### 3. 检索编排参数(`SearchPipelineDeps`)

| 字段 | 默认 | 含义 |
|---|---|---|
| `embedder` | — | LangChain `Embeddings`(必填) |
| `llm` | — | LangChain `BaseChatModel`(必填) |
| `rerank_client` | None | 留 `None` 跳过 rerank 阶段 |
| `audit_tap` | None | 配 `AuditTap` 后 `req.audit=True` 触发 NDJSON |
| `vector_weight` | 0.7 | 向量侧融合权重 |
| `fulltext_weight` | 0.3 | 全文侧融合权重 |
| `rrf_k` | 60 | RRF k 常量 |
| `rerank_weight` | 0.7 | rerank 与原 RRF 融合权重 |
| `top_k` | 10 | 每 dataset 召回 top-k |
| `token_budget` | 960000 | 最终 token 上限(默认 1M context - 40K 预留) |

### 4. 缓存(`CacheSettings`)

| 层级 | 内容 | 默认 TTL |
|---|---|---|
| L1 | embedding(text → vector) | 86400s(24h) |
| L2 | query extension(LLM 改写) | 1800s(30min) |
| L3 | search 检索结果 | 300s(5min) |
| L4 | rerank 结果 | 3600s(1h) |

失效:chunk 变更时按 `search_key_pattern_for_dataset(dataset_id)` 主动 unlink;`dataset_version_key` 记录 dataset 当前版本。

### 5. 并发控制(`LLMConcurrencySettings`)

按能力通道独立:chat / embedding / rerank(若配置 key);`max_concurrent` 由 `.env` 中 `OPENAI_*_MAX_CONCURRENT` 覆盖。

### 6. LangSmith(可选)

```env
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=rag-pipeline
```

启动时由 `sync_langsmith_env` 写入 `os.environ`,LangChain SDK 自动接管。

---
