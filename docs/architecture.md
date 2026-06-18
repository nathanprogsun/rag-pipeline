# architecture.md — High-level design

Conceptual overview of rag-pipeline. For module-level API, see
docstrings; for entry points see [`dev.md`](dev.md).

## Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  CLI (5g)                                                        │
│    rag-ingest | rag-search | rag-eval                             │
└──────┬──────────────────────┬──────────────────────┬─────────────┘
       │                      │                      │
       ▼                      ▼                      ▼
┌──────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│   Ingest     │  │   Search Pipeline   │  │    Eval Pipeline     │
│  (pre-M1)    │  │       (5a-5f)       │  │       (5h-5i)       │
│              │  │                     │  │                     │
│ FileSource   │  │ SearchPipeline      │  │ UnifiedEvalRunner    │
│ UrlSource    │  │   ├ 1 QueryExt      │  │   ├ EvalConfig       │
│ Normalizer   │  │   ├ 2 Subgraph×N    │  │   ├ GenMetricsBackend│
│ Chunker      │  │   │   (intra-fuse)  │  │   │   (naive|ragas)  │
│              │  │   ├ 3 InterVariant  │  │   ├ GateThresholds   │
└──────┬───────┘  │   ├ 4-5 Rerank      │  │   └ ArtifactWriter   │
       │          │   ├ 7 Filter        │  └──────────┬───────────┘
       │          │   ├ 8 ParentDoc     │             │
       │          │   ├ 9 Cite          │             ▼
       │          │   └ 10 Gen (LLM)    │  ┌─────────────────────┐
       │          │                     │  │   Audit (5e)          │
       │          │ Citation DTOs +     │  │   └ NDJSON per request│
       │          │ SearchResult (Pydantic)  └─────────────────────┘
       │          └──────────┬──────────┘
       │                     ▼
┌──────┴────────────────────────────────────────────────────────────┐
│  Domain Layer (Pydantic v2)                                        │
│    Chunk | ScoredDocument | SearchRequest | SearchResult | Citation  │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  Infrastructure (SQLAlchemy 2.0 async + LangChain + DashScope)      │
│    VectorRetriever | FulltextRetriever | ChunkRepository            │
│    QwenRerank | get_chat_model | get_embed_model | ChineseTokenizer │
└──────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  Storage                                                              │
│    PostgreSQL 16 + pgvector (vector + tsvector HNSW/GIN)             │
│    Redis 7 (optional L2 cache)                                       │
└──────────────────────────────────────────────────────────────────┘
```

## Search flow (Contract 8, 10 stages)

```
                 query
                   │
   ┌───────────────▼───────────────┐
 1 │ query_ext (5c)                │  ← FastGPT-aligned LLM rewrite + dedup
   └───────────────┬───────────────┘
                   │ variants[]
   ┌───────────────▼───────────────┐
 2 │ per-variant per-dataset       │  ← SearchSubgraph (5d1)
   │   vector retriever           │     vector + fulltext → intra-fuse
   │   fulltext retriever         │
   └───────────────┬───────────────┘
                   │ per-variant hits[]
   ┌───────────────▼───────────────┐
 3 │ inter-variant intra_fusion   │  ← Contract 1 (5a)
   └───────────────┬───────────────┘
                   │ fused hits[]
   ┌───────────────▼───────────────┐
 4 │ rerank (5d3)                  │  ← text-only via QwenRerank
 5 │ re-fuse (rerank vs original) │  ← Contract 8: rerank pre-inter-fuse
   └───────────────┬───────────────┘
                   │ reranked hits[]
   ┌───────────────▼───────────────┐
 7 │ filter (5b)                  │  ← dedup + score_breakdown threshold + token budget
   └───────────────┬───────────────┘
                   │ filtered hits[]
   ┌───────────────▼───────────────┐
 8 │ parent_doc (5d5)             │  ← window expansion via ChunkRepository.get_siblings
   └───────────────┬───────────────┘
                   │ expanded hits[]
   ┌───────────────▼───────────────┐
 9 │ cite (5d4)                   │  ← 1-based numbering → list[Citation]
   └───────────────┬───────────────┘
                   │ citations[]
   ┌───────────────▼───────────────┐
10 │ gen (5f make_llm_gen)        │  ← LLM call with [id](CITE) instruction
   └───────────────┬───────────────┘
                   │
                   ▼
              SearchResult
              ├ response: str
              ├ citations: list[Citation]
              ├ _intermediate_hits: list[ScoredDocument]   (Contract 6)
              ├ failed_dataset_ids: list[UUID]
              └ warnings: list[str]
```

## Contract compliance

| Contract | Implementation | Notes |
|---|---|---|
| 1 intra_fusion signature | `rag.search.retrieve.fusion.intra_fusion` | query-variant semantics |
| 2 score_breakdown semantics | `rag.search.post.filter.filter_by_score` | per-source max merge |
| 3 typed Pipeline.ainvoke | `rag.search.orchestrator.SearchPipeline` | `SearchRequest` + production/test 双模式 |
| 4 SearchResult.response | `rag.search.orchestrator` | LLM answer, not prompt |
| 5 [id](CITE) inline | `rag.search.post.cite.SimpleCite` + `rag.infra.text.citation_check` parser | 1-based |
| 6 _intermediate_hits | `rag.domain.search.SearchResult` | PrivateAttr, excluded from JSON |
| 7 with_cache removed | N/A | direct Cache.get/set instead |
| 8 stage ordering | `rag.search.orchestrator.ainvoke` | rerank pre-inter-fuse |
| 9 QueryDecomposer dropped | N/A | only QueryExtension |

See [`../.agents/design/2026-06-14-cross-task-contracts.md`](../.agents/design/2026-06-14-cross-task-contracts.md)
for full contract specifications.

## Eval loop (5h-5i)

```
   eval.jsonl (单条 record, 含检索+生成 ground truth)
   ┌──────────────────────────────────────────────────┐
   │ query | dataset_ids | ground_truth_chunk_ids     │
   │ k | reference_answer | reference_contexts        │
   └─────────────────────────┬────────────────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │  UnifiedEvalRunner           │
              │  ├ EvalConfig                │
              │  │   gen_backend: naive|     │
              │  │               ragas|skip  │
              │  ├ GenMetricsBackend         │
              │  ├ GateThresholds            │
              │  │   min_recall_at_k=0.7     │
              │  │   min_faithfulness=0.8    │
              │  ├ ArtifactWriter (optional) │
              │  └ baseline_diff (optional)  │
              └──────────────┬───────────────┘
                             │
                             ▼
              ┌──────────────────────────────┐
              │ UnifiedEvalSummary           │
              │  ├ sample_count              │
              │  ├ metric_aggregates         │
              │  ├ baseline_delta            │
              │  ├ gate: passed|fail         │
              │  └ exit_code: 0|1            │
              └──────────────────────────────┘
```

旧版 ``EvalRunner`` (retrieval-only) + ``RagasRunner`` (gen-only) 已合并删除。
``RagasRealRunner`` 改名为 ``backends/ragas.RagasBackend`` 实现同一协议。
Stub metrics 改名为 ``backends/naive.NaiveBackend``, docstring 明确标注非真实语义指标。

## CI (5j)

GitHub Actions at [`.github/workflows/ci.yml`](../.github/workflows/ci.yml):

1. Lint (ruff check src tests)
2. Unit tests (`tests/unit -m "not live_llm"`)
3. Integration tests (`tests/integration -m "not live_llm"`, real PG via service)
4. Coverage report (`coverage.xml` + term-missing)

## Deployment (5k)

- [`Dockerfile`](../Dockerfile) — multi-stage Python 3.13 + uv + pgvector
- [`docker-compose.yml`](../docker-compose.yml) — pg + redis + ad-hoc CLI runner

## See also

- [`dev.md`](dev.md) — local dev workflow
- [`README.md`](../README.md) — full project overview
