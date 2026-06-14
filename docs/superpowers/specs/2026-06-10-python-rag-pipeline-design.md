# Python RAG Pipeline 设计 spec

> **项目路径**: `/Users/jung/pro/rag-pipeline/`
> **日期**: 2026-06-10
> **状态**: 待用户复核

## 0. 目标与范围

**目标**: 使用 LangChain (LCEL) 实现 RAG 流水线,PostgreSQL+pgvector 做向量与全文存储,Redis 做多级缓存。

**范围(查询侧全流水线)**:

1. Query Extension(MultiQuery 思路)
2. 多路召回(Vector + Fulltext,按 dataset 并行)
3. 第一层 RRF 融合(同 dataset 内 vector × fulltext)
4. Rerank(可配置,默认不启用)
5. 过滤(去重 / 阈值 / token 预算)
6. 第三层 RRF 融合(跨 dataset)
7. 引用组装 + prompt 拼装
8. 多级 Redis 缓存(embedding / query_ext / search / rerank)
9. 轻量 ingest(读文件 → chunker → embed → PG)

**SearchResult.prompt 定位(本项目是 library 不生成回答)**:
- prompt 拼装属于"检索结果的结构化呈现",与 LLM 调用解耦
- caller 拿到 `SearchResult { citations, prompt, failed_dataset_ids, warnings }` 后,**自行决定**用哪个 LLM 生成回答
- 这样 library 的边界清晰:只负责"找到相关内容 + 拼好可用的 prompt",不碰生成
- 灵活性: caller 可用 OpenAI / Claude / 本地 LLM,或加入额外 system prompt,或不用 prompt 直接用 citations
- 类比: 类似 SQL driver 返回 prepared statement + params,而非执行结果

### 0.1 流水线全景图(本项目视角)

```
+-------------------------------------------------------------------------+
|                       rag-pipeline 流水线 全景                          |
+-------------------------------------------------------------------------+

                            [ 用户 Query ]
                                |
                                v
    +-----------------------------------------------------------------+                        
    |  CLI: rag search / SearchRequest  (domain/search.py)            |
    |  入口: 归一化输入, 解析 16 个 SearchRequest 字段, 缓存键生成     |
    |  (12 默认 + image_urls / query_decomposition / parent_doc_window   |
    |   / use_global_rerank / audit 4 个可选开关)                      |
    +--------------------------------+--------------------------------+
                                    |
                [可选 ① 复杂查询拆分]
                        v
    +----------------------+
    | QueryDecomposer      |  Pydantic DecomposedQueries (sub_queries: list[str])
    | (retrieval/          |  LLM 一次 call 返回 is_complex + 拆分子查询
    |  decomposition.py)   |  简单查询直接返回 [query]
    +-----------+----------+
                                |
                +--------------------+--------------------+
                |                                         |
                v                                         v
    +-------------------+                     +----------------------+
    | text queries      |                     | image caption queries|
    | SearchRequest     |                     | SearchRequest       |
    | .query 字段       |                     | .image_urls 字段     |
    | (纯文本)          |                     | → 多模态大模型 caption → 文本 |
    +---------+---------+                     +-----------+----------+
                |                                           |
                |                                           v
                |                                +----------------------+
                |                                | ImageCaptionRunnable  |
                |                                | 调多模态大模型 (M3)   |
                |                                | 图片 → caption 文本   |
                |                                +-----------+----------+
                |                                            |
                +--------------------+-----------------------+
                                    |
                                    v
    +-----------------------------------------------------------------+
    |  QueryExtensionRunnable  (pipeline/query_ext.py)                |
    |  FastGPT 风格: LLM 改写 + submodular query selection             |
    |                                                                   |
    |  ┌─────────────────────────────────────────────────────────┐   |
    |  │  Stage 1: LLM 改写                                       │   |
    |  │   - Prompt: 抄 FastGPT system + few-shot, 多角度覆盖      │   |
    |  │   - 输入: query + chat_bg + histories (上下文感知)         │   |
    |  │   - 输出: QueryVariants (Pydantic, 10 个候选)              │   |
    |  │   - 失败兜底: 返回 [query]                                │   |
    |  └─────────────────────────────────────────────────────────┘   |
    |  ┌─────────────────────────────────────────────────────────┐   |
    |  │  Stage 2: Lazy Greedy Submodular Selection               │   |
    |  │   (retrieval/lazy_greedy.py, 复刻 FastGPT useTextCosine)  │   |
    |  │   gain(c) = α·cos(c, original) + (1 - max cos(c, sel))   │   |
    |  │   α = 0.3 (默认, 强调多样性), k = 3 (选 3 个最优)         │   |
    |  │   PriorityQueue + lazy re-eval                           │   |
    |  └─────────────────────────────────────────────────────────┘   |
    +--------------------------------+--------------------------------+
                                    |
                                    v (3 个最优变体)
    +-----------------------------------------------------------------+
    |  DatasetOrchestrator  (pipeline/orchestrator.py)               |
    |  RunnableParallel + with_fallbacks, 异常隔离                     |
    +----------------------------+------------------------------------+
                                |
                                v
    +-----------------------------------------------------------------+
    |  subgraph(dataset_n)  (pipeline/subgraph.py)                    |
    |  RunnableParallel(vec ‖ fulltext) → IntraFusion → Rerank → IntraFilter
    +--------------+----------------------------------+----------------+
                    |                                  |
                    v                                  v
    +--------------------------+       +-------------------------------+
    | VectorRetriever          |       | FulltextRetriever             |
    | (infra/pg/vector_store)  |       | (infra/pg/fulltext_store)     |
    | pgvector HNSW 检索       |       | jieba 预分词 + tsvector 检索  |
    +------------+-------------+       +---------------+---------------+
                |                                     |
                v                                     v
    +--------------------------+       +-------------------------------+
    | chunks.embedding         |       | chunks.ts_tokens              |
    | VECTOR(1536), HNSW 索引  |       | TSVECTOR, GIN 索引            |
    +------------+-------------+       +---------------+---------------+
                |                                     |
                +---------------+---------------------+
                                |
                                v
                                [可选 ③ Parent Doc 上下文扩展]
    +-----------------------------------------------------------------+
    |  ParentDocExpander  (pipeline/parent_doc.py)                     |
    |  命中 chunk 后按 parent_title + window 拉取兄弟 chunks 合并       |
    |  window=0=不扩展 (默认), 1=前后各 1, 2=前后各 2                  |
    |  合并后受 max_tokens 限制(默认 2000)                              |
    +--------------------------------+--------------------------------+
                                |
                                v
    +-----------------------------------------------------------------+
    |  fusion.py  RRF 三层融合 (本项目: 第一层 + 第三层)              |
    |  dataset.rrf_k 默认 60, 可调 (Cormack 2009)                       |
    |                                                                    |
    |  ┌──────────────────────────────────────────────────────────┐    |
    |  │ 第一层: 同 dataset 内融合 (RRF)                           │    |
    |  │   score(c) = w_v·1/(rrf_k+rank_v) + w_f·1/(rrf_k+rank_f) │    |
    |  │   w_v=0.7 (vector_weight), w_f=0.3 (fulltext_weight)       │    |
    |  ├──────────────────────────────────────────────────────────┤    |
    |  │ 第二层: 图片内融合  (本期不实现, 单图 = caption × 1)        │    |
    |  ├──────────────────────────────────────────────────────────┤    |
    |  │ 第三层: 跨 dataset 融合                                     │    |
    |  │   score(c) = Σ_dataset  1/(rrf_k+rank)  (dataset 间等权)  │    |
    |  └──────────────────────────────────────────────────────────┘    |
    +-----------------------------------------------------------------+
                                |
                                v
    +-----------------------------------------------------------------+
    |  rerank.py  (可选, 由 dataset.rerank_model 决定)                  |
    |   - 调用 langchain-cohere CohereRerank (替代自实现)               |
    |   - rerank_weight 0.7 混合原 score                               |
    |   - 失败时跳过, 不阻塞流水线                                     |
    +-----------------------------------------------------------------+
                                |
                                v
    +-----------------------------------------------------------------+
    |  filter.py  过滤管线                                             |
    |   1) remove_duplicates  text md5 hash 去重                       |
    |   2) filter_by_score     score_threshold                         |
    |   3) filter_by_token_budget  max_tokens, min_keep=1              |
    +-----------------------------------------------------------------+
                                |
                                v
    +-----------------------------------------------------------------+
    |  cite.py  引用组装                                                |
    |   SearchResult = { citations, prompt, failed_dataset_ids,        |
    |                     warnings }                                  |
    +-----------------------------------------------------------------+
                                |
                                v  (audit=True 时旁路)
    +-------------------+
    | RetrievalAudit    |  写 audit_log.jsonl, 旁路, 不阻塞
    | (retrieval/       |  包含 query / variants / per-dataset
    |  audit.py)        |  vec+ft+fused+rerank+final trace
    +--------+----------+
                                |
                                v
                            [ SearchResult ]

+-------------------------------------------------------------------------+
|  流水线外的工具 (由 caller 显式调用)                                       |
+-------------------------------------------------------------------------+

    CitationChecker.check(llm_response, citations)
        → recall / precision / hallucinated / unused

    rag audit --last=20  (查看历史 trace)

+-------------------------------------------------------------------------+
|  ingest 侧 (文件上传时, 一次性同步执行)                                  |
+-------------------------------------------------------------------------+

    [文件 .md/.txt/.pdf/.docx/.html/.json + 图片]
            |
            v
    [reader.py]  按后缀分发解析器, 图片走多模态大模型 caption
            |
            v
    [chunker.py]  17 级分隔符递归切 (customReg→md headers→code→tables→soft seps→char)
                    soft_seps 提到 ChunkSettings 可裁剪 (纯英文语料可去掉中文标点)
                    标题继承, 代码块保护, overlap 0.15
            |
            v
    [embed]  OpenAI text-embedding-3-small, 同步调用
            | 注: dataset.embed_model 选定后不可变 (本期不支持换 model 后就地迁移)
            v
    [jieba]  预分词写入 chunks.ts_tokens
            |
            v
    [(INSERT) chunks]  UUID PK, modality='text'|'image_caption'

+-------------------------------------------------------------------------+
|  缓存 (Redis, 4 层 + 失效策略)                                          |
+-------------------------------------------------------------------------+

    L1: rag:emb:{model}:{provider_version}:{hash}    TTL 24h
    L2: rag:qext:{model}:{provider_version}:{hash}   TTL 30min  [default off]
    L3: rag:search:{dataset_version}:{hash}          TTL 5min
    L4: rag:rk:{model}:{hash}                        TTL 1h

    失效:
    - chunk 增删  → 清 L3 + L4 (该 dataset)
    - dataset 删  → 清 L3 + L4
    - 切 embed_model → 清 L1 + L2 + 该 dataset 的 L3
    - Redis 不可用 → 降级直连 + warnings 标记, 不报错
  

```

**对照 FastGPT 原版(参照用户提供的全景图)**:

| FastGPT 节点 | 本项目对应 | 实现位置 |
|--------------|-----------|----------|
| `dispatch/dataset/search.ts` | SearchRequest + CLI | `domain/search.py` + `cli/main.py` |
| `imageCaption.ts` (ingest 侧) | (做) 多模态大模型(M3)caption → chunks.image_caption | `ingest/pipeline.py` |
| `imageCaption.ts` (搜索侧 caption 路径) | (做) ImageCaptionRunnable | `pipeline/image_caption.py` |
| `imageQueries` 搜索时多模态路径 | (不做) 不引入多模态 embed 模型 | — |
| `Query Extension` | QueryExtensionRunnable | `pipeline/query_ext.py` |
| `defaultRecall/index.ts` | DatasetOrchestrator | `pipeline/orchestrator.py` |
| `multiQueryRecall.ts` | subgraph Runnable | `pipeline/subgraph.py` |
| `embeddingRecall.ts` | VectorRetriever(text only) | `infra/pg/vector_store.py` |
| `fullTextRecall.ts` | FulltextRetriever | `infra/pg/fulltext_store.py` |
| `result.ts` RRF | fusion.py(intra + inter) | `pipeline/fusion.py` |
| `rerank.ts` | RerankRunnable | `pipeline/rerank.py` |
| `defaultRecall/utils.ts` 过滤 | filter.py | `pipeline/filter.py` |
| `replaceS3KeyToPreviewUrl` | 引用组装时填 `image_path` | `pipeline/cite.py` |
| `toolResponses` | SearchResult | `domain/search.py` |
| `MongoDB datas` | (简化为 chunks 单表) | `infra/pg/schema.sql` |
| `MongoDB trainings` 队列 | (同步 ingest,无队列) | `ingest/pipeline.py` |
| `Worker: embedding API` | (同步调用 OpenAI Embeddings) | `infra/llm/embed.py` |

---

## 1. 项目结构

```
/Users/jung/pro/rag-pipeline/
├── pyproject.toml
├── README.md
├── Makefile
├── docker-compose.yml          # pgvector/pgvector:pg16 + redis:7
├── .env.example
│
├── src/rag/
│   ├── __init__.py
│   ├── config.py               # pydantic-settings
│   ├── exceptions.py
│   │
│   ├── domain/                 # 纯数据模型,无 I/O
│   │   ├── dataset.py
│   │   ├── document.py
│   │   └── search.py
│   │
│   ├── infra/
│   │   ├── pg/
│   │   │   ├── connection.py
│   │   │   ├── schema.sql
│   │   │   ├── vector_store.py     # VectorRetriever(Runnable)
│   │   │   └── fulltext_store.py   # FulltextRetriever(Runnable)
│   │   ├── cache/
│   │   │   ├── connection.py
│   │   │   ├── keys.py
│   │   │   ├── embedding_cache.py
│   │   │   ├── query_ext_cache.py
│   │   │   ├── search_cache.py
│   │   │   ├── rerank_cache.py
│   │   │   └── invalidation.py
│   │   └── llm/
│   │       ├── chat.py             # ChatOpenAI(base_url)
│   │       ├── embed.py            # OpenAIEmbeddings
│   │       ├── vlm.py              # 多模态大模型 (MiniMax-M3) caption 客户端
│   │       └── rerank.py           # Cohere / BGE / Jina
│   │
│   ├── pipeline/               # LCEL
│   │   ├── subgraph.py
│   │   ├── orchestrator.py
│   │   ├── fusion.py
│   │   ├── rerank.py
│   │   ├── filter.py
│   │   ├── query_ext.py
│   │   ├── image_caption.py     # 多模态大模型 caption(image_urls → text)
│   │   ├── parent_doc.py        # Parent Document Retriever 上下文扩展
│   │   ├── cache_decorator.py
│   │   ├── full.py               # build_full_pipeline()
│   │   └── cite.py
│   │
│   ├── ingest/
│   │   ├── reader.py           # .md / .txt / .pdf / .docx / .html / .json + 图片
│   │   ├── structure.py        # 提取 heading tree / 列表嵌套 (新)
│   │   ├── chunker.py          # 12 级分隔符递归切
│   │   └── pipeline.py
│   │
│   ├── retrieval/              # 检索增强 (新)
│   │   ├── decomposition.py    # Query Decomposition (新)
│   │   ├── audit.py            # 检索审计 trace (新)
│   │   └── citation_check.py   # 引用校验工具 (新)
│   │
│   └── cli/
│       └── main.py             # typer: search / ingest / eval / audit
│
├── tests/
│   ├── unit/
│   │   ├── test_chunker.py
│   │   ├── test_structure.py
│   │   ├── test_fusion.py
│   │   ├── test_filter.py
│   │   ├── test_query_ext.py
│   │   ├── test_query_decomposition.py
│   │   ├── test_parent_doc.py
│   │   ├── test_orchestrator.py
│   │   ├── test_cache_keys.py
│   │   ├── test_cite.py
│   │   └── test_citation_check.py
│   ├── integration/            # testcontainers
│   │   ├── test_pg_retrieval.py
│   │   ├── test_cache.py
│   │   ├── test_ingest_atomic.py
│   │   └── test_ingest.py
│   ├── e2e/
│   │   └── test_full_pipeline.py
│   ├── eval/
│   │   ├── goldset.jsonl       # 50-100 条
│   │   ├── synthetic.py        # 自动生成 synthetic query
│   │   ├── retrieval_metrics.py # Recall@K / MRR / NDCG 计算
│   │   ├── regression.py       # 回归测试 20-30 条边界 query
│   │   └── run_ragas.py
│   └── conftest.py
│
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-06-10-python-rag-pipeline-design.md   # 本文件
```

**约定**:
- `src/` 布局;`domain/` 无 I/O;`infra/` 内部子包不交叉依赖;`pipeline/` 只依赖 `domain/` + `infra/` 抽象。
- LLM/Embedding provider:OpenAI 兼容协议(base_url 可换)。
- CLI:typer,三个子命令 `search` / `ingest` / `eval`。

---

## 2. 默认值(对齐 FastGPT)

| 字段 | 默认 | 来源 |
|------|------|------|
| `chunk_size` | 1000 | `chunkAutoChunkSize` |
| `max_chunk_size` | 8000 | `defaultMaxChunkSize` |
| `overlap_ratio` | 0.15 | textSplitter 默认 |
| `paragraph_chunk_deep` | 5 | textSplitter 默认 |
| `paragraph_chunk_min_size` | 100 | textSplitter 默认 |
| `min_chunk_size` | 64 | `minChunkSize` |
| `embedding_dim` | 1536 | OpenAI text-embedding-3-small |
| `RRF_K` | 60 | 经典值 |
| `vector_weight` | 0.7 | FastGPT RRF 文本侧 |
| `fulltext_weight` | 0.3 | FastGPT RRF 文本侧 |
| `top_k` | 10 | — |
| `score_threshold` | 0.0 | — |
| Cache TTL: L1 emb | 24h | — |
| Cache TTL: L2 qext | 30min | — |
| Cache TTL: L3 search | 5min | — |
| Cache TTL: L4 rerank | 1h | — |

---

## 3. 数据模型(domain/— 全部用 uuid.UUID 主键)

```python
# domain/dataset.py

class Dataset(BaseModel):
    """知识库配置:一个 Dataset 等价于一个独立的 RAG 知识库。

    持有嵌入模型、chunk 切分粒度、RRF 权重、prompt 模板等元数据;
    chunks 写入时按本配置生成 embedding / 切分;召回时按本配置路由。
    """
    id: uuid.UUID
    name: str
    embed_model: str                 # "text-embedding-3-small"
    embed_dim: int                   # 1536
    chunk_size: int = 1000
    rerank_model: str | None = None
    rrf_k: int = 60                  # M5: per-dataset RRF 参数, 默认 60 (Cormack 2009)
    query_select_alpha: float = 0.3  # M6: Stage 2 submodular α
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE
    system_prompt: str | None = None # M7: dataset 级 system prompt
    created_at: datetime

# 默认 prompt 模板,放 config.py
DEFAULT_PROMPT_TEMPLATE = """基于以下参考资料回答用户问题。

## 参考资料
{citations}

## 用户问题
{query}

## 回答"""


# domain/document.py

class ChunkMetadata(BaseModel):
    """Chunk 的元数据载荷:用于引用展示、排序、按文件去重。

    不存正文与向量,只存定位与溯源信息。
    created_at 在 ScoredDocument → Citation 转换时从 PG 回填(L3 修正)。
    """
    dataset_id: uuid.UUID
    datasource: Literal["file", "manual", "api"]
    filename: str | None = None
    parent_title: str = ""
    chunk_index: int = 0
    custom_separator: str | None = None
    created_at: datetime | None = None   # L3: 从 chunks.created_at 读出,ScoredDoc 转换时填


class Chunk(BaseModel):
    """入库前的原始 Chunk:从 reader + chunker 出来的内容块,待嵌入与写入 PG。

    写库后其 id 是 chunks.id 主键;text 是正文或 image caption;
    embedding 由 ingest 阶段一次性生成,查询返回时不一定带。
    """
    id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None
    metadata: ChunkMetadata
    embedding: list[float] | None = None


class ScoredDocument(BaseModel):
    """召回结果:从 vector / fulltext / rerank 任意召回器出来的带分块。

    score + rank 同时存在:RRF 公式需要 rank 还原(融合时 score 变了 rank 不变)。
    source 标记召回路径便于 debug;embedding 可选(融合需要时填充)。
    """
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    score: float
    rank: int
    source: Literal["vector", "fulltext", "caption", "rerank"]
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None      # M8: modality=image_caption 时有值
    metadata: ChunkMetadata
    embedding: list[float] | None = None


# domain/search.py

class SearchRequest(BaseModel):
    """用户发起的搜索请求:query 文本 + 路由信息 + 调优参数。

    必填项只 3 个:query / dataset_ids / top_k;
    其他均有默认值,dataset 级的默认配置由 dataset.* 字段承担。
    image_urls 入参时由 ImageCaptionRunnable 转 caption 后并入 query 流。
    """
    query: str
    image_urls: list[str] = []                  # 可选图片入参,多模态大模型 caption 后并入 text 路径
    dataset_ids: list[uuid.UUID]
    top_k: int = 10
    score_threshold: float | None = None
    use_rerank: bool = True                     # L1 修正: rerank_model=None 时的 fallback 见下
    rerank_model: str | None = None
    rerank_weight: float = 0.5  # P0-1 修复 (audit #5): 默认 0.7 → 0.5,对齐 FastGPT defaultReRankWeight;Stage 2 RRF 混合权重, 向量侧与 rerank 侧各占 0.5 (与 Task 2 对齐)
    query_extension: bool = True
    max_query_variants: int = 3
    max_tokens: int = 4000
    embedding_model: str | None = None
    temperature: float = 0.0
    # ── task15/16 扩展 (H3 修正): 与 Task 2 SearchRequest 字段对齐 ──
    query_decomposition: bool = False   # 启用 QueryDecomposer(复杂查询拆分)
    parent_doc_window: int = 0          # parent_doc 上下文窗口, 0=不启用
    use_global_rerank: bool = False     # 全局 rerank vs per-dataset rerank
    audit: bool = False                 # 启用 RetrievalAudit(写入 AuditRecord)
    chat_bg: str = ""                   # C5: 多轮对话背景(指代消解用)
    histories: list[dict] = []          # C5: 对话历史 [{"role":"user","content":"..."}]


# L1 rerank 解析逻辑(rank 阶段用)
def resolve_rerank_model(req: SearchRequest, dataset: Dataset) -> str | None:
    """解析 rerank 模型优先级: req.rerank_model > dataset.rerank_model > None。
    
    返回 None 表示不启用 rerank;req.use_rerank=False 也强制 None。
    """
    if not req.use_rerank:
        return None
    return req.rerank_model or dataset.rerank_model


class Citation(BaseModel):
    """最终返回给前端/调用方的引用条目 DTO。

    字段全为必填(可空字段都用 ? 标记):source_name 给人类看,
    image_path 仅 modality=image_caption 时有值,score 用于前端排序展示。
    """
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    source_name: str
    content: str
    image_path: str | None = None
    score: float
    update_time: datetime | None = None


class SearchResult(BaseModel):
    """Search 接口的完整响应:citations 列表 + 已拼好的 prompt + 失败信号(H4 修正)。

    prompt 已包含"参考资料"段落,LLM 可直接用其生成回答;
    citations 供前端展示卡片;
    failed_dataset_ids 告知调用方"哪些 dataset 检索失败"(可与"无相关结果"区分);
    warnings 携带非阻塞告警(如 rerank 跳过、token 超限截断等)。
    """
    citations: list[Citation]
    prompt: str
    failed_dataset_ids: list[uuid.UUID] = []   # H4: 部分 dataset 失败时填
    warnings: list[str] = []                   # H4: 非阻塞告警列表
```

---

## 4. PostgreSQL Schema(单库 2 表)

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE datasets (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  embed_model     TEXT NOT NULL,
  embed_dim       INT  NOT NULL,
  chunk_size      INT  NOT NULL DEFAULT 1000,
  rerank_model    TEXT,
  rrf_k           INT  NOT NULL DEFAULT 60,   -- M5: per-dataset RRF parameter
  query_select_alpha REAL NOT NULL DEFAULT 0.3, -- M6: submodular α
  vector_weight   REAL NOT NULL DEFAULT 0.7,
  fulltext_weight REAL NOT NULL DEFAULT 0.3,
  prompt_template TEXT NOT NULL DEFAULT '',
  system_prompt   TEXT,                        -- M7: dataset 级 system prompt
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE chunks (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id    UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  text          TEXT NOT NULL,                          -- 文本 or image caption
  modality      TEXT NOT NULL DEFAULT 'text',
  image_path    TEXT,
  parent_title  TEXT NOT NULL DEFAULT '',
  chunk_index   INT  NOT NULL DEFAULT 0,
  filename      TEXT,
  embedding     VECTOR(1536) NOT NULL,             -- 起步定 1536;换 model 见下
  ts_tokens     TSVECTOR,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  
  CONSTRAINT modality_chk CHECK (modality IN ('text', 'image_caption')),
  CONSTRAINT image_path_required CHECK (
    (modality = 'image_caption' AND image_path IS NOT NULL) OR
    (modality = 'text')
  )
);

CREATE INDEX chunks_embedding_hnsw
  ON chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_ts_tokens_gin  ON chunks USING GIN (ts_tokens);
CREATE INDEX chunks_dataset_id_idx ON chunks (dataset_id);
CREATE INDEX chunks_modality_idx   ON chunks (modality);
```

**关系**:
- `datasets (1) → (N) chunks`,`ON DELETE CASCADE`
- 同 dataset 内 chunks 可混合 `text` 和 `image_caption`
- 检索统一走 `text embedding + tsvector`,RRF 不分模态

**审核账**(已剔除冗余):
- ❌ 不引入 `datas` / `data_texts` / `trainings` / `image_chunks` / `query_log` 表,理由见 brainstorming §2.2 字段级审核
- 🟡 8 字段 `datasets` + 10 字段 `chunks`,已是最小可用集

**embedding 维度变更策略(C3 修正)**:

> **本期不支持换 embed model 后就地迁移。**

- 理由: `ALTER TABLE chunks ALTER COLUMN embedding TYPE VECTOR(<new_dim>)` 是阻塞性 DDL,
  需整表重写 + 重建 HNSW 索引;10 万 chunk 量级可能锁表数分钟,无在线迁移方案风险大。
- 业务语义: dataset.embed_model 选定后**不可变**;换 model 必须:
  1. 新建 dataset(不同 UUID)
  2. 重新 ingest 全部数据
  3. 旧 dataset 用 `UPDATE datasets SET archived = true` 软删除(本期不实现 archived 字段,可用 `DELETE datasets WHERE id = ...`,依赖 `ON DELETE CASCADE` 清 chunks)
- 二期可加: 新建列 + 后台 batch 回填 + 原子切列(本期不做)

---

## 5. 全文检索(中文)方案

- **应用层 jieba 预分词** → 空格 join → 写入 `ts_tokens` 字段
- `to_tsvector('simple', tokens)` 构造(避免 PG 内置中文词典失效)
- 查询时同样用 jieba 切词 → `plainto_tsquery('simple', ...)`
- 与 chunker 共用 jieba 实例,保证一致
- **L2 修正**:`simple` 配置**有意设计**,不做 stemming/stop word 处理;
  - 中文为主场景下,`simple` 等价于"按空格分词的精确匹配",召回精度可接受
  - 已知限制: 英文混合内容召回率偏低(stemming 缺失);`is`/`the` 等 stopword 未过滤
  - 权衡: 不引入 zhparser 扩展(避免额外 PG 插件),不强求英文 stem

---

## 6. 图片路径(本期:走多模态大模型 Caption → 文本)

- 文件格式支持:`.png`/`.jpg`/`.jpeg`/`.webp`
- 处理:Reader 读 bytes → 调多模态大模型(**MiniMax-M3** 多模态模式,OpenAI 兼容协议)→ caption 字符串 → 走 `chunker.py` 二次切分(`chunk_size` 用 `min(dataset.chunk_size, len(caption))`,caption 短时直接当 1 个 chunk)→ `text embed` + `modality=image_caption` + `image_path=S3/本地路径` 入库
- 原图存 S3/本地,DB 只存路径字符串
- **不**用 CLIP/SigLIP 多模态向量(本期走生成式 caption 路径,与 FastGPT 当前的 imageCaption 路径一致)
- **M5 修正**: 长 caption(>2000 token)的切分策略:
  - chunker 的 12 级分隔符递归对纯 caption 文本退化为"按中英句号/问号/感叹号切分 + 末块合并"
  - markdown 标题继承逻辑对 caption 无效(无 markdown 结构)
  - 极端长 caption(单图描述 > dataset.max_chunk_size)整条作为一个 chunk,不强行切
  - 实现上:chunker 检测 input 是否有 markdown 标题,无则跳过对应 step

---

## 6.5 文档解析增强(数据工程)

### 6.5.1 复杂 PDF 处理(本期: 简化)

- 默认 reader: `pypdf`(能处理大多数文字 PDF)
- 已知限制(本期不解决): 双栏 PDF 文本顺序错乱、扫描件需 OCR、加密 PDF
- 升级路径: 替换为 `pymupdf4llm`(LLM 友好的 PDF→Markdown)或 `marker-pdf`(深度学习 PDF 解析)
- 决策: 本期 pypdf 起步,复杂 PDF 在生产遇到再换

### 6.5.2 表格 chunk 策略

chunker 的 step 8(markdown table)是关键:

```python
# chunker.py
def markdown_table_split(text, chunk_size, max_size):
    """按行切表格,header 重复到每个 chunk。
    表格视为一个语义单元,不按句号/逗号切。
    """
    ...
```

- markdown 表格(以 `|` 开头、有 header + separator 行): 整表保留,过大按行切,header 重复
- HTML 表格: 整表保留
- 复杂长表(> chunkSize * 1.2): 按行分块,header 重复注入
- 表格里的数据保持完整,不被标点切碎

### 6.5.3 文档结构提取(`ingest/structure.py` 新)

```python
class DocumentStructure(BaseModel):
    """文档结构信息,在 chunking 前提取,作为 chunk metadata 的一部分。"""
    heading_tree: list[Heading]            # 标题树(含层级 1-5)
    list_nesting_depth: int               # 最大列表嵌套深度
    has_code_blocks: bool
    has_tables: bool
    page_count: int | None                # PDF 专用

class Heading(BaseModel):
    level: int                            # 1-5
    text: str
    line: int
    children: list["Heading"] = []
```

- `ingest/structure.py` 单独模块,从原文抽结构
- 结构信息作为 ChunkMetadata 字段,便于按章节过滤/检索
- heading_tree 序列化为 JSON 存 metadata(便于 chunk 反向关联父标题链)

### 6.5.4 增量更新与原子性(M4 扩展)

```python
# ingest/pipeline.py
async def ingest_atomic(dataset_id: UUID, filename: str, new_chunks: list[Chunk]):
    """整批原子 ingest,失败回滚,不留脏数据。"""
    try:
        async with conn.transaction():
            # 1) 删旧 chunks
            await conn.execute(
                "DELETE FROM chunks WHERE dataset_id = $1 AND filename = $2",
                dataset_id, filename,
            )
            # 2) 删 cache L3 / L4
            await cache.invalidate(f"rag:search:*{dataset_id}*")
            await cache.invalidate(f"rag:rk:*{dataset_id}*")
            # 3) 写新 chunks
            await _bulk_insert_chunks(new_chunks)
            # 4) commit (transaction exit)
    except Exception:
        # PG 自动 rollback
        # 但 cache invalidation 已经执行!会有短暂脏读
        # 缓解: cache invalidation 放到 transaction 提交后
        raise
```

**变更检测**:
- filename 级别: 简单实现,同文件重 ingest 触发整批替换
- 内容级别(本期不实现): 加 ETag / md5 检测,只对变更的 chunks 做 diff

**原子性边界**:
- ✅ PG transaction: `DELETE` + `INSERT` 在同一事务,失败回滚
- ⚠️ Cache invalidation: 不在 PG 事务内,失败可能留脏数据(L3 5min TTL 自愈)
- 文档化: 已知不一致窗口 < 5min

### 6.5.5 Embedding A/B 对比方案(本期 stub)

```python
# tests/eval/ab_embedding.py
async def ab_embedding_compare(
    old_model: str, new_model: str,
    goldset: list[EvalQuery],
):
    """同一 gold set 上跑两个 embedding 模型,对比 L2 指标。"""
    # 1) 临时 ingest 到两个 dataset (old_model / new_model)
    # 2) 跑同一组 query
    # 3) 对比 Recall@K / MRR / Hit Rate
    # 4) 报告: 退化 / 持平 / 提升
```

- 实施成本: 中(需双倍 embed + 临时 dataset)
- 价值: 切换 embed model 前可量化对比
- 本期: 实现基础框架,实际跑 A/B 由 owner 触发

---

## 7. LCEL 流水线 + RRF 三层融合

### 7.0 检索增强(在主流程外可选挂载)

#### 7.0.1 Query Decomposition(`retrieval/decomposition.py`)

**场景**: 复杂查询"X 和 Y 的区别是什么?" → 拆为子查询"X 是什么?" + "Y 是什么?",分别检索再合并

```python
class QueryDecomposer:
    """用 LLM 把复杂 query 拆为多个子查询,每个子查询独立检索。"""
    
    def __init__(self, llm: ChatModel):
        self.llm = llm
    
    async def decompose(self, query: str) -> list[str]:
        if self._is_simple(query):
            return [query]   # 简单查询不拆,直接走
        return await self._llm_decompose(query)
    
    def _is_simple(self, q: str) -> bool:
        # 启发式: < 20 字 / 无 "和"/"区别"/"对比" 等词,视为简单
        return len(q) < 20 or not any(kw in q for kw in ["和", "区别", "对比", "vs", "difference"])
    
    async def _llm_decompose(self, query: str) -> list[str]:
        prompt = f"将以下复杂查询拆为多个子查询(每行一个):\n\n{query}\n\n子查询:"
        result = await self.llm.ainvoke(prompt)
        return [line.strip() for line in result.content.split("\n") if line.strip()]
```

- 子查询各自走一遍 subgraph,结果合并去重
- 简单查询(启发式判断)不拆,避免无谓 LLM 调用
- LLM 拆解失败时回退到原 query

#### 7.0.2 Parent Document Retriever(`pipeline/parent_doc.py`)

**场景**: 检索到小 chunk(如 200 token),但 LLM 需要更多上下文 → 自动扩展到父 chunk(1000 token)

```python
class ParentDocExpander:
    """对命中的 chunk,按 parent_title + 邻近 index 扩展上下文。"""
    
    def expand(self, hits: list[ScoredDocument], window: int = 1) -> list[ScoredDocument]:
        """每个 chunk 拉取前后 window 个兄弟 chunk,合并到 text。"""
        expanded = []
        for hit in hits:
            siblings = self._fetch_siblings(hit, window=window)
            merged_text = "\n\n...\n\n".join([s.text for s in [hit, *siblings]])
            expanded.append(hit.model_copy(update={"text": merged_text}))
        return expanded
```

- 利用 ChunkMetadata.parent_title 找同 section 的兄弟 chunks
- 一次 SQL 查询 batch 拉取所有所需 chunks
- 合并后 text 长度受 max_tokens 限制(走 filter_by_token_budget)

**配置**:
- `parent_doc_window`: 0=不扩展(默认),1=前后各 1,2=前后各 2
- `parent_doc_max_tokens`: 合并后单 chunk 的 token 上限(默认 2000)

#### 7.0.3 检索审计(`retrieval/audit.py`)

```python
class RetrievalAudit:
    """记录每次检索的完整 trace,便于回溯为什么命中/没命中。"""
    
    async def trace(self, query: str, result: SearchResult) -> AuditRecord:
        return AuditRecord(
            timestamp=datetime.utcnow(),
            query=query,
            query_variants=[...],            # QueryExtension 产出
            dataset_ids=[...],
            per_dataset={
                ds_id: DatasetTrace(
                    vector_hits=[(chunk_id, score, rank), ...],
                    fulltext_hits=[...],
                    fused_top_n=[...],
                    rerank_input=[...],
                    rerank_output=[...],
                    final_hits=[...],
                )
                for ds_id in ...
            },
            global_ranking=[...],
            final_citations=[...],
            cache_hits={layer: bool},
            latency_ms={stage: float},
        )
```

- **存储**: 写 `audit_log.jsonl` 文件(本期不写 PG,避免引入新表)
- **CLI**: `rag audit --last=20` 打印最近 20 次 trace
- **用途**: debug "为什么这个 query 没命中",可逐 stage 看
- **生产化**: 二期可写入 PG `query_log` 表,本期不实现

#### 7.0.4 ColBERT 粗排(本期 stub,二期可加)

- 评估层面提一句: 标准 RRF + rerank 已是工程实践常用组合,ColBERT token-level 交互可作为重排前粗排
- 实施成本: 高,需引入 ColBERT 推理服务
- 决策: 本期不实现,文档化作为评估期可考虑的方向

### 7.1 架构

```
SearchRequest { query, image_urls, dataset_ids, ... }
   ↓
[可选] QueryDecomposer  ← 复杂查询拆子查询(挂载点 ①)
   ↓
ImageCaptionRunnable       ← image_urls → 多模态大模型 → caption 列表
   ↓
QueryExtensionRunnable     ← 合并 (text query ∪ caption) → N 个变体
   ↓
DatasetOrchestrator (RunnableParallel + with_fallbacks)
   ├─ subgraph(ds_1) = VecRT ‖ FTRT → IntraFusion(L1) → Rerank → IntraFilter
   ├─ subgraph(ds_2) = ...
   └─ subgraph(ds_n) = ...
   ↓
[可选] ParentDocExpander   ← 命中 chunk 上下文扩展(挂载点 ③)
   ↓
InterDatasetFusion (L3)
   ↓
[可选] GlobalRerank       ← 跨 dataset 二次重排(挂载点 ②)
   ↓
GlobalFilter
   ↓
CiteAssembler
   ↓
[审计] RetrievalAudit     ← 写 trace(旁路,不阻塞主流程)
   ↓
SearchResult { citations, prompt, failed_dataset_ids, warnings }

# 流水线之外的工具(由 caller 显式调用):
#   - CitationChecker.check(llm_response, citations)  → 引用校验
#   - rag audit --last=20                            → 查看历史 trace
```

> **C1 修正**: 不使用裸 `asyncio.gather` 调度 subgraph;
> 改用 LangChain `RunnableParallel` (LCEL 原生并发原语) + 每个 subgraph `with_fallbacks()` 做异常隔离。
> 理由: 裸 asyncio.gather 会绕过 LangChain callback / tracing 链路,`return_exceptions=True` 异常语义与 `with_fallbacks` 不一致。

**可选模块挂载点表(新增模块定位)**:

| 模块 | 挂载点 | 触发条件 | 必装? |
|------|--------|----------|-------|
| `QueryDecomposer` | ① QueryExtension 之前 | `SearchRequest.query_decomposition=True` (默认 False) | 否 |
| `ImageCaptionRunnable` | QueryExtension 之前(内置) | `SearchRequest.image_urls` 非空 | 条件必装 |
| `ParentDocExpander` | ③ Fusion 之后 / Rerank 之前 | `SearchRequest.parent_doc_window > 0` (默认 0) | 否 |
| `GlobalRerank` | ② Filter 之前 | `SearchRequest.use_global_rerank=True` (默认 False) | 否 |
| `RetrievalAudit` | Cite 之后旁路 | `SearchRequest.audit=True` (默认 False) | 否 |
| `CitationChecker` | 流水线外 | caller 在 LLM 生成回答后显式调用 | 工具,非节点 |

### 7.2 第一层 RRF(同 dataset 内)

```python
RRF_K = 60

def intra_fusion(vector_hits, fulltext_hits, w_vector, w_fulltext):
    """第一层融合: **加权 RRF (Weighted RRF, WRRF)**。

    公式: score(c) = Σ_s  w_s × 1 / (RRF_K + rank_s(c))

    注意: 本项目使用**加权**变体(WRRF)而非**等权**标准 RRF,
    对齐 FastGPT `concatWeightedRecallLists` 实现。
    与标准 RRF 的差异: 不同召回源有显式权重,允许按业务调优;
    标准 RRF 等权求和,无法突出向量召回。
    RAGAS 评估时需注明此差异,直接与 FastGPT 复现结果对比。
    """
    by_chunk: dict[UUID, ScoredDocument] = {}
    for rank, d in enumerate(vector_hits):
        by_chunk.setdefault(d.chunk_id, d.model_copy(update={
            "score": w_vector * (1.0 / (RRF_K + rank)),
            "rank": rank, "source": "vector",
        }))
    for rank, d in enumerate(fulltext_hits):
        score = w_fulltext * (1.0 / (RRF_K + rank))
        if d.chunk_id in by_chunk:
            by_chunk[d.chunk_id].score += score
        else:
            by_chunk[d.chunk_id] = d.model_copy(update={
                "score": score, "rank": rank, "source": "fulltext",
            })
    return sorted(by_chunk.values(), key=lambda x: x.score, reverse=True)
```

### 7.3 第三层 RRF(跨 dataset)

- 输入:各 subgraph 输出 `filtered` 的并集
- 输出:同样按 `1/(60+rank)` 累加,排序
- 公式与 7.2 相同(无 w 区分,dataset 间等权)

### 7.4 异常隔离

| 阶段 | 异常 | 处理 |
|------|------|------|
| QueryExtension | LLM 失败 | 回退单 query 继续 |
| subgraph | 任一失败 | `with_fallbacks` 返回 `{filtered: [], error: ...}`,记录到 `failed_dataset_ids` |
| Rerank | API 失败 | 跳过 rerank,直接进 filter,加 warning |
| 全部 dataset 失败 | — | 返回 `SearchResult { citations: [], failed_dataset_ids: [全部], warnings: [...] }`,不抛异常 |

### 7.5 过滤管线

```python
def filter_pipeline(hits, score_threshold, max_tokens, tokenizer=None):
    """过滤管线:去重 → 阈值 → token 预算(H3 修正截断语义)。

    Token 预算策略: 当 hits 总 token 数 > max_tokens 时,按 score 降序截断,
    对**超长单 chunk**(单条 > max_tokens)的处理:
      - 若 tokenizer 不为空: 截断该 chunk 的 text 到 max_tokens 等价字符
      - 若 tokenizer 为空: 整条保留,但发 warning 到 SearchResult.warnings
    min_keep 参数只对"正常大小 chunk"生效;超长 chunk 单独走截断路径。
    """
    hits = remove_duplicates(hits)
    hits = filter_by_score(hits, score_threshold)
    if max_tokens:
        hits, warnings = filter_by_token_budget(
            hits, max_tokens, min_keep=1, tokenizer=tokenizer,
        )
    return hits, warnings
```

### 7.6 引用组装

```python
def build_prompt(query, citations, template: str | None = None):
    """M1 修正: prompt 模板支持 dataset 级配置。

    template 形如 "{citations} {query}",用 str.format 插值。
    不传则用 dataset.prompt_template,再 fallback 到 DEFAULT_PROMPT_TEMPLATE。
    """
    tpl = template or dataset.prompt_template
    cite_blocks = "\n\n".join(
        f"[{i+1}] 来源:{c.source_name}\n{c.content}"
        for i, c in enumerate(citations)
    )
    return tpl.format(citations=cite_blocks, query=query)
```

### 7.7 引用校验工具(`retrieval/citation_check.py`)

即使 library 不生成 LLM 响应,仍提供工具让上层 caller 校验:

```python
class CitationChecker:
    """校验 LLM 生成文本中的 [n] 引用编号是否真的对应 citations。"""
    
    def check(
        self, llm_response: str, citations: list[Citation]
    ) -> CitationCheckResult:
        # 1) 提取 LLM 回答中的所有 [n] 引用编号
        cited_ids = re.findall(r'\[(\d+)\]', llm_response)
        cited_idx = [int(i) - 1 for i in cited_ids if i.isdigit()]
        
        # 2) Citation Recall: 引用的编号是否都对应有效 citation
        valid_idx = [i for i in cited_idx if 0 <= i < len(citations)]
        recall = len(valid_idx) / max(len(cited_idx), 1)
        
        # 3) Citation Precision: 提供的 citations 中有多少被实际引用
        used = set(valid_idx)
        precision = len(used) / max(len(citations), 1)
        
        # 4) 检测"幻觉引用": LLM 引用了不存在的 citation (idx 越界)
        hallucinated = [i for i in cited_idx if i not in valid_idx]
        
        return CitationCheckResult(
            recall=recall,
            precision=precision,
            hallucinated_citations=[citations[i] if 0 <= i < len(citations) else f"<invalid:{i+1}>" for i in hallucinated],
            unused_citations=[c for i, c in enumerate(citations) if i not in used],
        )
```

- LLM 生成回答后,调用 `CitationChecker.check()` 得到指标
- `CitationCheckResult` 可写回 SearchResult.warnings 或外层反馈
- 单测覆盖: 正常引用、幻觉引用、无引用、引用编号越界

### 7.8 Prompt 模板管理(`domain/dataset.py` 扩展)

- `Dataset.prompt_template`: 文本模板,支持 `{query}` `{citations}` 两个占位符
- `Dataset.system_prompt`: 可选 system message(给 LLM 的角色设定)
- 默认值在 `config.DEFAULT_PROMPT_TEMPLATE` / `config.DEFAULT_SYSTEM_PROMPT`

```python
# domain/dataset.py (扩展)
class Dataset(BaseModel):
    ...
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE
    system_prompt: str | None = None
```

- 业务场景: 学术引用 vs 产品文档 vs 法律条文,模板差异大
- 实施: 改 template 字段即可,无需改代码

---

## 8. 多级 Redis 缓存

### 8.1 层级与 TTL

| 级别 | 内容 | TTL | key 模式 |
|------|------|-----|----------|
| L1 | embedding | 24h | `rag:emb:{model}:{hash}` |
| L2 | query variants | 30min | `rag:qext:{model}:{hash}` |
| L3 | 端到端 search result | 5min | `rag:search:{hash}` |
| L4 | rerank hits | 1h | `rag:rk:{model}:{hash}` |

### 8.2 key 规范

- 顶层 namespace:`rag`
- 模型版本强制入 key(避免模型切换读到旧向量)
- `payload` 用 `json.dumps(..., sort_keys=True)` 后 `sha256` 前 16 字节 hex
- `L3 hash` 包含 `dataset_ids` 列表 + query + top_k + 模型版本

### 8.3 数据结构

- L1/L2/L4: `STRING` + JSON 序列化
- L3: `HASH`,fields = `citations` / `prompt` / `created_at` 三个固定字段
  - **L4 修正**: 选 HASH 而非 STRING 是为**单字段更新不重写整个 value**;
    缺点是 HASH 不支持 per-field TTL,整 key 共用 5min。
    未来若加 `latency_ms` 等监控字段,需拆为独立 STRING 键。
  - 评估: 当前 3 字段 + 高频读,HASH 优势在"局部更新",但 L3 实际上每次都是全量写,
    优势不显著。可考虑回归 STRING + JSON 简化(本期保留 HASH,因为已经写好)

### 8.4 失效

| 触发 | 范围 |
|------|------|
| **单 dataset chunks 增删(H1 修正)** | 清 L3 (search 缓存结果含旧 chunks) + 清 L4 (rerank 依赖文档内容) |
| dataset 删除 | 清 L3 + L4 |
| 切换 embed_model | 清 L1 + L2 + 该 dataset 相关 L3 |
| L1/L2 模型同名升级 (H1 修正) | 走"手动 flush rag:emb:*" CLI 命令 |
| L3 chunk 增删期间 5min TTL 内 | 接受短暂脏读(默认 5min);高一致性场景调短 TTL |

### 8.5 一致性边界

- L1/L2 强(模型不变 + 显式 flush)
- L3 弱(5min TTL 内可容忍脏读;chunk 增删时主动失效)
- L4 强(文档变 → 立即清)

**L1 模型版本 hash 入 key(H1 修正)**: 完整 key 形如 `rag:emb:{model}:{provider_version}:{hash}`,
`provider_version` 由 LLM provider header 返回(如 OpenAI `x-model-version`),
这样模型同名升级时(如 `text-embedding-3-small` 静默换实现)key 自动失效。
实施复杂度低,OpenAI 当前未公开该 header,**本期 stub 为空字符串**,等 provider 支持后补齐。

### 8.5.1 Redis 不可用降级(review 3 新增)

```python
# infra/cache/connection.py
class Cache:
    """Redis 缓存,不可用时降级到直连模式。"""
    
    async def get(self, key: str) -> str | None:
        try:
            return await self.client.get(key)
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"Redis get 失败,降级直连: {e}")
            return None   # 当作 cache miss
    
    async def set(self, key: str, value, ex: int | None = None):
        try:
            s = value if isinstance(value, str) else json.dumps(value, default=str)
            await self.client.set(key, s, ex=ex)
        except (ConnectionError, TimeoutError) as e:
            logger.warning(f"Redis set 失败,降级忽略: {e}")
            # 不抛错,缓存写入失败不应阻塞主流程
```

**降级策略**:

| 缓存层 | Redis 不可用影响 | 降级行为 |
|--------|------------------|----------|
| L1 embedding | 高频 query 重新 embed | 直连 LLM API,多花 latency;warning |
| L2 query_ext | LLM 改写重复执行 | 同上 |
| L3 search | 端到端结果不缓存 | 每次完整跑 pipeline |
| L4 rerank | rerank 结果不缓存 | 每次重新调 rerank API |

**SearchResult 标记**:
- 任意一层 cache 不可用时,在 `SearchResult.warnings` 加一条 `"redis_unavailable: layer=L1"`
- 业务侧可监控此 warning 频次,决定是否触发运维告警

**配置**:
- `redis_url` 配错 / Redis 进程挂 → 自动进入降级模式
- 客户端连接超时:`socket_timeout=1.0`(1s 超时即降级,不阻塞主流程)

**行为保证**:
- Redis 挂不会导致 pipeline 抛错
- 性能影响: 多 0.5-2s (per LLM API 调用),仍可用
- 一致性: 缓存层无,所有数据从源头取,反而是强一致

### 8.6 并发控制(review 1 强化版)

**config.py 全局定义**:

```python
# config.py
@dataclass
class LLMSettings:
    """LLM 全局配置,query / ingest 路径统一使用。"""
    max_concurrent: int = 16
    max_concurrent_per_provider: dict[str, int] = field(default_factory=lambda: {
        "openai": 16,
        "cohere": 8,
        "minimax": 16,
    })
    rate_limit_rpm: dict[str, int] = field(default_factory=lambda: {
        "openai": 3000,
        "cohere": 100,
        "minimax": 2000,
    })
```

**infra/llm/semaphore.py 全局应用(query + ingest 共享)**:

```python
class LLMSemaphore:
    def __init__(self, settings: LLMSettings):
        self._sem_global = asyncio.Semaphore(settings.max_concurrent)
        self._sem_per_provider = {
            p: asyncio.Semaphore(n) for p, n in settings.max_concurrent_per_provider.items()
        }
        self._rpm_windows: dict[str, deque] = {
            p: deque(maxlen=n) for p, n in settings.rate_limit_rpm.items()
        }
    
    async def run(self, provider: str, coro):
        async with self._sem_global:
            async with self._sem_per_provider[provider]:
                await self._check_rpm(provider)
                return await coro
    
    async def _check_rpm(self, provider: str):
        # 60s 滑动窗口
        now = time.time()
        window = self._rpm_windows[provider]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self._settings.rate_limit_rpm[provider]:
            sleep_for = 60 - (now - window[0])
            await asyncio.sleep(sleep_for)
        window.append(now)
```

**query 侧并发预算**: 5 dataset + rerank + 3 query variants = 3 (qext) + 10 (per-ds embed+rerank) = **13 LLM 调用**
- 13 < max_concurrent=16,本场景不排队
- 经验值: `max_concurrent ≥ datasets × (1 + use_rerank) × 1.5`

**ingest 侧**: 复用同一信号量,batch_size=32 顺序处理,LLM 调用并行

### 8.7 可观测性(原 H5)

每个 Runnable 节点统一注入 LangChain Callback,采集:

| 指标 | 用途 |
|------|------|
| 各阶段耗时分布 | 定位瓶颈(query_ext / embed / fuse / rerank / filter) |
| 缓存命中率 | L1/L2/L3/L4 独立统计 |
| RRF 融合前后 rank 变化 | 评估 fusion 公式有效性 |
| LLM 调用 token 消耗 | 成本归因 + 异常突增告警 |
| 失败 dataset 列表 | 与 SearchResult.failed_dataset_ids 双向校验 |

- 流水线入口 `config={"callbacks": [JsonLoggingHandler(), LangSmithTracer()]}`
- `JsonLoggingHandler` 输出 `{"ts", "stage", "latency_ms", "tokens", "cache_hit"}` 单行 JSON
- 失败 dataset 列表**两路记录**:SearchResult 字段(同步)+ 结构化日志(异步)

### 8.8 Ingest 异步化(M2 修正)

```python
# ingest/pipeline.py — producer-consumer 模式
async def ingest_directory(path: Path, dataset_id: UUID, batch_size: int = 32):
    """异步 ingest: reader+chunker 生产 → embed batch → PG insert batch。"""
    # 生产: 文件 → chunks (CPU bound, 用 thread pool)
    chunk_iter = await asyncio.get_event_loop().run_in_executor(
        None, _sync_read_and_chunk, path, dataset_id
    )
    # 消费: 攒 batch → 批量 embed → 批量 insert
    batch: list[Chunk] = []
    async for chunk in chunk_iter:
        batch.append(chunk)
        if len(batch) >= batch_size:
            await _flush_batch(batch)   # 一次 embed 32 个 + 一次 insert 32 个
            batch = []
    if batch:
        await _flush_batch(batch)

async def _flush_batch(batch: list[Chunk]):
    # 1) 批量 embed (LLMSemaphore 限流, 默认 16)
    texts = [c.text for c in batch]
    vecs = await embed_model.aembed_documents(texts)
    # 2) 批量 jieba + tsvector
    ts_vecs = [_build_tsvector(c.text) for c in batch]
    # 3) 批量 INSERT
    await conn.executemany("INSERT INTO chunks (...) VALUES (...)", [...])
```

- 性能瓶颈是 embedding API 网络 IO,单条串行 1000 文档可能 5+ 分钟,批量 + 并发可压到 1 分钟内
- `batch_size` 默认 32,可调
- LLM 并发仍走 `LLMSemaphore` 限流,避免触发 provider 限流

---

## 9. 测试 + 评估

### 9.1 覆盖率目标

| 模块 | 目标 |
|------|------|
| `domain/` | 100% |
| `ingest/chunker.py` | 95% |
| `pipeline/fusion.py` | 95% |
| `pipeline/filter.py` | 90% |
| `infra/cache/` | 85% |
| `infra/pg/` | 75% |
| `infra/llm/` | 70% |
| **整体** | **≥80%** |

### 9.2 单元测试关键点

- **chunker**:12 级分隔符、标题继承、代码块保护、overlap 比例、min/max chunk 强制、末块合并
- **fusion**:RRF 公式正确性、单/双源、同 chunk 累加、排序降序
- **filter**:md5 去重、score 阈值、token 预算(至少保留 1)
- **cache_keys**:确定性 + 模型敏感性
- **cite**:prompt 拼接格式

### 9.3 集成测试

- `testcontainers` 起 `pgvector/pgvector:pg16` + `redis:7`
- 验证 HNSW 索引实际可用,tsvector 检索中文
- Redis 缓存命中 / 失效

### 9.4 E2E 测试

- 1 个端到端:ingest 小语料 → search → 拿到 citations

### 9.5 Eval 全栈: L1/L2/L3/L4 分层(扩展)

| 层级 | 评估对象 | 指标 |
|------|----------|------|
| **L1 组件级** | chunker / embed / rerank 单组件 | chunk 长度分布、语义边界保持率、jieba OOV 率 |
| **L2 检索级** | 整个 retrieval pipeline | Recall@K / Precision@K / MRR / NDCG / Hit Rate |
| **L3 生成级** | 检索结果 + LLM 生成 | Faithfulness / Answer Relevancy / Context Precision / Citation Recall |
| **L4 用户级** | 真实用户反馈 | (本期不实现)显式 👍/👎、引用点击率 |

#### 9.5.1 L1 组件级评测

**chunker 质量**:
- chunk 长度分布直方图(应接近 1000±20%,无极端长尾)
- 语义边界保持率:人工标 50 条 "理想切分点",计算 chunker 切分点与之重合率
- 标题继承正确率:含 markdown 标题的文档,chunk 是否保留父标题

**embedding 召回质量**:
- 同 section chunk 间的 cosine similarity > 跨 section 的(分布对比)
- 维基类语料上:同词条 chunks 的 avg similarity > 不同词条

**分词覆盖**:
- jieba 词典内/外词比例(OOV 率)
- 行业专有词表可注入(本期不提供,但留 API:`jieba.add_word("RAG")`)

#### 9.5.2 L2 检索级评测

**评测集构建**:

1. **Synthetic Query Generation**(自动规模化)
   ```python
   # tests/eval/synthetic.py
   async def gen_synthetic_queries(chunks: list[Chunk], n: int = 200) -> list[EvalQuery]:
       """用 LLM 为随机 chunk 生成 question,作为正样本。"""
       results = []
       for chunk in random.sample(chunks, n):
           question = await llm.ainvoke(
               f"基于以下段落,生成一个用自然语言查询能命中该段落的问题:\n\n{chunk.text}"
           )
           results.append(EvalQuery(
               query=question,
               relevant_chunk_ids=[chunk.id],
               irrelevant_chunk_ids=random.sample(  # hard negative
                   [c.id for c in chunks if c.id != chunk.id], k=3
               ),
           ))
       return results
   ```

2. **Human-Annotated Gold Set**(已含 hard negatives)
   ```json
   {
     "id": "q-001",
     "query": "RAG 的向量检索和关键词检索如何融合?",
     "relevant_chunk_ids": ["uuid-1", "uuid-2"],
     "irrelevant_chunk_ids": ["uuid-3", "uuid-7"],
     "answer": "RRF 是一种融合方法...",
     "tags": ["concept", "fusion"],
     "difficulty": "medium"
   }
   ```
   - `relevant_chunk_ids`: 期望命中的 chunks
   - `irrelevant_chunk_ids`: hard negative(语义相似但不相关),用于验证排序能力

**检索指标**:

| 指标 | 公式 | 适用场景 |
|------|------|----------|
| Recall@K | `\|hits ∩ relevant\| / \|relevant\|` | 通用,K=top-K 候选 |
| Precision@K | `\|hits ∩ relevant\| / K` | 评估噪音水平 |
| MRR | `mean(1/rank_of_first_relevant)` | 看重排序头部 |
| NDCG@K | `DCG / IDCG` | gold set 有多级相关度 |
| Hit Rate | `1 if any relevant else 0` | 最低门槛 |

**与 L3 的相关性**:
- 在 gold set 上同时跑 L2 和 L3 指标,计算 Spearman 相关系数
- 若 Recall@10 与 Faithfulness 高相关,CI 只需跑 L2 做快速 gate
- 若低相关,两个都要跑

#### 9.5.3 L3 生成级评测(扩展)

在原 RAGAS 四指标基础上增加:

| 指标 | 方法 | 工具 |
|------|------|------|
| Citation Recall | 检查 LLM 回答中 [n] 引用编号是否对应真实 citation | 正则 + citation.id 比对 |
| Citation Precision | 检查 citations 中有多少被 LLM 实际使用 | NLI 模型判断 |
| Hallucination Rate | 每个 claim 是否能被 citations 支撑 | NLI 模型 |
| SelfCheckGPT | 多次采样 LLM,检查一致性 | 多次调用 + BERTScore |

**LLM-as-judge 简化版**(本期):
- 单一 LLM 当 judge(默认 OpenAI GPT-4o)
- 暂不实现 multi-judge 交叉验证(复杂度太高,与"中规模 + 学习用"定位不符)
- 已知 bias(position bias)文档化,实施时随机化回答顺序

**Gold set 阈值建议**:
- 通用 QA: Faithfulness ≥ 0.85, Context Recall ≥ 0.80
- 严格场景(法律/医疗): Faithfulness ≥ 0.95, Hallucination ≤ 0.05
- 本期不设硬阈值,跑完输出报告由 owner 决定

#### 9.5.4 L4 用户级评测(本期不实现)

理由: 需要线上 A/B 框架 + 用户反馈收集,与 library 定位不符。
可能涉及的能力: 显式 👍/👎、引用点击率、回答复制率、追问率。
留作未来扩展。

### 9.6 Eval 时机矩阵

| 时机 | 跑什么 | 耗时预算 | 阻止合入? |
|------|--------|----------|-----------|
| **pre-commit** | 单元测试 + L1 组件指标 | <30s | 是 |
| **on-PR** | 单元+集成 + L2 检索指标(synthetic) | <5min | 是,Recall 退化 >2% block |
| **on-merge** | 全量 L2+L3(含 gold set) | <30min | 否,告警 |
| **nightly** | L3 RAGAS 全套 + L2 全 gold set | <2h | 否,趋势报告 |
| **weekly** | 全量 + 人工抽检 + hallucination 审计 | <8h | 否,review |
| **pre-release** | L4 用户 A/B test | 1-7 天 | 是,统计显著退化则回滚 |

### 9.7 Regression Testing(回归测试)

```python
# tests/eval/regression.py
REGRESSION_QUERIES = [
    "RRF 公式是什么?",           # 已知容易出错
    "pgvector HNSW 索引参数?",   # 边界
    "图片如何存储?",              # 多模态相关
    "缓存如何失效?",              # 工程细节
]

async def test_regression():
    """每次变更前后跑同一组 query,比对检索结果集。"""
    for q in REGRESSION_QUERIES:
        result_before = await run_pipeline(q)
        result_after = await run_pipeline(q)
        # review 2 修正: HNSW 近似搜索非确定性,改用 Jaccard 相似度
        set_before = {c.chunk_id for c in result_before.citations}
        set_after = {c.chunk_id for c in result_after.citations}
        if not set_before and not set_after:
            continue
        jaccard = len(set_before & set_after) / len(set_before | set_after)
        assert jaccard >= 0.95, \
            f"regression: {q} Jaccard={jaccard:.3f} < 0.95 (before={len(set_before)}, after={len(set_after)})"
```

- 固定 20-30 条边界 query 集
- 任何 PR 改动 retrieval 相关代码,必须保证检索结果集高度相似(Jaccard ≥ 0.95)
- **review 2 修正**: 严格相等比较对 HNSW 近似搜索不成立;
  pgvector HNSW 受 `ef_search`、索引状态、tie-breaker 影响,小幅度 rank 漂移正常
- 阈值 0.95 是经验值: ≥ 0.95 表示核心召回稳定,< 0.95 提示实质性变化需人工 review
- 配套: 另存 result_before 的 top-3 citations 作为"金标准",人工抽检比对

### 9.8 CI

```yaml
# .github/workflows/ci.yml
- pytest tests/unit --cov=src/rag --cov-fail-under=80
- pytest tests/integration  # testcontainers
- pytest tests/eval/regression.py  # 回归测试
- ruff check + mypy
# RAGAS 走 weekly schedule job(需真实 LLM)
# L1/L2 retrieval 指标 on-PR
```

---

## 10. Chunk 更新/删除策略(M4 新增)

**本期策略: 整批替换(DELETE + INSERT),不做 chunk 级 diff**

```python
async def update_dataset_chunks(dataset_id: UUID, filename: str, new_chunks: list[Chunk]):
    """源文件重新 ingest 时: 删该 filename 的旧 chunks,写新 chunks。"""
    async with conn.transaction():
        await conn.execute(
            "DELETE FROM chunks WHERE dataset_id = $1 AND filename = $2",
            dataset_id, filename,
        )
        await _bulk_insert_chunks(new_chunks)
```

- dataset 级删除: `DELETE FROM datasets WHERE id = ...` 触发 `ON DELETE CASCADE` 清全部 chunks
- 整 dataset 删除**不**软删除(无 archived 字段,数据可恢复性差),业务侧须在外层做归档
- 行为说明: 这是**预期行为**,不是 bug,理由:
  - 简化实现
  - 重新 ingest 通常意味着源文件完全替换,旧 chunks 全部过时
  - 软删除会让 chunks 表膨胀,索引性能下降

## 11. 启动流程

```bash
# 1. 起依赖
make up            # docker compose up -d (PG + Redis)

# 2. 装依赖
uv sync

# 3. 灌数据
uv run rag ingest ./docs/ --dataset-id=$(uuidgen) \
  --embed-model=text-embedding-3-small

# 4. 搜索
uv run rag search "什么是 RAG?" \
  --dataset-ids=<id> --top-k=5 --rerank

# 5. 评估
make eval
```

---

## 12. 关键决策摘要

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 部署形态 | Library + CLI | 灵活、嵌入友好 |
| LangChain 抽象 | 全 Runnable/LCEL | 与生态兼容,LangSmith 可观测 |
| 数据规模 | 中规模 + 多 dataset | pgvector HNSW 足够 |
| LLM/Embed | OpenAI 协议 | 国产模型也走这个 |
| 测试策略 | 单测 + 集成 + RAGAS | 全覆盖 |
| Redis 范围 | 多级缓存 | 命中率/成本平衡 |
| 项目位置 | `~/pro/rag-pipeline/` | 独立项目,与 FastGPT 平级 |
| 架构 | 子图并行 + 顶层合并 | 多 dataset 隔离好 |
| 全文分词 | jieba 预分词 | 中文支持好 |
| ID 类型 | uuid.UUID (pgcrypto) | 原生 16 字节 |
| 图片路径 | 多模态大模型(M3) caption → 文本 | 与 FastGPT 对齐,主流方案 |
| chunk 复刻 | 深度复刻 12 级 | 同质量 chunks 利于 cross-compare |

---

## 13. Chunker 12 级分隔符详细说明(L5 新增)

参照 FastGPT `packages/global/common/string/textSplitter.ts` 的 `commonSplit()` 函数,
分隔符优先级(自顶向下递归):

| Step | 分隔符 | 类型 | overlap? | maxLen |
|------|--------|------|----------|--------|
| 0 | 用户自定义 regex (`customReg`) | 硬切 | ❌ | maxSize |
| 1-5 | Markdown 标题 H1-H5 (`# ...` 至 `##### ...`) | 标题继承 | ❌ | chunkSize |
| 6 | 代码块 ` ``` ... ``` ` 或 `~~~...~~~` | 保护独立 | ❌ | min(maxSize, chunkSize*4) |
| 7 | HTML table `<table>...</table>` | 完整保留 | ❌ | chunkSize |
| 8 | Markdown table (按行) | header 重复 | ❌ | chunkSize*1.2 |
| 9 | `\n\n` (段) | 软切 | ✅ | chunkSize |
| 10 | `\n` (行) | 软切 | ✅ | chunkSize |
| 11 | 中英句号 `。` / `. ` | 软切 | ✅ | chunkSize |
| 12 | 中英叹号 `！` / `! ` | 软切 | ✅ | chunkSize |
| 13 | 中英问号 `？` / `? ` | 软切 | ✅ | chunkSize |
| 14 | 中英分号 `；` / `; ` | 软切 | ✅ | chunkSize |
| 15 | 中英逗号 `，` / `, ` | 软切 | ✅ | chunkSize |
| 16 | 字符级滑窗 (chunkSize - overlapLen) | 兜底 | ✅ | chunkSize |

**算法**: 递归下钻;小块累积,过大再切;末块过小合并。

**Caption 特殊路径**(M5 修正):
- 检测输入文本是否含 markdown 标题(正则 `^#{1,5} `)
- 无 → 跳过 step 1-5,直接进入 step 6 (代码块) → step 9 (`\n\n`)
- 有 → 完整 12 级

**关键边界**:
- `paragraph_chunk_min_size = 100`: 切出来的段 <100 字符时,与邻段合并
- `min_chunk_size = 64`: 累积中的 lastText < 64 时不切
- `overlap_ratio = 0.15`: 倒着走分隔符取尾部 ≤ chunkSize*0.4 作下 chunk 开头

---

## 14. 风险与边界

| 风险 | 缓解 |
|------|------|
| LangChain API 变动 | 锁定版本,`langchain==0.3.x` |
| 多模态大模型调用慢且贵 | ingest 时单次,运行时只走文本 |
| 多 dataset 召回合并偏移 | 顶层 RRF + dataset 权重可配 |
| RAGAS 评估需真实 LLM | CI 拆 scheduled 与 on-PR,避免 PR 阻塞 |
| 中文 tsvector 召回质量 | jieba 默认词典;不引入同义词扩展 |
| pgvector HNSW 大规模性能 | 当前规模 < 100k chunks 时延 < 50ms;不验证 >1M 场景 |

---

## 15. pgvector HNSW 调优指南(M3 新增)

### 13.1 索引参数

```sql
-- 当前默认(已写入 schema.sql)
CREATE INDEX chunks_embedding_hnsw
  ON chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
```

| 参数 | 含义 | 默认 | 调优方向 |
|------|------|------|----------|
| `m` | 每节点邻居数 | 16 | 召回率↑,m↑(8-48);建索引时间↑;查询时延略↑ |
| `ef_construction` | 建索引时搜索宽度 | 64 | 建索引质量↑,ef↑(64-200);建索引时间显著↑ |
| `ef_search` | 查询时搜索宽度(会话级) | 40 | 召回率↑,ef_search↑(40-400);查询时延↑ |

### 13.2 不同规模推荐参数

| Chunk 量级 | m | ef_construction | ef_search | 召回率 | 时延 |
|------------|---|-----------------|-----------|--------|------|
| < 10k | 16 | 64 | 40 | 0.95+ | < 10ms |
| 10k - 100k | 16 | 64 | 80 | 0.97+ | < 50ms |
| 100k - 1M | 24 | 128 | 100 | 0.97+ | < 100ms |
| > 1M | 不验证 | — | — | — | — |

### 13.3 验证索引生效

```sql
-- 1) 检查是否走 HNSW 索引
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM chunks
WHERE dataset_id = '...'
ORDER BY embedding <=> $1
LIMIT 10;
-- 期望: "Index Scan using chunks_embedding_hnsw"

-- 2) 强制不用索引,看全表扫描耗时作 baseline
SET enable_indexscan = off;
EXPLAIN ANALYZE ...;
```

### 13.4 调优决策点

- 召回率不足: 提升 `ef_search`(每次查询,无需重建索引)
- 召回率够但建索引慢: 降低 `ef_construction`,接受略低索引质量
- 内存紧张: 降低 `m`(但会显著影响召回率)
- 不确定: 保持默认 + 跑 EXPLAIN ANALYZE 验证

---

## 16. Gold Set 标注与维护(M6 新增)

### 14.1 文件格式

`tests/eval/goldset.jsonl`,每行一条:

```json
{
  "id": "q-001",
  "query": "什么是 RRF?",
  "ground_truth_chunks": [
    "uuid-of-chunk-a",
    "uuid-of-chunk-b"
  ],
  "ground_truth_answer": "RRF 是 Reciprocal Rank Fusion,一种...",
  "tags": ["concept", "fusion"],
  "created_at": "2026-06-10",
  "annotated_by": "nathan"
}
```

- `ground_truth_chunks`: 期望命中的 chunk UUID 列表(给 context_precision/recall 用)
- `ground_truth_answer`: 自由文本参考答案(给 faithfulness/answer_relevancy 用)
- `tags`: 分类标签(便于按类别切分评估)
- `annotated_by`: 谁标的(便于追溯)

### 14.2 版本管理

- goldset.jsonl 用 git 管理
- 语料变化(chunks 重 ingest)时,UUID 改变 → 旧 goldset 失效
- **流程**:
  1. 修改 goldset 前先跑 `rag eval --dry-run`,记录当前 RAGAS 分数
  2. 重新 ingest 后,旧 goldset 的 chunk UUID 已无效
  3. 用 `rag eval --validate` 检查,标记失效条目
  4. 手动重新标注(50-100 条,2-4 小时工作量)
  5. 提交新 goldset + 跑 `rag eval` 对比分数

### 14.3 标注流程

- 谁: 项目 owner(本期即 nathan 一个人)
- 工具: 暂用文本编辑器 + chunk_id 反查 CLI(`rag chunk --id=<uuid>` 看正文)
- 审核: 自审,无需多轮 review
- 节奏: 语料稳定时 1 次,语料变更时重做

---

## 17. RAG 特有评测维度

在标准 L1/L2/L3 之外,RAG 系统还应测试:

| 维度 | 测试方法 | 工具 |
|------|----------|------|
| **鲁棒性** | 对 query 加 typo、同义词替换、语序变换 | `tests/eval/robustness.py` 跑 query 变换集 |
| **时效性** | 新 ingest 的文档立即可检索 | 测 ingest 完到 search 命中的端到端时延 |
| **多语言** | 中文 query 查英文 docs / 混合 | 准备多语 gold set |
| **安全** | prompt injection / PII 泄露 | 攻击测试 query 集,验证 LLM 拒绝 |
| **幻觉防御** | query 问不存在于语料的问题 | LLM 应拒绝回答而非编造 |
| **多跳推理** | "A 和 B 的关系是什么?"跨 chunk 推理 | gold set 含多跳 question |

本期实施: 鲁棒性 + 幻觉防御(其他 4 项留作评估时再补)。
