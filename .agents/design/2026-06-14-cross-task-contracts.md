# Cross-Task Contracts (Lockdown for Tasks 11-20 Implementation)

> **Date:** 2026-06-14
> **Status:** LOCKED — design freeze. Any change requires a P0 doc + new audit.
> **Source:** Synthesized from `docs/superpowers/plans/audit/2026-06-14-task11-20-summary.md` (10 task audits).
> **Scope:** Every implementation in tasks 11-20 must conform to these contracts. A test that violates a contract is a bug in the test, not the contract.

---

## Why this exists

The 10 audit reports surfaced **5 cross-task coupling P0s** (Cluster 5) and **3+ missing signature locks** (Cluster 4, 7, 8). Without lockdown, every implementation PR risks re-litigating decisions the user has already made. This note is the single source of truth.

**Approved by user (2026-06-14):**
- Scope = A3 (full spec, 4-6 weeks)
- QueryDecomposer = **dropped** (decision C)
- Rerank ordering = **pre-inter-fuse** (decision D, matches FastGPT)
- Cite format = **inline `[id](CITE)`** (decision E, matches FastGPT)
- Parent doc = **implement** (decision F)
- `intra_fusion` weights = **query variant semantics** (decision I, locked earlier)
- task 15 audit JSONL = **implement** (decision B from A3)

---

## Contract Index

| # | Contract | Affects tasks | Locked? |
|---|---|---|---|
| 1 | `intra_fusion(query_groups, weights=None, rrf_k=60)` — query variant semantics, per-group weights | 11, 12, 14, 16 | ✅ (round 1) |
| 2 | `ScoredDocument.score_breakdown: dict[str, float]` — per-source max merge | 11, 12, 16 | ✅ (round 1, `document.py:68`) |
| 3 | `pipeline.ainvoke(SearchRequest) -> SearchResult` — typed in/out | 14, 16, 17, 18, 19 | 🆕 (this doc) |
| 4 | `SearchResult.response: str` — LLM's *generated* answer (not the prompt) | 14, 19 | 🆕 (this doc) |
| 5 | `SearchResult.citations: list[Citation]` + inline `[id](CITE)` format | 14, 19 | 🆕 (this doc) |
| 6 | `SearchResult._intermediate_hits: list[ScoredDocument]` — `Field(exclude=True)` | 14, 16, 18 | 🆕 (this doc) |
| 7 | `with_cache` decorator **removed** — use `Cache.get/set(key, layer, warnings)` directly | 16 | 🆕 (this doc) |
| 8 | Stage ordering: **rerank → pre-inter-fuse** (per D, matches FastGPT) | 14, 16 | 🆕 (this doc) |
| 9 | `QueryDecomposer` **dropped** (per C) — only `QueryExtensionRunnable` | 13 | 🆕 (this doc) |

---

## Contract 1: `intra_fusion` signature

**Location:** `src/rag/pipeline/fusion.py` (to be created in task 11).

```python
from uuid import UUID
from rag.domain.document import ScoredDocument

DEFAULT_RRF_K: int = 60  # Cormack 2009 default

def intra_fusion(
    query_groups: list[list[ScoredDocument]],  # N query variants, each a list of hits
    weights: list[float] | None = None,         # per-query-variant trust weight, default uniform 1.0
    rrf_k: int = DEFAULT_RRF_K,
) -> list[ScoredDocument]:
    """N-way WRRF over query variants. Each group's local rank is
    enumerate(start=1); same chunk_id across groups is summed.
    score_breakdown[source] uses per-source max (per Contract 2).
    Returns NEW list, never mutates inputs.
    """
```

**Semantics (per audit P0-2, decision I):**
- `query_groups[g]` is **one query variant's combined retrieval result** (already merged across vector+fulltext upstream by the recall layer). NOT per-source groups.
- `weights[g]` is the per-query-variant trust weight (e.g. lower for paraphrases).
- On duplicate `chunk_id`, accumulate `w_g / (rrf_k + rank)` into `score` and `max` per source into `score_breakdown[source]`.

**Test contract (tests/unit/test_fusion.py):**
```python
def test_intra_per_group_weight_applied():
    """weights[0]=1.0, weights[1]=0.0 → variant 1 contributes 0, only variant 0 matters."""
    g0 = [doc("a", source="vector", score=0.9), doc("b", source="vector", score=0.5)]
    g1 = [doc("a", source="vector", score=0.99)]  # best score but weight=0
    fused = intra_fusion([g0, g1], weights=[1.0, 0.0])
    assert fused[0].chunk_id == uuid.UUID("a...")
    assert fused[0].score == pytest.approx(1.0 / 61)  # only g0's contribution
    # score_breakdown preserved
    assert fused[0].score_breakdown == {"vector": pytest.approx(0.9)}

def test_intra_query_variant_semantics():
    """B4 invariant: each group is a query variant, not a source type.
    The caller should pass groups like [rewrite1_hits, rewrite2_hits, original_hits]."""
    pass  # 3+ groups with same source across groups

def test_intra_score_breakdown_max():
    """Same chunk from same source in 2 groups: score_breakdown takes max."""
    a = doc("a", source="vector", score=0.7)
    a_better = doc("a", source="vector", score=0.95)  # higher in g1
    fused = intra_fusion([[a], [a_better]])
    assert fused[0].score_breakdown == {"vector": pytest.approx(0.95)}
```

**Affects:** task 11 (defines), task 12 (consumes), task 14 (calls in 2 sites — see audit G-P0-3), task 16 (orchestrator).

---

## Contract 2: `ScoredDocument.score_breakdown`

**Location:** `src/rag/domain/document.py:68` (already landed in round 1).

```python
score_breakdown: dict[str, float] = Field(default_factory=dict)
# Keys: 'vector' / 'fulltext' / 'caption' / 'rerank'
# Empty dict: single-source path that didn't go through fusion.
# Per-source max on duplicate sightings (aligns FastGPT `concatScore.find(type).value = max(...)`).
```

**Semantics:**
- `score_breakdown[source]` is the **raw similarity** from the source (cosine ~0-1, or bm25 normalized, or rerank 0-1).
- `score` is the **RRF sum** (a ranking signal in ~0.01-0.1 range; NOT a threshold signal).
- Threshold filters (task 12) MUST read from `score_breakdown[source]`, NOT from `score`.

**Test contract (tests/unit/test_document.py):**
```python
def test_score_breakdown_default_empty():
    """Single-source path → empty dict."""
    d = ScoredDocument(chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(), text="t",
                       score=0.5, rank=1, source="vector", metadata=...)
    assert d.score_breakdown == {}

def test_score_breakdown_mutable_via_model_copy():
    """frozen=False on model_config → fusion can update via model_copy."""
    d = ScoredDocument(...)
    d2 = d.model_copy(update={"score_breakdown": {"vector": 0.9}})
    assert d.score_breakdown == {}  # original unchanged
    assert d2.score_breakdown == {"vector": 0.9}
```

**Affects:** task 11 (writes), task 12 (reads in threshold), task 16 (reads in audit_tap), task 18 (reads in entity-level recall metric).

---

## Contract 3: `pipeline.ainvoke` typed I/O

**Location:** `src/rag/pipeline/full.py:build_full_pipeline` (to be created in task 16).

```python
from rag.domain.search import SearchRequest, SearchResult

class PipelineDeps(BaseModel):
    """Typed dependency injection for the pipeline. No dict-bag."""
    model_config = ConfigDict(frozen=True)
    llm: LLMClient            # any object with .achat(messages) -> str
    embedder: EmbedderClient  # any object with .aembed(texts) -> list[list[float]]
    cache: Cache              # rag.infra.cache.connection.Cache
    vector_store: VectorStore
    fulltext_store: FulltextStore
    audit_hook: AuditHook | None = None
    rerank_client: RerankClient | None = None

def build_full_pipeline(deps: PipelineDeps) -> Pipeline:
    """Returns a typed Pipeline object. Pipeline exposes .ainvoke(SearchRequest) -> SearchResult."""
    ...

class Pipeline(Protocol):
    async def ainvoke(self, req: SearchRequest) -> SearchResult: ...
```

**Semantics (per audit Cluster 5, G-P0-3 task 14):**
- Input: `SearchRequest` Pydantic model. No `dict[str, Any]`.
- Output: `SearchResult` Pydantic model. No `dict[str, Any]`.
- Dependencies: `PipelineDeps` Pydantic model. No `dict` bag.
- All async. The pipeline is a coroutine-returning function.

**Test contract (tests/integration/test_full_pipeline.py):**
```python
async def test_pipeline_ainvoke_typed_io():
    """Pipeline.ainvoke accepts SearchRequest, returns SearchResult."""
    deps = PipelineDeps(llm=FakeLLM(), embedder=FakeEmbed(), cache=NoopCache(),
                        vector_store=FakeVector(), fulltext_store=FakeFulltext())
    pipeline = build_full_pipeline(deps)
    req = SearchRequest(query="hello", dataset_ids=[uuid.uuid4()])
    result = await pipeline.ainvoke(req)
    assert isinstance(result, SearchResult)
    assert isinstance(result.response, str)
    assert isinstance(result.citations, list)
```

**Affects:** task 14 (defines Pipeline interface), task 16 (builds it), task 17 (CLI calls), task 18 (EvalRunner), task 19 (RAGAS runner).

---

## Contract 4: `SearchResult.response` (LLM answer, NOT prompt)

**Location:** `src/rag/domain/search.py:SearchResult` (needs rename: `prompt` → `response`).

```python
class SearchResult(BaseModel):
    """Search interface complete response.
    response: the LLM's *generated* answer text. Used for RAGAS faithfulness.
             May contain [id](CITE) inline citation markers (see Contract 5).
    """
    model_config = ConfigDict(frozen=True)
    response: str
    citations: list[Citation]
    failed_dataset_ids: list[uuid.UUID] = []
    warnings: list[str] = []
    _intermediate_hits: list[ScoredDocument] = Field(default_factory=list, exclude=True)
```

**Semantics (per audit task 19 P0-2):**
- `response` is the **LLM's generated answer**, possibly containing `[id](CITE)` markers.
- It is NOT the prompt that was sent to the LLM. (The old `prompt` field was misnamed; rename to `response`.)
- RAGAS `faithfulness` reads from `result.response`, not `result.prompt`.

**Test contract (tests/unit/test_search_result.py):**
```python
def test_response_holds_llm_answer_not_prompt():
    """`response` is the LLM's answer, not the input prompt."""
    r = SearchResult(
        response="The capital of France is [1](CITE).",
        citations=[Citation(chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
                            source_name="geo", content="Paris is the capital...",
                            score=0.9)],
    )
    # response is the answer, NOT "What is the capital of France?"
    assert "capital" in r.response
    assert "[1](CITE)" in r.response

def test_response_field_renamed_from_prompt():
    """The old `prompt` field is removed. SearchResult only has `response`."""
    r = SearchResult(response="x", citations=[])
    assert not hasattr(r, "prompt")
```

**Migration:** Existing code that reads `result.prompt` must be updated. Use `grep -rn "result.prompt\|r\.prompt\|\.prompt$" src/rag/` to find call sites.

**Affects:** task 14 (orchestrator sets response), task 19 (RAGAS reads from response).

---

## Contract 5: Inline citation format

**Location:** `src/rag/domain/search.py:Citation` (existing) + `SearchResult.citations`.

**Format:** The LLM's `response` text contains `[id](CITE)` markers, where `id` is the 1-based index into `SearchResult.citations`. Example:

```
SearchResult.response: "The capital of France is [1](CITE), located in [2](CITE)."
SearchResult.citations: [
    Citation(source_name="Wikipedia", content="Paris is the capital of France.", ...),
    Citation(source_name="Britannica", content="Paris is in north-central France.", ...),
]
```

**Semantics (per audit task 14 G-P1-2, decision E):**
- Renderer (or post-processor) parses `[id](CITE)` in `response` and replaces with formatted citation text.
- LLM is prompted to insert `[id](CITE)` at relevant points in its answer.
- `Citation` DTO is unchanged structurally; only its *usage* changes.
- `Citation.position: int | None = None` — 1-based position in `response` (inferred by regex during cite step, or set by LLM-aware post-processor).

**Test contract (tests/unit/test_citation.py):**
```python
def test_inline_citation_format_parser():
    """Given response 'a [1](CITE) b [2](CITE) c', parse 2 citations at positions 2, 8."""
    response = "a [1](CITE) b [2](CITE) c"
    citations = parse_inline_citations(response, candidates=[c1, c2, c3])
    assert len(citations) == 2
    assert citations[0].position == 2
    assert citations[1].position == 8

def test_citation_dto_unchanged():
    """Citation DTO structure is unchanged. Only usage differs."""
    c = Citation(chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
                 source_name="t", content="x", score=0.5)
    assert c.position is None  # not set by DTO, set by cite step
```

**Affects:** task 14 (cite.py implements parse + format), task 19 (RAGAS reads `response` with markers, doesn't care about format).

---

## Contract 6: `_intermediate_hits` exclude=True

**Location:** `src/rag/domain/search.py:SearchResult` (new field).

```python
class SearchResult(BaseModel):
    ...
    _intermediate_hits: list[ScoredDocument] = Field(default_factory=list, exclude=True)
```

**Semantics (per audit task 16 P1-2):**
- Stores pre-rrf, pre-rerank, pre-filter hits for debugging/eval.
- `exclude=True` means it does NOT appear in `.model_dump()` or `.model_dump_json()` output → safe to return to API consumers.
- Audit tap (task 15) reads `_intermediate_hits` to record full provenance.
- EvalRunner (task 18) reads `_intermediate_hits` for per-stage metrics (e.g. "recall before vs after rerank").

**Test contract (tests/unit/test_search_result.py):**
```python
def test_intermediate_hits_excluded_from_dump():
    """_intermediate_hits must not leak to JSON output."""
    r = SearchResult(response="x", citations=[],
                     _intermediate_hits=[ScoredDocument(...)])
    json_str = r.model_dump_json()
    assert "_intermediate_hits" not in json_str
    assert "intermediate_hits" not in json_str
    # But still accessible programmatically
    assert len(r._intermediate_hits) == 1

def test_intermediate_hits_default_empty():
    """Default is empty list, not None."""
    r = SearchResult(response="x", citations=[])
    assert r._intermediate_hits == []
```

**Affects:** task 14 (orchestrator populates), task 15 (audit reads), task 16 (pipeline passes through), task 18 (eval uses).

---

## Contract 7: `with_cache` decorator removed

**Location:** N/A (was spec'd in task 16, removed per audit G-P0-5).

**Decision:** The `with_cache` decorator proposed in task 16 G-P0-5 is **REMOVED**. Code that needs caching calls `Cache.get(key, layer, warnings)` / `Cache.set(key, value, ex, layer, warnings)` directly.

**Rationale (per audit task 16 G-P0-5):**
- `src/rag/infra/cache/connection.py:94-139` already provides `Cache.get` / `Cache.set` with `RedisError`-only handling, structured `logger.warning`, warnings-sink append, metrics increment.
- `with_cache` re-implemented these worse: `try/except Exception: pass` (catches `TypeError` from bad `key_fn` as cache miss), hard-coded TTLs, discards warnings.
- Direct `Cache` calls preserve spec §0.1 L226 ("Redis 不可用 → 降级直连 + warnings 标记, 不报错").

**Migration:** Any code that previously called `with_cache(fn, key_fn=...)` is rewritten to:
```python
async def call_with_cache(req: SearchRequest) -> SearchResult:
    key = make_cache_key(req)
    cached = await cache.get(key, layer="search", warnings=req.warnings)
    if cached is not None:
        return cached
    result = await expensive_compute(req)
    await cache.set(key, result, ex=settings.cache.l2_ttl, layer="search", warnings=req.warnings)
    return result
```

**Affects:** task 16 (replaces with_cache in `build_full_pipeline`).

---

## Contract 8: Stage ordering — Rerank PRE-inter-fuse

**Location:** `src/rag/pipeline/full.py` (task 16) and `src/rag/pipeline/orchestrator.py` (task 14).

**Order (per decision D, matches FastGPT `defaultRecall/rerank.ts:55-110`):**

```
1. QueryExtension      (task 13: rewrite query → N variants)
2. Recall per variant  (vector + fulltext per variant, then intra_fusion per variant)
3. Inter-variant Fusion (intra_fusion over N query variants)
4. Rerank (text-only hits, per task 14 rerank.py)
5. Re-fuse (intra_fusion over [reranked_text_hits, original_text_hits] with weights)
6. Inter-dataset Fusion (if multiple datasets, intra_fusion over datasets)
7. Filter (task 12: dedup, threshold via score_breakdown, token budget)
8. ParentDoc Expand (task 14: parent_doc.py)
9. Cite (task 14: cite.py, inline format)
10. Generation (LLM call with citation instruction)
```

**Key invariants (per decision D):**
- Rerank is **text-only** at step 4. Image hits are deliberately excluded from text rerank.
- Re-fuse at step 5 uses weights: `[rerank_weight, 1.0 - rerank_weight]` for `[reranked_hits, original_hits]`.
- Image hits bypass step 4 and are added at step 5/6 with weight `1.0`.

**Test contract (tests/integration/test_pipeline_ordering.py):**
```python
async def test_rerank_precedes_inter_fuse():
    """Image hits must NOT pass through text rerank."""
    # Build pipeline with stub rerank that records which chunks it sees
    rerank_recorder = RecordingRerank()
    pipeline = build_full_pipeline(deps_with(rerank=rerank_recorder))
    # Send a query with both text and image hits
    result = await pipeline.ainvoke(req_with_image_url)
    reranked_ids = rerank_recorder.seen_chunk_ids
    # All reranked chunks must be text modality
    for cid in reranked_ids:
        chunk = get_chunk(cid)
        assert chunk.metadata.modality == "text"  # NOT image_caption

async def test_refuse_weights_rerank_vs_original():
    """Step 5 re-fuse uses weights=[w, 1-w]."""
    pass
```

**Affects:** task 14 (orchestrator.py implements order), task 16 (pipeline.py exposes), task 18 (eval checks per-stage metrics at this order).

---

## Contract 9: `QueryDecomposer` dropped

**Location:** `src/rag/pipeline/query_ext.py` (task 13). **Do NOT create `decomposition.py`**.

**Decision (per C):** Only `QueryExtensionRunnable` (LLM rewrite query → N variants) is implemented. `QueryDecomposer` (sub-query split + lazy-greedy MMR selection) is **dropped**.

**Rationale (per audit task 13 P0-4, P1-5):**
- Lazy greedy is a rag-pipeline invention. FastGPT has no equivalent.
- MMR formula in the spec (`α·cos + (1-α)·(1-maxSim)`) is a coefficient divergence from FastGPT's `α·cos + 1·(1-maxSim)` (per audit P0-1 task 13) — debugging this is wasted work if the feature itself is not aligned with FastGPT.
- Deletion simplifies the test matrix: no MMR oracle, no lazy-greedy unit tests, no decomposition pytest fixtures.

**What survives in task 13:**
- `QueryExtensionRunnable` (Stage 1: LLM rewrite query → 3-5 variants; Stage 2: optionally filter variants via embedding deduplication)
- Tests: `test_query_ext_rewrite`, `test_query_ext_dedup_filter`
- Files: `src/rag/pipeline/query_ext.py` only

**What is removed:**
- `src/rag/retrieval/decomposition.py` (per plan tree line 132-136)
- `src/rag/retrieval/lazy_greedy.py` (per plan tree line 132-136)
- `tests/unit/test_decomposition.py`, `tests/unit/test_lazy_greedy.py` (phantom files referenced in task 20)

**Affects:** task 13 (define scope), task 20 (CI target list).

---

## Cross-task dependency map (updated)

```
task 11 (intra_fusion + score_breakdown) ──┬─→ task 12 (filter uses score_breakdown)
                                           ├─→ task 14 (rerank + orchestrator use intra_fusion)
                                           └─→ task 16 (build_full_pipeline uses intra_fusion)
task 13 (QueryExtension only, no Decomposer) ──→ task 16 (build_full_pipeline uses QueryExt)
task 14 (5 sub-modules: subgraph, orchestrator, rerank, cite, parent_doc)
                                                ──→ task 16
task 15 (audit + citation_check JSONL)  ──┬─→ task 16 (audit_tap hook)
                                           └─→ task 17 (audit subcommand)
task 16 (build_full_pipeline, pre-inter-fuse) ──┬─→ task 17 (search subcommand)
                                                  ├─→ task 18 (EvalRunner)
                                                  └─→ task 19 (RAGAS runner)
task 17 (CLI, 6 subcommands)   ──→ task 20 (CI gates the CLI tests)
task 18 (EvalRunner + 5 metrics)  ──→ task 20 (CI runs eval on PR)
task 19 (RAGAS + jaccard + compare_results) ──→ task 20 (weekly cron runs RAGAS)
```

**Topological order (locked):** 11 → 12 → 13 → 14 → 15 → 16 → 17 → 18 → 19 → 20.

---

## Test contracts summary

For each implementation, the test file MUST include the contract tests listed in each contract's section. These are non-negotiable:

| Contract | Test file | Test names |
|---|---|---|
| 1 | `tests/unit/test_fusion.py` | `test_intra_per_group_weight_applied`, `test_intra_query_variant_semantics`, `test_intra_score_breakdown_max` |
| 2 | `tests/unit/test_document.py` | `test_score_breakdown_default_empty`, `test_score_breakdown_mutable_via_model_copy` |
| 3 | `tests/integration/test_full_pipeline.py` | `test_pipeline_ainvoke_typed_io` |
| 4 | `tests/unit/test_search_result.py` | `test_response_holds_llm_answer_not_prompt`, `test_response_field_renamed_from_prompt` |
| 5 | `tests/unit/test_citation.py` | `test_inline_citation_format_parser`, `test_citation_dto_unchanged` |
| 6 | `tests/unit/test_search_result.py` | `test_intermediate_hits_excluded_from_dump`, `test_intermediate_hits_default_empty` |
| 7 | (covered by task 16 Cache integration tests) | — |
| 8 | `tests/integration/test_pipeline_ordering.py` | `test_rerank_precedes_inter_fuse`, `test_refuse_weights_rerank_vs_original` |
| 9 | (no tests; just absence) | — |

**CI enforcement:** task 20's CI must run all of these. A PR that violates a contract test is a sign-off blocker.

---

## Pre-implementation checklist (before starting Step 5a)

Before task 11 implementation starts, these source-file changes are prerequisites:

1. **`src/rag/domain/search.py`** — Rename `SearchResult.prompt` → `SearchResult.response`. Add `SearchResult._intermediate_hits: list[ScoredDocument] = Field(default_factory=list, exclude=True)`. Add `SearchResult.citation_format: Literal["inline", "prefix"] = "inline"` (per E). Update any callers (currently none in `src/rag/`).
2. **`src/rag/domain/document.py`** — Already done in round 1.
3. **`src/rag/retrieval/trace.py`** — Verify `RetrievalTrace` and `remove_duplicates` are stable; task 12 will re-export.

---

## Out of scope (deferred to v2, NOT A3)

Even with A3, these are explicitly **NOT** in this round (re-confirm with user before adding):

- Multi-modal end-to-end training / fine-tuning
- Distributed tracing via OTel (the JSONL audit in task 15 is a local-debug channel, not OTel)
- Postgres-backed `IngestDatasource` for `url` (currently stored as `manual`)
- LLM-based hallucination detection in `CitationChecker` (only regex-based, per audit)
- Real RAGAS `faithfulness` with custom LLM judge (task 19 ships a stub for v2; see G-P0-2)
- Cross-language SDK (JS/Go client wrappers)

---

## Sign-off

- **Author:** design synthesis from 10 task audits
- **Locked by:** 2026-06-14 audit round + user A1-A3 scope decision
- **Next:** task 11 implementation per `tasks/task11.md` (already doc-fixed in round 1)
- **Re-review trigger:** Any P0 fix in tasks 11-20 that touches these 9 contracts.

**A contract change = new audit + new design note revision. No silent edits.**
