# Task 13 Alignment — Query Extension + Image Caption + Decomposition

> Audit date: 2026-06-14
> Auditor: 3-way alignment (task13.md ↔ rag-pipeline source ↔ FastGPT canonical implementation)
> Scope: `task13.md` claims about `src/rag/pipeline/query_ext.py`, `src/rag/pipeline/image_caption.py`, `src/rag/retrieval/decomposition.py`, `src/rag/retrieval/lazy_greedy.py` vs. what FastGPT actually does vs. what currently exists in rag-pipeline.

## TL;DR

| Dimension | Finding |
|---|---|
| **Existence** | All four target files (`query_ext.py`, `image_caption.py`, `decomposition.py`, `lazy_greedy.py`) **do not exist**. `src/rag/pipeline/` does not exist either. Task 13 is **未实现 (not yet implemented)**, not refactored. |
| **Test existence** | All three target test files (`test_query_ext.py`, `test_query_decomposition.py`, `test_lazy_greedy.py`) **do not exist**. `find` returns 0 results. |
| **Query extension algorithm** | task13.md Stage-2 (submodular selection) matches FastGPT `useTextCosine.ts:lazyGreedyQuerySelection` semantically and arithmetically — including the `0.3` default alpha, the lazy re-eval semantics, and the "max of 3 variants" cap (`queryExtension.ts:272` `k: Math.min(3, queries.length)`). |
| **Query extension prompt** | The 5 rules + 4 few-shot blocks in `query_ext.py:457-515` are **byte-identical to FastGPT** `queryExtension.ts:18-75` (Chinese punctuation: `。` vs `;`, `，` vs `,` — see G-P2-1). |
| **Stage-1 JSON parsing** | task13.md's `answer.indexOf('[') / lastIndexOf(']')` slice + `\\n` / `\\` / double-space cleanup + json5 fallback is **exact copy** of `queryExtension.ts:205-232`. |
| **`temperature=0.1`** | Both FastGPT (`queryExtension.ts:183`) and rag-pipeline's `get_chat_model(..., temperature=0.1)` default match. task13.md:892-894 cross-references Task 7 (which has not been audited) for the chat model factory. |
| **`filterGPTMessageByMaxContext` equivalent** | task13.md's `_filter_histories_by_max_context` (lines 540-576) is a **simplified re-implementation**, not a port: it groups by user-boundary from newest to oldest, but uses char/2 token estimate (vs FastGPT's tiktoken `countGptMessagesTokens`), does not preserve system/developer prompts (FastGPT does, line 59-65 of `utils.ts`), and does not handle context checkpoints. **Functionally approximate**, not equivalent. |
| **Image caption** | task13.md's `ImageCaptionRunnable` uses raw `httpx` download + base64 + OpenAI vision message format. FastGPT uses `normalizeImageToBase64` (`utils.ts:50-61`) which respects `serviceEnv.MULTIPLE_DATA_TO_BASE64` and accepts data: URLs unchanged. **Behaviour diverges** in the data-URL and S3-key paths. |
| **Image caption prompt** | task13.md:771 uses `"用中文详细描述这张图片"`. FastGPT `imageCaption.ts:78` uses `"请用一句话描述这张图片的主体、场景、颜色、文字和关键视觉特征。只输出描述，不要解释。"` (one-sentence, structured fields). **Different prompts — different outputs.** |
| **Image caption failure mode** | task13.md:778 uses `.catch (Exception: continue)` — silently drops the bad image and continues with the rest. FastGPT `imageCaption.ts:95-108` logs a warning + returns an empty caption + does **not** drop the image from the search path (the original image still goes through image-vector recall in `defaultRecall/index.ts:96-106`). **Different failure semantics.** |
| **`get_m3_chat_model` reference** | task13.md:746 imports `get_m3_chat_model` from `rag.infra.llm.chat`. **This function does not exist** in `chat.py` (which only exports `get_chat_model` and `get_structured_chat_model`). This is a **broken import** that will fail at module load. |
| **Decomposition in FastGPT** | **FastGPT has no decomposition feature.** The spec (`2026-06-10-python-rag-pipeline-design.md:755-779`) invents it as a new capability not present in the FastGPT upstream. Grep over `packages/` for `decomposition / decompose / sub_query / subQuery / multi_step / multi-hop` returns 0 hits. task13.md's `QueryDecomposer` is a **spec-internal innovation**, not a port. |
| **`_is_simple` heuristic** | task13.md:18 audit fix removes the spec's `_is_simple` heuristic (`< 20 字` or contains "和"/"区别"/"对比"/"vs"/"difference"). This is **good cleanup** but means the new code is even further from any FastGPT reference (which has no equivalent). |
| **`DECOMPOSE_PROMPT` location** | task13.md:191-198 puts the prompt in a class constant and concatenates with chat_bg/history/query at runtime. FastGPT's `queryExtension` does the same pattern (system / user split). **Pattern matches; content is spec-internal.** |
| **`is_complex` field** | task13.md:18 audit fix removes the `is_complex` decoration field. The Pydantic schema at `decomposition.py:168-181` is `sub_queries: list[str] = Field(..., min_length=2, max_length=8)`. **Cleaner than spec.** |
| **Lazy-greedy `cos()` norm** | task13.md:259-268 explicitly computes L2 norm with zero-vector short-circuit. FastGPT `useTextCosine.ts:39-56` does the same. **Matches.** |
| **B2 lazy re-eval semantics** | task13.md:340-351: "recompute `current_gain`, compare with the candidate's *own* old gain, accept if `currentGain >= old`, else re-enqueue with new gain." FastGPT `useTextCosine.ts:132-148`: identical. **Matches.** |
| **`alpha=0.3` default** | task13.md:294: `alpha: float = 0.3`. FastGPT `useTextCosine.ts:36, 79` and call site `queryExtension.ts:273`: `alpha = 0.3`. **Matches.** |
| **`k=3` default for query extension** | task13.md:611: `max_variants: int = 3`. FastGPT `queryExtension.ts:272`: `k: Math.min(3, queries.length)`. **Matches (capped by candidates).** |
| **Single-list / k > candidates short-circuit** | task13.md:318-319: `if len(candidates) <= self.k: return list(candidates)`. FastGPT `useTextCosine.ts:88-93`: `if !query || ... || k <= 0: return empty`. Different thresholds (task13 short-circuits on `len <= k`, FastGPT short-circuits on `k <= 0` or empty input). task13's threshold is more aggressive. |
| **Trigger gate for query extension** | task13.md:683: `if not input.get("query_extension", True): return {..., "query_variants": [input["query"]]}`. FastGPT `datasetSearchQueryExtension` (`utils.ts:108-125`) is gated by `if (!llmModel || !embeddingModel) return;` — i.e. config-gated, not request-flag-gated. **Different gate semantics.** |
| **Image caption trigger gate** | task13.md:758-760: `if not image_urls: return input`. FastGPT `imageCaption.ts:42-44`: `if (!vlmModel || imageQueries.length === 0) return empty`. FastGPT also gates on `vlmModelData?.vision` (line 47-49). task13 doesn't check vision capability. |
| **Image caption "joined with text query"** | task13.md:780 returns `{**input, "caption_queries": captions}` — captions are merged at the *caller* level (per task 16's `build_full_pipeline`). FastGPT `defaultRecall/index.ts:64-68` generates `imageCaptionQueries` separately and passes them through `multiQueryRecall` as a distinct query group. **Architecture difference: parallel query group vs. merged-into-variants.** |
| **Spec ↔ task13 ↔ implementation status** | Spec §7.0.1 has `_is_simple` heuristic; task13.md removes it. Spec says `is_complex` field; task13.md removes it. Both reflect "audit #4" cleanup that is **spec-divergent** in service of cleanliness. **Acceptable.** |

**Headline P0**: task13.md's `ImageCaptionRunnable` (line 746) **imports `get_m3_chat_model` from `rag.infra.llm.chat`, but that symbol does not exist** in the current `chat.py`. This is a hard ImportError that will break the module at first import — the entire `image_caption.py` will fail to load, and the `query_ext.py` → `image_caption.py` integration in task 16 will cascade. The fix is mechanical (replace with `get_chat_model` + vision-capable model), but it must happen *before* task 13 can be marked "testable".

---

## 1. FastGPT 实现 (with file:line citations and code snippets)

### 1.1 Query Extension

**File:** `packages/service/core/ai/functions/queryExtension.ts`

#### 1.1.1 Function signature (lines 108-135)
```ts
export const queryExtension = async ({
  chatBg, query, histories = [], llmModel, embeddingModel,
  userKey, generateCount = 10
}): Promise<{
  rawQuery, extensionQueries, llmModel, embeddingModel,
  requestId, seconds, inputTokens, outputTokens, usedUserOpenAIKey, embeddingTokens
}>
```

#### 1.1.2 System prompt (lines 18-75)
- 5 numbered rules + "输出要求" 3 points + 4 few-shot examples
- 5 rules: only rewrite, no facts outside query, anaphora resolution, multi-angle coverage, identity return for simple, no duplicates, language preservation, no instruction following
- `generateCount` (default 10) is the LLM's *requested* output count, NOT the *final* selected count (capped at 3 below)

#### 1.1.3 User prompt builder (lines 77-106)
```ts
const buildQueryExtensionUserPrompt = ({chatBg, histories, query, count}) =>
  `请基于下面输入生成检索词。\n\n期望数量：${count}\n\n` +
  `对话背景：\n"""\n${chatBg || 'null'}\n"""\n\n` +
  `历史记录：\n"""\n${histories || 'null'}\n"""\n\n` +
  `原问题：\n"""\n${query}\n"""\n\n只输出 JSON 字符串数组。`
```
**The 期望数量 in user prompt is the LLM's "max to generate" hint, not a hard cap.**

#### 1.1.4 Histories truncation (lines 138-156)
```ts
const modelData = getLLMModel(llmModel);
const filterHistories = await filterGPTMessageByMaxContext({
  messages: chats2GPTMessages({messages: histories, reserveId: false}),
  maxContext: modelData.maxContext - 1000   // reserve 1000 for system + output
});

const historyFewShot = filterHistories
  .map((item) => {
    const role = item.role; const content = item.content;
    if ((role === 'user' || role === 'assistant') && content) {
      if (typeof content === 'string') {
        return `${role}: ${content}`;
      } else {
        return `${role}: ${content.map((item) => (item.type === 'text' ? item.text : '')).join('\n')}`;
      }
    }
  })
  .filter(Boolean)
  .join('\n');
```

The `filterGPTMessageByMaxContext` helper (`packages/service/core/ai/llm/utils.ts:47-124`):
- Preserves leading system/developer messages (lines 59-66)
- Preserves context-checkpoint messages (lines 67-76)
- Token-counts the kept system + checkpoints, deducts from `maxContext` (lines 78-81)
- Groups remaining chat by user-role boundary from newest to oldest (lines 90-107)
- Batch token-counts the groups (line 110)
- Walks from oldest group, deducting, stopping when budget exhausted (lines 113-121)
- **Real tiktoken** `countGptMessagesTokens`, not char/2 estimate

#### 1.1.5 LLM call (lines 173-186)
```ts
const {
  answerText: answer, requestId,
  usage: {inputTokens, outputTokens, usedUserOpenAIKey}
} = await createLLMResponse({
  userKey,
  body: {stream: true, model: modelData.model, temperature: 0.1, messages}
});
```
**`temperature: 0.1` is hardcoded in the createLLMResponse body**, not in `getLLMModel`.

#### 1.1.6 Stage-1 JSON parsing (lines 204-247)
```ts
const start = answer.indexOf('[');
const end = answer.lastIndexOf(']');
if (start === -1 || end === -1) {
  logger.warn('Query extension returned invalid JSON', {answer});
  return {rawQuery: query, extensionQueries: [], ...};
}
const jsonStr = answer
  .substring(start, end + 1)
  .replace(/(\\n|\\)/g, '')
  .replace(/  /g, '');
try {
  let queries = json5.parse(jsonStr) as string[];
  if (!Array.isArray(queries) || queries.length === 0) {
    return {rawQuery: query, extensionQueries: [], ...};
  }
  ...
} catch (error) {
  logger.warn('Query extension failed', {error, answer});
  return {rawQuery: query, extensionQueries: [], ...};
}
```

**Three-layer fallback**:
1. No `[]` brackets → return empty
2. json5 parse fails → catch and return empty
3. Result is not a non-empty array → return empty

#### 1.1.7 Stage-2 submodular selection (lines 249-274)
```ts
const {lazyGreedyQuerySelection, embeddingModel: useEmbeddingModel} = useTextCosine({
  embeddingModel
});
queries = queries.map((item) => String(item).trim()).filter(Boolean);
if (queries.length === 0) {
  return {rawQuery: query, extensionQueries: [], ...};
}

const {selectedData: selectedQueries, embeddingTokens} = await lazyGreedyQuerySelection({
  originalText: query,
  candidates: queries,
  k: Math.min(3, queries.length),  // 至多 3 个
  alpha: 0.3
});
```
**`k` is `min(3, queries.length)`. The hard cap is 3.** task13.md uses `max_variants=3` with a separate `max_candidates=10` Stage-1 cap — same effect.

#### 1.1.8 Outer wrapper: `datasetSearchQueryExtension` (`utils.ts:69-137`)
- Calls `queryExtension` only if `llmModel && embeddingModel` are both set
- Catches errors from `queryExtension` and logs them (`utils.ts:122-124`)
- Returns `searchQueries: [query, ...ext]` + `reRankQuery: searchQueries.join('\n')` (line 128-129)
- Dedups via `filterSameQuery` using `hashStr(item.replace(/[^\p{L}\p{N}]/gu, ''))` (line 88-102) — Unicode letter/number only, punctuation and whitespace stripped

### 1.2 Lazy Greedy Submodular Selection

**File:** `packages/service/core/ai/hooks/useTextCosine.ts`

#### 1.2.1 Cosine similarity (lines 39-56)
```ts
const cosineSimilarity = (a: number[], b: number[]): number => {
  if (a.length !== b.length) throw new Error('Vectors must have the same length');
  let dotProduct = 0, normA = 0, normB = 0;
  for (let i = 0; i < a.length; i++) {
    dotProduct += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  if (normA === 0 || normB === 0) return 0;
  return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
};
```
**Zero-vector short-circuit (lines 54). No assumption of pre-normalized embeddings.**

#### 1.2.2 Marginal gain (lines 32-72)
```ts
const computeMarginalGain = (
  candidateEmbedding, selectedEmbeddings, originalEmbedding, alpha = 0.3
): number => {
  if (selectedEmbeddings.length === 0) {
    return alpha * cosineSimilarity(originalEmbedding, candidateEmbedding);
  }
  let maxSimilarity = 0;
  for (const selectedEmbedding of selectedEmbeddings) {
    const similarity = cosineSimilarity(candidateEmbedding, selectedEmbedding);
    maxSimilarity = Math.max(maxSimilarity, similarity);
  }
  const relevance = alpha * cosineSimilarity(originalEmbedding, candidateEmbedding);
  const diversity = 1 - maxSimilarity;
  return relevance + diversity;
};
```

**Note**: `gain = α·cos(c, orig) + (1-α)·(1 - max_sim(c, selected))` is the formula. When `selected` is empty, only the relevance term is used (no diversity penalty). **The `(1-α)` multiplier is implicit** — it's `diversity = 1 - maxSim`, and the final term is `relevance + diversity`, so the diversity weight is `1.0`, not `(1-α)`. This is a **subtle discrepancy** with task13.md:306-311 which explicitly multiplies `(1.0 - self.alpha) * diversity`.

Wait — re-reading FastGPT line 71: `return relevance + diversity;` — the diversity term is added at full weight, not `(1-α)`. This means FastGPT's gain is effectively:
```
α·cos(c, orig) + 1·(1 - max_sim(c, selected))
```
while task13.md's `lazy_greedy.py:306-311` does:
```
α·cos(c, orig) + (1-α)·(1 - max_sim(c, selected))
```

**This is a real formula-level mismatch.** See gap **G-P0-2**.

#### 1.2.3 Lazy re-eval semantics (lines 126-153)
```ts
for (let iteration = 0; iteration < k; iteration++) {
  if (pq.isEmpty()) break;
  let bestCandidate;
  while (!pq.isEmpty()) {
    const candidate = pq.dequeue()!;
    const currentGain = computeMarginalGain(
      candidateEmbeddings[candidate.index], selectedEmbeddings, originalEmbedding, alpha
    );
    if (currentGain >= candidate.gain) {
      bestCandidate = {index: candidate.index, gain: currentGain};
      break;
    } else {
      pq.enqueue({index: candidate.index, gain: currentGain}, currentGain);
    }
  }
  if (bestCandidate) {
    selected.push(normalizedCandidates[bestCandidate.index]);
    selectedEmbeddings.push(candidateEmbeddings[bestCandidate.index]);
  }
}
```
**Matches task13.md:337-353** exactly (recompute gain vs own old gain, accept if `currentGain >= old.gain`).

#### 1.2.4 PriorityQueue implementation (lines 9-28)
```ts
class PriorityQueue<T> {
  private heap: Array<{item: T; priority: number}> = [];
  enqueue(item: T, priority: number): void {
    this.heap.push({item, priority});
    this.heap.sort((a, b) => b.priority - a.priority);  // resort on every enqueue — O(n log n) per op
  }
  dequeue(): T | undefined { return this.heap.shift()?.item; }
  ...
}
```
**Note**: FastGPT's "PriorityQueue" is a sort-on-insert array, **not a binary heap**. task13.md uses `heapq` (a real binary heap). This is a *performance* difference, not a *correctness* difference. task13.md is more efficient.

#### 1.2.5 Empty / k=0 short-circuit (lines 88-93)
```ts
if (!query || normalizedCandidates.length === 0 || k <= 0) {
  return {selectedData: [], embeddingTokens: 0};
}
```
task13.md:318-319: `if len(candidates) <= self.k: return list(candidates)`. **Different thresholds**: task13 short-circuits at `len <= k` (returns all candidates), FastGPT short-circuits at `k <= 0` or empty input (returns empty). The semantic intent is the same: when there are too few candidates to do meaningful greedy selection, FastGPT returns *empty* (the caller is expected to handle that), while task13 returns *all candidates* (treating it as a no-op pass-through).

### 1.3 Image Caption

**File:** `packages/service/core/dataset/search/defaultRecall/imageCaption.ts`

#### 1.3.1 Function signature and gate (lines 33-50)
```ts
export const getImageCaptionQueries = async ({
  vlmModel, imageQueries, userKey
}): Promise<ImageCaptionQueries> => {
  if (!vlmModel || imageQueries.length === 0) {
    return emptyImageCaptionQueries();
  }
  const vlmModelData = getLLMModel(vlmModel);
  if (!vlmModelData?.vision) {
    return emptyImageCaptionQueries();
  }
  ...
};
```
**Two gates**: (1) `vlmModel` is set, (2) the model has `vision: true` capability.

#### 1.3.2 Per-image processing (lines 51-111)
```ts
const results = await Promise.all(
  imageQueries.map(async (url, index) => {
    try {
      const llmStartTime = Date.now();
      const {answerText, requestId, usage: {inputTokens, outputTokens, usedUserOpenAIKey}} =
        await createLLMResponse({
          userKey,
          body: {
            model: vlmModelData.model,
            temperature: 0.1,
            stream: true,
            useVision: true,   // FastGPT-specific flag
            messages: [{
              role: 'user',
              content: [
                {type: 'image_url', image_url: {url: await normalizeImageToBase64(url)}},
                {type: 'text', text: '请用一句话描述这张图片的主体、场景、颜色、文字和关键视觉特征。只输出描述，不要解释。'}
              ]
            }] as any
          }
        });
      return {query: answerText.trim(), requestId, inputTokens, outputTokens, seconds: ..., usedUserOpenAIKey};
    } catch (error) {
      logger.warn('Image caption generation failed during dataset search', {
        model: vlmModelData.model, imageIndex: index, error
      });
      return {query: '', requestId: '', inputTokens: 0, outputTokens: 0, seconds: 0, usedUserOpenAIKey: false};
    }
  })
);
```

**Key points**:
- `Promise.all` → all images processed **in parallel**
- `useVision: true` is a FastGPT-internal flag for the LLM proxy
- **Prompt is hardcoded**, not configurable
- **Per-image error isolation**: a bad URL doesn't fail the batch; it returns empty `query`
- The bad image is **not** removed from `imageQueries` — the original image still goes through image-vector recall (`defaultRecall/index.ts:96-106`)
- `imageQueries.map((url, index) => ...)` — `index` is for logging only

#### 1.3.3 Result aggregation (lines 112-124)
```ts
const validResults = results.filter((item) => item.query);
const billableResults = results.filter((item) => item.inputTokens > 0 || item.outputTokens > 0);

return {
  model: vlmModelData.model,
  queries: validResults.map((item) => item.query),
  requestIds: results.map((item) => item.requestId).filter(Boolean),
  inputTokens: results.reduce((sum, item) => sum + item.inputTokens, 0),
  outputTokens: results.reduce((sum, item) => sum + item.outputTokens, 0),
  seconds: results.reduce((sum, item) => sum + item.seconds, 0),
  usedUserOpenAIKey:
    billableResults.length > 0 && billableResults.every((item) => item.usedUserOpenAIKey)
};
```
**Tokens are summed across all images, not per-image**. Failure-flag tracking distinguishes "billable" results (any with non-zero tokens) for the `usedUserOpenAIKey` rollup.

#### 1.3.4 Image base64 normalization (`utils.ts:50-61`)
```ts
export const normalizeImageToBase64 = async (imageUrl: string) => {
  if (imageUrl.startsWith('data:image/')) return imageUrl;  // pass-through
  if (!serviceEnv.MULTIPLE_DATA_TO_BASE64) return imageUrl;  // skip conversion
  const {completeBase64} = await getImageBase64(imageUrl);
  return completeBase64;
};
```
**Conditional conversion**: only when `MULTIPLE_DATA_TO_BASE64` env flag is true. data: URLs always pass through. Otherwise the URL is used as-is by the LLM provider.

#### 1.3.5 Image caption usage in search flow (`defaultRecall/index.ts:64-68, 96-106`)
- `imageCaptionQueries` (text captions) → passed to `multiQueryRecall` as a separate query group
- `imageQueries` (original URLs) → passed to `embeddingRecall` for image-vector recall (gated on `vlmModelData.vision`)
- Both results are fused in Steps 3-5 of `searchDatasetData` (`defaultRecall/index.ts:111-118, 137-146`)

### 1.4 Query Decomposition: Does Not Exist in FastGPT

**Grep results across all `packages/`:**
```
$ grep -rln "decomposition\|decompose\|sub_query\|subQuery\|multi_step\|multi-hop" packages/
(no results)
$ grep -rln "complex.*query\|isComplex\|needDecompose" packages/
(no results)
```

**The only decomposition-like code in the entire FastGPT repo is HTML tag decomposition** in chunker code:
```
$ grep -rn "tag.decompose\(\)" packages/global/common/string/html/...
```
(Used for HTML structure parsing, not query decomposition.)

**Conclusion**: FastGPT has no query decomposition feature. The spec invents it as a new capability. task13.md's `QueryDecomposer` is **a spec-internal invention**, not a port. This is **not necessarily wrong** (the spec is allowed to add features), but the audit must flag it: there is no FastGPT reference for alignment.

---

## 2. rag-pipeline 当前状态

### 2.1 Path check

```
$ find /Users/jung/pro/rag-pipeline/src -name "query_ext*" -o -name "image_caption*" \
                                       -o -name "decomposition*" -o -name "lazy_greedy*"
(no results)

$ ls /Users/jung/pro/rag-pipeline/src/rag/pipeline/
ls: cannot access ... : No such file or directory

$ find /Users/jung/pro/rag-pipeline/tests -name "test_query*" -o -name "test_decompos*" \
                                          -o -name "test_image*" -o -name "test_lazy*"
(no results)
```

**All four target files and all three target test files are absent.** task 13 is a spec-only document at this point. The plan's main index marks it as "OK" in the task list (`2026-06-10-python-rag-pipeline.md:202`), but that's an aspirational state, not a current state.

### 2.2 Current `src/rag/` layout (only files relevant to task 13)

```
src/rag/
├── __init__.py
├── config.py                          # openai_model: str = "MiniMax-M3"
├── domain/
│   ├── search.py                      # SearchRequest.context.query_extension / .max_query_variants / .query_decomposition
│   │                                  # SearchRequest.history.chat_bg / .histories  ← task13 B9 修正 字段已存在
│   └── document.py                    # ScoredDocument (no M3/vision-specific fields)
├── infra/llm/chat.py                  # get_chat_model, get_structured_chat_model
│                                      # ⚠ NO get_m3_chat_model — task13.md:746 引用不存在
└── retrieval/
    ├── __init__.py                    # exports trace only
    └── trace.py                       # RetrievalTrace, remove_duplicates
```

**No query extension, no image caption, no decomposition, no lazy greedy.**

### 2.3 `SearchRequest` shape (already has the fields task13 needs)

`src/rag/domain/search.py:51-68`:
```python
class SearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)
    query: str
    dataset_ids: list[uuid.UUID]
    image_urls: list[str] = []
    use_global_rerank: bool = False
    audit: bool = False
    retrieval: RetrievalConfig = RetrievalConfig()
    generation: GenerationConfig = GenerationConfig()
    context: ContextConfig = ContextConfig()       # ← query_extension=True, max_query_variants=3, query_decomposition=False
    history: HistoryConfig = HistoryConfig()       # ← chat_bg="", histories=[]
```

`ContextConfig` (line 31-39):
```python
class ContextConfig(BaseModel):
    parent_doc_window: int = 0
    query_extension: bool = True
    max_query_variants: int = 3
    query_decomposition: bool = False
```

`HistoryConfig` (line 42-48):
```python
class HistoryConfig(BaseModel):
    chat_bg: str = ""
    histories: list[dict[str, str]] = []
```

**Good**: All input fields task13.md references already exist on the domain model. The B9 修正 (decompose takes chat_bg + histories) and the runtime input shape are consistent with the domain.

### 2.4 LLM factory current state

`src/rag/infra/llm/chat.py:23-54`:
```python
def get_chat_model(
    model: str | None = None,
    temperature: float = 0.1,           # ← default 0.1, matches FastGPT queryExtension.ts:183
    timeout: float = _LLM_TIMEOUT_SECONDS,
    max_retries: int = 0,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    resolved_model = model or settings.openai_model
    if _is_reasoning_model(resolved_model):
        return ChatOpenAI(..., extra_body={"reasoning_split": True})
    return ChatOpenAI(...)
```

**Exports**:
- `get_chat_model(...) -> ChatOpenAI`
- `get_structured_chat_model(schema, ...) -> Runnable` (with `.with_structured_output(schema, method="function_calling")`)

**Missing**: `get_m3_chat_model` (task13.md:746 imports it). The model name "MiniMax-M3" exists in config.py:37 as the default `openai_model`, but there's no factory function for it specifically. M3 likely refers to the MiniMax M3 chat model family, but the factory wrapper is not implemented.

**Impact**: `image_caption.py` will fail at module load with `ImportError: cannot import name 'get_m3_chat_model' from 'rag.infra.llm.chat'`.

### 2.5 Image caption prompt divergence (not yet implemented)

task13.md:771: `{"type": "text", "text": "用中文详细描述这张图片"}` — vague, "describe in detail"
FastGPT `imageCaption.ts:78`: `"请用一句话描述这张图片的主体、场景、颜色、文字和关键视觉特征。只输出描述，不要解释。"` — structured one-sentence prompt with 5 explicit fields

**When task 13 is implemented, the rag-pipeline will produce more verbose, less structured captions than FastGPT.** This will affect downstream recall (image-captioned text feeds into the fulltext/embedding recall) but is a *quality* concern, not a *correctness* blocker.

### 2.6 `with_structured_output` usage

task13.md:617-620:
```python
self._structured_llm = (
    llm.with_structured_output(QueryVariants, method="function_calling")
    if llm else None
)
```

`get_structured_chat_model` (chat.py:57-75) **already wraps this**:
```python
return base.with_structured_output(schema, method="function_calling")
```

**task13.md's Stage-1 `with_structured_output` approach is reasonable, but FastGPT doesn't use it** — it relies on the raw text → `json5.parse` slice approach. The two are equivalent in success, but structured output gives a typed return (`QueryVariants.variants: list[str]`) while raw parsing gives an untyped list. The fallback chain in task13.md:691-707 (try structured, fall back to raw + json5) is **strictly more robust** than FastGPT. **Acceptable improvement.**

### 2.7 Embedding model for Stage 2

task13.md:622-626:
```python
self._selector = (
    LazyGreedySelector(embed_model, alpha=alpha, k=max_variants)
    if embed_model else None
)
```

rag-pipeline has `src/rag/infra/llm/embed.py` (per `find` output), but it was not inspected in this audit. **The wiring path through task 16 (`build_full_pipeline`) must inject the embed model here.** Out of scope for this audit, but flag for the integration audit.

---

## 3. task13.md 关键声明清单

| # | Claim (file:line) | Concrete content |
|---|---|---|
| C-1 | task13.md:24-31 | 4 files to create: `pipeline/query_ext.py`, `pipeline/image_caption.py`, `retrieval/decomposition.py`, `retrieval/lazy_greedy.py`; 3 test files. **None exist.** |
| C-2 | task13.md:3 | Cross-reference `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` lines **2677-3132** — **this range is wrong**: the plan file is only 506 lines total. The actual content is in the spec file (`2026-06-10-python-rag-pipeline-design.md:755-779` for decomposition, `43-52` for the module summary, `300-317` for the plan tree). |
| C-3 | task13.md:11 | (B9) `QueryDecomposer.decompose()` accepts `chat_bg` + `histories`; `decompose_state` uses `state["sub_queries"]` field. **Domain has `HistoryConfig` with both fields, so the input shape is wired.** |
| C-4 | task13.md:13-19 | Multiple audit fixes: 5 missing rules in system prompt (subagent #1), 3 output hard constraints, Stage-1 json5 parse tolerance, `histories` token-aware truncation (subagent #3), few-shot section name `历史记录` consistency, `_is_simple` removal, `is_complex` field removal, `min_length=2` on sub_queries, `Field(description=...)`. |
| C-5 | task13.md:457-515 | `QUERY_EXTENSION_SYSTEM_PROMPT` — 8 rules, 3 output requirements, 4 few-shot examples. **Byte-identical to FastGPT** (modulo Chinese punctuation). |
| C-6 | task13.md:520-535 | `_build_user_prompt` puts `chat_bg` / histories / query in user prompt, keeps system prompt clean. **Matches FastGPT pattern.** |
| C-7 | task13.md:540-576 | `_filter_histories_by_max_context` — simplified re-implementation of `filterGPTMessageByMaxContext`. Uses char/2 token estimate, no system/checkpoint preservation. **Approximate, not equivalent.** |
| C-8 | task13.md:595-737 | `QueryExtensionRunnable` — Stage 1 (LLM rewrite) + Stage 2 (submodular selection). Two-tier fallback: structured output → raw + json5. |
| C-9 | task13.md:683 | Gate: `if not input.get("query_extension", True): return {..., "query_variants": [input["query"]]}`. **Domain flag-gated, FastGPT config-gated.** |
| C-10 | task13.md:168-181 | `DecomposedQueries` Pydantic: `sub_queries: list[str] = Field(..., min_length=2, max_length=8)`. **Clean spec-internal schema, no FastGPT reference.** |
| C-11 | task13.md:192-198 | `DECOMPOSE_PROMPT` instructs LLM to return at least 2 sub-queries; for simple queries, return `[query, equivalent paraphrase]`. **No FastGPT equivalent.** |
| C-12 | task13.md:259-268 | `_cos()` explicit L2 norm + zero-vector short-circuit. **Matches FastGPT `useTextCosine.ts:39-56`.** |
| C-13 | task13.md:271-353 | `LazyGreedySelector` with `_PQItem` and lazy re-eval. **Matches FastGPT `useTextCosine.ts:75-160`** (recompute gain vs own old gain, accept if `currentGain >= old.gain`). |
| C-14 | task13.md:294 | `alpha: float = 0.3, k: int = 3`. **Matches FastGPT call site defaults.** |
| C-15 | task13.md:306-311 | `gain = α·cos(c, orig) + (1-α)·(1 - max_sim)` — **uses `(1-α)` multiplier on diversity term**. FastGPT (`useTextCosine.ts:58-72`) uses **`1·(1-maxSim)`** — diversity weight is implicitly 1.0. **Real formula divergence.** See G-P0-2. |
| C-16 | task13.md:318-319 | `if len(candidates) <= self.k: return list(candidates)` short-circuit. **Different from FastGPT's `k <= 0 || empty input` threshold.** |
| C-17 | task13.md:748-789 | `ImageCaptionRunnable` uses raw `httpx` + base64 + vision message. **Different from FastGPT `normalizeImageToBase64` + `MULTIPLE_DATA_TO_BASE64` env gate.** |
| C-18 | task13.md:746 | `from rag.infra.llm.chat import get_m3_chat_model` — **`get_m3_chat_model` does not exist in `chat.py`.** |
| C-19 | task13.md:771 | Caption prompt: `"用中文详细描述这张图片"`. **FastGPT: structured one-sentence prompt with 5 fields.** |
| C-20 | task13.md:778 | `.catch (Exception: continue)` — silent skip on bad image. **FastGPT: log warning + return empty caption, but the original image still flows through image-vector recall.** |
| C-21 | task13.md:892-894 | `temperature=0.1` is sourced from Task 7's `get_m3_chat_model` / `get_openai_chat_model`. **Task 7 not yet audited; current `get_chat_model` does have `temperature=0.1` default, so the value is right; the factory function name is wrong.** |
| C-22 | task13.md:897-901 | Task 13 doesn't directly call Redis; `warnings: list[str] = []` is on Task 2's `SearchRequest` / `SearchResponse` (verified — `SearchResult.warnings: list[str] = []` at `domain/search.py:89`). |

---

## 4. 三向差异矩阵

| Aspect | task13.md says | rag-pipeline has | FastGPT does |
|---|---|---|---|
| **Existence of target files** | 4 source + 3 test files to create | **0 exist** | `queryExtension.ts`, `useTextCosine.ts`, `imageCaption.ts` exist (canonical) |
| **Query extension function name** | `QueryExtensionRunnable.ainvoke` | (none) | `queryExtension({chatBg, query, histories, llmModel, embeddingModel, userKey, generateCount=10})` |
| **Query extension system prompt** | 8 rules + 3 output reqs + 4 few-shot | (none) | 8 rules + 3 output reqs + 4 few-shot (`queryExtension.ts:18-75`) — **identical modulo Chinese punctuation** |
| **User prompt structure** | system clean, user has chat_bg / histories / query | (none) | same: system + user 2-message structure |
| **Stage-1 json5 parse tolerance** | `answer.indexOf('[') / lastIndexOf(']')` slice + `\\n`/`\\`/double-space replace + json5 fallback | (none) | `queryExtension.ts:204-232` — **identical algorithm** |
| **`with_structured_output` use** | Used as primary; raw LLM as fallback | (none — not used) | Not used (raw text only) |
| **`generateCount` default** | `max_candidates=10` (Stage 1 hint) | (none) | `generateCount=10` (`queryExtension.ts:115`) |
| **Stage-2 cap** | `max_variants=3` | (none) | `k: Math.min(3, queries.length)` (`queryExtension.ts:272`) |
| **`alpha` default** | `0.3` | (none) | `0.3` (`useTextCosine.ts:79`, `queryExtension.ts:273`) |
| **Lazy re-eval semantics** | recompute gain vs own old gain; accept if `currentGain >= old` | (none) | identical (`useTextCosine.ts:132-148`) |
| **Diversity weight in gain formula** | `(1-α)·(1-maxSim)` | (none) | **`1·(1-maxSim)`** (no `1-α` multiplier — see `useTextCosine.ts:71`) — **divergence** |
| **`cos()` zero-vector handling** | explicit short-circuit | (none) | explicit short-circuit (`useTextCosine.ts:54`) |
| **PriorityQueue impl** | `heapq` (binary heap) | (none) | sort-on-insert array (O(n log n) per op, `useTextCosine.ts:11-16`) |
| **k=0 / empty short-circuit** | `len(candidates) <= k → return all` | (none) | `k <= 0 || empty → return []` |
| **`temperature` for Stage-1 LLM** | `0.1` (sourced from Task 7 factory default) | `get_chat_model` default is `0.1` ✓ | `0.1` hardcoded in `createLLMResponse` body (`queryExtension.ts:183`) |
| **Histories truncation** | char/2 token estimate, no system-preserve | (none) | tiktoken `countGptMessagesTokens`, system/developer preserved, context checkpoints preserved |
| **Histories section name in user prompt** | `历史记录` | (none) | `历史记录` (`queryExtension.ts:96`) |
| **Trigger gate for query extension** | `if not input.get("query_extension", True)` flag | `SearchRequest.context.query_extension=True` (default) | config-gated: `if (!llmModel || !embeddingModel) return;` |
| **Extension result: dedup** | not explicit in task13; relies on Stage-2 selection | (none) | `filterSameQuery` strips punctuation/whitespace before hash (`utils.ts:88-102`) |
| **`extensionQueries` final format** | `query_variants: list[str]` (output dict) | (none) | `{rawQuery, extensionQueries, llmModel, embeddingModel, requestId, seconds, inputTokens, outputTokens, usedUserOpenAIKey, embeddingTokens}` |
| **Image caption: function name** | `ImageCaptionRunnable.ainvoke` | (none) | `getImageCaptionQueries({vlmModel, imageQueries, userKey})` |
| **Image caption: prompt** | `"用中文详细描述这张图片"` | (none) | `"请用一句话描述这张图片的主体、场景、颜色、文字和关键视觉特征。只输出描述，不要解释。"` |
| **Image caption: parallelism** | sequential `for url in image_urls: await ...` (line 762) | (none) | `Promise.all(imageQueries.map(...))` (parallel) |
| **Image caption: failure mode** | `.catch (Exception: continue)` — silent skip, image dropped | (none) | log warn + return empty `query`, but original image still flows to image-vector recall |
| **Image caption: vision capability gate** | not checked | (none) | `vlmModelData?.vision` must be true (`imageCaption.ts:47-49`) |
| **Image base64 normalization** | raw `httpx` download + b64-encode (line 765) | (none) | `normalizeImageToBase64` (`utils.ts:50-61`): pass-through for data: URLs, conditional on `serviceEnv.MULTIPLE_DATA_TO_BASE64` |
| **`useVision: true` flag** | not in task13 (default OpenAI vision format) | (none) | `useVision: true` body flag (`imageCaption.ts:64`) — FastGPT-internal |
| **Image caption: token rollup** | per-image (not summed explicitly; just appended to `captions`) | (none) | summed across all images (`imageCaption.ts:119-121`) |
| **Image caption integration** | returns `{**input, "caption_queries": captions}` — caller merges | (none) | passes `imageCaptionQueries` as separate query group to `multiQueryRecall` (`defaultRecall/index.ts:64-68, 96-106`) |
| **Image caption: empty result on no vlmModel** | `if not image_urls: return input` | (none) | `if (!vlmModel || imageQueries.length === 0) return empty` |
| **Decomposition: feature exists in FastGPT?** | Yes (per task13.md design) | (no implementation) | **No.** Grep returns 0 hits across all packages. |
| **`DecomposedQueries` schema** | `sub_queries: list[str]`, min_length=2, max_length=8 | (none) | N/A — no FastGPT equivalent |
| **Decomposition prompt** | Chinese 3-paragraph prompt with examples (lines 192-198) | (none) | N/A — no FastGPT equivalent |
| **Decomposition trigger** | `if self._structured_llm is None: return [query]` (no LLM) | (none) | N/A |
| **Decomposition failure mode** | `try/except → return [query]` | (none) | N/A |
| **Decomposition: `_is_simple` heuristic** | **removed** by audit #4 (line 18 fix) | (none — spec had it at line 771-773) | N/A |
| **Decomposition: `is_complex` field** | **removed** by audit #4 (line 18 fix) | (none) | N/A |
| **Decomposition accepts chat_bg / histories** | yes (B9 修正, line 207-212) | (none — domain has `HistoryConfig`) | N/A |
| **Factory import for chat model** | `get_m3_chat_model` from `rag.infra.llm.chat` (line 746) | **`get_m3_chat_model` does not exist** in `chat.py` | N/A |
| **`SearchRequest.context` defaults** | `query_extension=True, max_query_variants=3, query_decomposition=False` (line 37-39) | matches | N/A |
| **`SearchRequest.history` shape** | `chat_bg: str, histories: list[dict]` (line 47-48) | matches | N/A |

---

## 5. 修复建议 (P0 → P1 → P2 → P3, 每条带具体文件:行号)

### P0 (blocker before sign-off)

#### G-P0-1: `get_m3_chat_model` is a phantom import (broken at module load)
**Where:** `task13.md:746` `from rag.infra.llm.chat import get_m3_chat_model`
**Problem:** The symbol `get_m3_chat_model` does not exist in `/Users/jung/pro/rag-pipeline/src/rag/infra/llm/chat.py`. The file exports only `get_chat_model` and `get_structured_chat_model`. The `MiniMax-M3` string appears in `config.py:37` as the default `openai_model`, but no factory function is named after it.
**Why P0:** `image_caption.py` will fail to import. This cascades: task 16's `build_full_pipeline` instantiates `ImageCaptionRunnable(chat_model=...)` at graph-build time, so the entire pipeline import fails. The very first test run produces an ImportError on a missing name, not on a logic bug.
**Fix options:**
- **Option A (minimal):** Replace `from rag.infra.llm.chat import get_m3_chat_model` with `from rag.infra.llm.chat import get_chat_model` and call `get_chat_model(model="MiniMax-M3")` (or whatever the actual vision-capable model is). Drop the `self.chat_model = chat_model or get_m3_chat_model()` constructor default.
- **Option B (factory by name):** Add `get_m3_chat_model(temperature=0.1, **kwargs) -> ChatOpenAI` to `chat.py` as a thin wrapper over `get_chat_model` with a fixed model name. This requires deciding what "M3" is in the rag-pipeline context (the M3 chat model from the spec, the local model name, or a placeholder).
- **Recommend:** Option A. Don't introduce a factory whose only purpose is to wrap a default value — that's a YAGNI trap. The `model` parameter is already plumbed through `get_chat_model(model=...)`.
- **Test impact:** None directly, but the module-import test (RED phase in task13.md:155) will currently pass with the wrong error (ImportError on a missing function, not a logic assertion failure). The audit #1 P1-1 stub discipline is broken.

#### G-P0-2: Lazy-greedy diversity term is weighted by `(1-α)` in task13 but unweighted (×1) in FastGPT
**Where:** `task13.md:306-311` `LazyGreedySelector._compute_marginal_gain`
```python
relevance = self.alpha * _cos(candidate, orig_vec)
if not selected_vecs: return relevance
max_sim = max(_cos(candidate, sv) for sv in selected_vecs)
diversity = 1.0 - max_sim
return relevance + (1.0 - self.alpha) * diversity
```
**Problem:** FastGPT `useTextCosine.ts:68-71`:
```ts
const relevance = alpha * cosineSimilarity(originalEmbedding, candidateEmbedding);
const diversity = 1 - maxSimilarity;
return relevance + diversity;  // <-- NO (1-alpha) multiplier
```
The diversity term in FastGPT is weighted at 1.0, not `(1-α)`. This is a real arithmetic difference. At `α=0.3`, task13's gain for a selected vector's second-round update is `0.3·cos + 0.7·(1-maxSim)`; FastGPT's is `0.3·cos + 1.0·(1-maxSim)`. The difference grows as candidates are added (maxSim increases, so `(1-maxSim)` shrinks, but in task13 it shrinks 30% faster).
**Why P0:** If the task11 fusion semantics audit (the previous pilot) found that RRF sum-vs-max was a "type-level algorithmic gap", this is in the same category. The cosine selection algorithm is the second-most important number in the pipeline after the RRF `k=60` constant. A wrong coefficient changes the selected query set, which changes the recall results.
**Fix:** Either:
- **Option A (match FastGPT):** Drop the `(1.0 - self.alpha)` multiplier. Use `return relevance + diversity`.
- **Option B (justify the divergence):** Add a comment explaining that the `(1-α)` is intentional and cite the original jina-ai submodular-optimization paper (`useTextCosine.ts:3` reference). Note that FastGPT's implementation is itself a simplification.
- **Recommend:** Option A for parity. Option B requires citing the jina-ai reference and adding a "task13 intentional divergence" note in the design doc.
- **Test impact:** `test_selector_preserves_original_similar` (task13.md:387-395) with `α=0.9` would behave differently — FastGPT at α=0.9 would have gain `0.9·cos + 1.0·(1-maxSim)`, task13 would have `0.9·cos + 0.1·(1-maxSim)`. The "selects similar" assertion may still hold because `cos` dominates at α=0.9, but the *relative ordering* of candidates will differ.

#### G-P0-3: Image caption does not gate on vision capability
**Where:** `task13.md:748-789` `ImageCaptionRunnable`
**Problem:** The Runnable is constructed unconditionally with `self.chat_model = chat_model or get_m3_chat_model()`. There's no check that the underlying model supports vision. FastGPT's `getImageCaptionQueries` (line 47-49) checks `vlmModelData?.vision` and returns empty if not vision-capable. task13 sends an image to a text-only model → the LLM provider returns a 4xx or silently truncates the image.
**Why P0:** If task 16's caller passes a non-vision model (e.g., `openai_model: "gpt-3.5-turbo-instruct"`), the request fails at LLM-call time with no recovery. FastGPT would silently skip the image and return `imageCaptionQueries: []`. The behavior divergence is **silent at the FastGPT side, loud at the task13 side**.
**Fix:** Add a `vision_capable: bool = True` constructor parameter. The caller (task 16) is responsible for passing the right model. Alternatively, take the model name as a constructor arg and look up its vision capability (would require a model registry that doesn't yet exist in rag-pipeline).
**Recommended:** Make `chat_model` a required positional arg in the constructor. The caller must pass a known-vision model. Document the assumption in the class docstring.

#### G-P0-4: Image caption processes images sequentially (line 762 `for url in image_urls: await ...`)
**Where:** `task13.md:761-779` loop body
```python
for url in image_urls:
    try:
        ...
        result = await self.chat_model.ainvoke([msg])
        ...
    except Exception:
        continue
```
**Problem:** FastGPT uses `Promise.all(imageQueries.map(...))` for parallel processing. task13's `for` loop with sequential `await` serializes the LLM calls. For 5 images, task13 takes 5× the latency of FastGPT. The `httpx.AsyncClient()` is also created **per image** with no `aclose()` (resource leak: TCP connection pool never closed).
**Why P0:** Latency is a user-facing metric; a 5× slowdown on image-bearing queries is a regression. The `httpx.AsyncClient` leak will cause file-descriptor exhaustion on long-running processes that handle many requests.
**Fix:**
- Replace the `for` loop with `await asyncio.gather(*[self._caption_one(url) for url in image_urls], return_exceptions=True)`.
- Move `httpx.AsyncClient()` to module-level (lazy singleton) or pass it in via the constructor; call `aclose()` on shutdown.
- Move the per-image logic into an inner `async def _caption_one(self, url: str) -> str` method that returns `""` on failure (so `gather` can collect results into a `list[str | BaseException]`).
- **Test impact:** `test_query_ext_passes_chat_bg_and_histories` (line 849-874) is for `QueryExtensionRunnable`; image caption has no dedicated test in task13.md's spec. Add a new test that asserts `ImageCaptionRunnable.ainvoke` with 2 URLs calls the chat model twice in parallel (use a list-of-call-timestamps mock).

### P1 (significant API/type mismatch)

#### G-P1-1: Image caption prompt produces different output from FastGPT
**Where:** `task13.md:771` `{"type": "text", "text": "用中文详细描述这张图片"}`
**Problem:** FastGPT `imageCaption.ts:78` uses `"请用一句话描述这张图片的主体、场景、颜色、文字和关键视觉特征。只输出描述，不要解释。"` — a structured one-sentence prompt that asks for 5 specific fields (主体/场景/颜色/文字/关键视觉特征). task13's prompt is vague ("describe in detail in Chinese"). Output is:
- **Length:** task13 → multi-sentence, possibly very long; FastGPT → 1 sentence, bounded.
- **Structure:** task13 → free-form prose; FastGPT → field-prefixed prose.
- **Embedding stability:** FastGPT's bounded length + structured fields yields more consistent embeddings across runs of the same image. task13's free-form prompt yields more variance.
- **Downstream fulltext impact:** the caption is fed into `multiQueryRecall` as a text query (or merged with text queries in task13's design). Long, variable captions produce unstable fulltext matches.
**Why P1:** The recall quality difference is real but **not directly testable in unit tests** without a real VLM. Will be caught only in eval (task 18, not yet audited). Flag for the eval audit.
**Fix:** Copy FastGPT's prompt verbatim. The 5-field structure produces tighter, more consistent captions.

#### G-P1-2: Image caption failure silently drops the image from the search path
**Where:** `task13.md:778-779` `except Exception: continue`
**Problem:** FastGPT `imageCaption.ts:95-108` catches the error, logs a warning, and returns `query: ''` — but the *original image URL* is **not** removed from `imageQueries`. It still flows through `multiQueryRecall` to the image-vector recall path (which gates on `vlmModelData.vision`). task13's `continue` skips the failed image entirely. If the LLM is the only image-recall path configured (no image-vector model), the bad image produces zero recall.
**Why P1:** The user uploaded an image; if captioning fails, they probably still want *some* signal from that image. FastGPT preserves that signal. task13 loses it.
**Fix:** When `caption_queries[i]` is empty (per-image failure), still include the original image URL in a separate field, e.g. `failed_image_urls: list[str]`. task 16's integration can decide whether to use these for image-vector recall or not.

#### G-P1-3: Image base64 normalization is unconditional and ignores data: URLs
**Where:** `task13.md:764-772`:
```python
import httpx
resp = await httpx.AsyncClient().get(url)
resp.raise_for_status()
b64 = base64.b64encode(resp.content).decode()
msg = {
    "role": "user",
    "content": [
        {"type": "text", "text": "用中文详细描述这张图片"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
    ],
}
```
**Problem:**
- `httpx.AsyncClient().get(url)` fails on data: URLs (they're not URLs).
- Doesn't check `url.startswith('data:image/')` before HTTP-fetching.
- Hardcodes MIME as `image/jpeg` — incorrect for PNG/WebP/GIF uploads.
- FastGPT's `normalizeImageToBase64` (`utils.ts:50-61`) handles all three cases (data: pass-through, env-gated conversion, S3 key handling).
**Why P1:** This breaks the `data:` URL path that FastGPT explicitly supports. If a user pastes a data: URL (common in browser-based uploads), task13's image caption raises an `httpx` exception. The exception is caught by `except Exception: continue`, so the failure is **silent** — the image just disappears from the search.
**Fix:** Add a `if url.startswith("data:image/"): b64 = url.split(",", 1)[1]` short-circuit. Then `import magic` or use the HTTP `Content-Type` header to determine the MIME type. Or accept that the data: path is out of scope and document it.

#### G-P1-4: `with_structured_output` mode may emit LLM-token overhead that FastGPT avoids
**Where:** `task13.md:617-620, 691-698` — `self._structured_llm = llm.with_structured_output(QueryVariants, method="function_calling")` as primary path
**Problem:** `with_structured_output(method="function_calling")` injects a tool/function schema into the LLM's system prompt. This consumes output tokens for the tool call wrapper and may shift the response distribution. FastGPT uses raw text + json5 parse, which is more direct. The two are not equivalent in:
- **Token cost:** function_calling adds ~100-200 tokens to the prompt per request.
- **Output format:** function_calling returns `{"variants": [...]}`; raw returns `["...", "..."]`. The task13 fallback path correctly handles both, but the primary path is strictly more expensive.
**Why P1:** This is a **deliberate design choice** with real cost implications. The task13 author chose structured output for type safety. Fine, but:
- The 3 hard output constraints (line 472-475 in the system prompt: "只输出 JSON 字符串数组") are redundant with structured output's function schema. The function schema already enforces the shape.
- The fallback to raw + json5 is good defensive programming but masks the real failure mode: if the LLM doesn't support function calling, the structured path fails and the user gets a json5 parse of a free-form response.
**Fix:** Document the tradeoff in the class docstring: "Primary path uses function_calling for typed output; ~150 token overhead per request. Fallback path uses raw text + json5 for compatibility with non-function-calling models." Add a benchmark comparing both paths' token usage.

#### G-P1-5: `QueryDecomposer` is a spec invention, not a port
**Where:** `task13.md:185-235` entire `decomposition.py`
**Problem:** FastGPT has no query decomposition feature. The spec invented it. The implementation in task13.md is also new. There is no upstream reference for alignment. The prompts, schema, and behavior are spec-internal decisions.
**Why P1:** Reviewers may incorrectly assume the implementation is "ported from FastGPT" and grant a pass on accuracy. The reality is that **every behavior is a design decision**:
- Why `min_length=2, max_length=8`? Spec internal.
- Why is the LLM prompt in Chinese? Spec internal.
- Why no `_is_simple` heuristic? Audit #4 cleanup, not a port decision.
- Why `temperature=0.1`? Inherited from `get_chat_model` default.
**Fix:** Add a clear docstring at the top of `decomposition.py`:
```python
"""
Query decomposition: spec-internal feature with no FastGPT equivalent.
Reference: docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md:755-779
This module decomposes a complex query into 2-8 sub-queries via LLM.
"""
```

#### G-P1-6: k=0 / empty short-circuit semantics diverge from FastGPT
**Where:** `task13.md:318-319` `if len(candidates) <= self.k: return list(candidates)` vs `useTextCosine.ts:88-93` `if !query || normalizedCandidates.length === 0 || k <= 0: return {selectedData: [], embeddingTokens: 0}`
**Problem:**
- task13 returns **all candidates** when `len(candidates) <= k`. This is a "no-op pass-through".
- FastGPT returns **empty** when `len(candidates) == 0` (and on `k <= 0`).
- The two are not equivalent when `len(candidates) == 1` and `k == 0`: task13 returns `[candidates[0]]`; FastGPT returns `[]`.
- The two are not equivalent when `len(candidates) == 0`: both return `[]` (task13's `list([]) = []`).
- The two diverge when `len(candidates) == 5, k == 5`: task13 returns all 5; FastGPT runs the loop 5 times and selects 5 (the selection might not include all candidates if some have negative diversity weight, but with `α >= 0` it always includes all). So same result.
- The two diverge when `len(candidates) == k > 0`: same result, but task13's fast-path skips the embedding call. **task13 is more efficient on this edge case.**
**Why P1:** The test `test_selector_returns_k_candidates` (task13.md:378-384) passes 5 candidates with `k=3` — neither path tests the `len <= k` case. The fast-path is undocumented and its semantics are unclear.
**Fix:** Add a test:
```python
async def test_selector_returns_all_when_candidates_below_k():
    sel = LazyGreedySelector(FakeEmbed(), alpha=0.3, k=5)
    result = await sel.select("query", ["a", "b", "c"])
    assert result == ["a", "b", "c"]
    # FakeEmbed.aembed_documents should NOT have been called
    assert not hasattr(sel.embed_model, 'call_count') or sel.embed_model.call_count == 0
```

#### G-P1-7: `with_structured_output` Pydantic schema uses snake_case `sub_queries`, but stage-2 message uses camelCase `query_variants`
**Where:** `task13.md:175-181` `sub_queries: list[str]` vs `task13.md:79, 449-453, 683-684` `query_variants: list[str]`
**Problem:** The decomposition output schema uses `sub_queries`. The query-extension output uses `query_variants`. The `state["sub_queries"]` field (B9 修正, task13.md:11) and the `query_variants` field (line 79) are the two different intermediate representations. Inconsistent naming makes task 16's state wiring harder to read.
**Why P1:** Pure naming consistency. Could be defended either way (snake_case is Pythonic, camelCase matches the JSON output key).
**Fix:** Pick one and stick to it. Recommend `sub_queries` (used in `state["sub_queries"]` per B9) and use `query_variants` only in the final output dict from `QueryExtensionRunnable.ainvoke` (line 726). Document the convention.

### P2 (doc-only / cleanup)

#### G-P2-1: System prompt has Chinese punctuation inconsistency
**Where:** `task13.md:457-477` (8 rules + output requirements)
**Problem:** FastGPT uses Chinese full-width punctuation (`。` `，` `？` `；`). task13's copy uses a mix:
- Line 459: `规则：` (full-width colon) — matches FastGPT
- Line 460: `只做检索词改写, 不回答问题, 不解释原因。` (half-width comma `,`, full-width period `。`) — FastGPT line 21 uses `，` (full-width comma)
- Line 461: `每个检索词都必须服务于原问题, 不能引入历史记录和原问题之外的新事实。` — same
- Line 462: `如果原问题存在指代、省略或上下文依赖, 必须把指代补全为明确对象。` — has both `、` (full-width enumeration comma) and `,` (half-width) in the same sentence
- Line 472: `1. 只输出 JSON 字符串数组, 例如 ["query 1","query 2"]。` — half-width comma after the colon
**Why P2:** Cosmetic but matters for LLM behavior — the model may interpret `,` as a list separator in a way that confuses downstream parsing. Low risk, high friction to fix later.
**Fix:** Replace all `,` in the system prompt with `，` and all `;` with `；` to match FastGPT byte-for-byte.

#### G-P2-2: `DECOMPOSE_PROMPT` at `task13.md:192-198` uses `判断是否需要拆解` — FastGPT has no reference
**Where:** `task13.md:192-198`
**Problem:** The prompt says "判断是否需要拆解为多个子查询" — i.e., the LLM is given the discretion to *not* decompose. Combined with the `min_length=2` constraint on `sub_queries`, the LLM cannot honor "don't decompose" — it must always return at least 2 sub-queries. The `is_complex` removal (audit #4) means the LLM has no way to signal "this is a simple query".
**Why P2:** The audit #4 fix is a strict improvement (no decoration field), but the prompt text is now misleading. The fallback at `task13.md:230-232` `if not result.sub_queries: return [query]` is dead code — the LLM cannot return 0 sub-queries.
**Fix:** Either:
- Change `min_length=2` to `min_length=1` and let the LLM return `[query]` for simple queries, OR
- Change the prompt to "always return at least 2 sub-queries (if the query is already simple, return `[query, equivalent paraphrase]`)" — and remove the dead-code fallback at line 230-232.

#### G-P2-3: Task13's `temperature=0.1` cross-reference is to a non-existent Task 7 factory
**Where:** `task13.md:892-894` "Task 13 的 `temperature=0.1` 由 Task 7 的 ChatOpenAI 实例化保证 (Task 7 在 `get_m3_chat_model` / `get_openai_chat_model` 工厂中硬编码 `temperature=0.1`)"
**Problem:** The current `get_chat_model` (chat.py:23-54) does hardcode `temperature=0.1` as a default. The cross-reference to "Task 7's `get_m3_chat_model` / `get_openai_chat_model` factories" is forward-looking and references task 7 (which is in the parallel audit queue). If task 7's factories don't have this default, the temperature drops to whatever the LLM provider's default is (often 1.0 for OpenAI-compatible APIs).
**Why P2:** Risk of silent regression. If task 7's factories don't hardcode `temperature=0.1`, task 13 inherits the wrong value.
**Fix:** In `QueryExtensionRunnable.__init__`, default the temperature explicitly: `temperature: float = 0.1`. This is independent of whatever the chat model factory does. Then the cross-reference becomes a "task 7 should also default to 0.1" advisory, not a load-bearing assumption.

#### G-P2-4: `QueryExtensionRunnable.invoke` uses `nest_asyncio` as a side-effect
**Where:** `task13.md:728-736`:
```python
def invoke(self, input, config=None):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        import nest_asyncio
        nest_asyncio.apply()  # ← side effect: monkey-patches the event loop
        return loop.run_until_complete(self.ainvoke(input))
    except RuntimeError:
        return asyncio.run(self.ainvoke(input))
```
**Problem:** `nest_asyncio.apply()` is a global monkey-patch of the asyncio event loop. It's a side effect that affects all other coroutines in the process. This is the kind of code that breaks pytest (which already runs an event loop) and Jupyter notebooks (which run their own event loop).
**Why P2:** `ainvoke` is the LangChain-canonical entry point. The `invoke` shim is only needed for sync callers. The `try / except` catches `RuntimeError` from `asyncio.get_running_loop()` but inside the `try` block, `nest_asyncio.apply()` is called **before** `run_until_complete`. The order is wrong — the apply should happen after detecting a running loop, not before.
**Fix:** Restructure:
```python
def invoke(self, input, config=None):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(self.ainvoke(input))
    # We're inside a running loop; this is a programming error in async code
    raise RuntimeError(
        "QueryExtensionRunnable.invoke called from within a running event loop. "
        "Use ainvoke() instead."
    )
```
The `nest_asyncio` workaround is not needed for production code and shouldn't ship.

#### G-P2-5: `task13.md:3` line-range citation is wrong
**Where:** `task13.md:3` → `docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` lines 2677-3132
**Problem:** The plan file is **506 lines total**. Lines 2677-3132 do not exist. The actual decomposition content is in the spec file (`2026-06-10-python-rag-pipeline-design.md:755-779`).
**Why P2:** Same issue as task 11's G-P0-3. Reviewer cannot reproduce the citation check.
**Fix:** Update task13.md:3 to cite the spec file ranges.

### P3 (nice-to-have)

#### G-P3-1: `QueryVariants.variants` field uses `min_length=1` but Stage-2 may produce 0
**Where:** `task13.md:447-453` `variants: list[str] = Field(..., min_length=1, max_length=10)`
**Problem:** The schema requires at least 1 variant. The fallback at task13.md:710-711 `if not candidates: return {..., "query_variants": [original]}` is the "1 variant" guarantee. But this is a `QueryVariants` Pydantic instance, not a `list[str]`. The output dict's `query_variants` is `list[str]`, which doesn't enforce `min_length=1`. Consistent naming but inconsistent type guarantees.
**Fix:** Add a docstring on `QueryExtensionRunnable.ainvoke`: "Always returns `query_variants: list[str]` with `len >= 1`. If LLM extension fails or produces no usable candidates, returns `[original]`."

#### G-P3-2: No `__all__` declaration in new modules
**Where:** `src/rag/pipeline/__init__.py` (to be created) and `src/rag/retrieval/__init__.py` (exists, line 3 has `__all__`)
**Problem:** Convention: each module exports its public surface via `__all__`. The current `retrieval/__init__.py` has `__all__ = ["RetrievalTrace", "ScoredDocumentLike", "remove_duplicates"]`. New modules should follow suit.
**Fix:** When the new files are created, declare:
- `pipeline/__init__.py`: `__all__ = ["QueryExtensionRunnable", "QueryVariants", "ImageCaptionRunnable"]`
- Add to `retrieval/__init__.py`: `from rag.retrieval.decomposition import QueryDecomposer, DecomposedQueries` and `from rag.retrieval.lazy_greedy import LazyGreedySelector`, update `__all__`.

#### G-P3-3: `histories` token estimate (char/2) is a rough heuristic
**Where:** `task13.md:570` `g_tokens = sum(len(str(h.get("content", ""))) // 2 for h in g)`
**Problem:** `len(content) // 2` is a rough CJK-friendly estimate but wildly wrong for English (1 token ≈ 4 chars) and code (1 token ≈ 3-4 chars). FastGPT uses real tiktoken. The `reserved_tokens: int = 1000` is also a magic number.
**Why P3:** Eval task 18 will likely catch this with mixed-language inputs. Not a blocker.
**Fix:** Use tiktoken (`tiktoken.encoding_for_model("gpt-4").encode(content)` then `len()`) for accurate counts. Or import a shared tokenizer from `rag.infra.llm`. Move `1000` to a named constant `HISTORY_RESERVED_TOKENS`.

#### G-P3-4: `DecomposedQueries` schema uses `description=...` for LLM guidance — this is non-standard
**Where:** `task13.md:175-181`:
```python
sub_queries: list[str] = Field(
    ...,
    min_length=2,
    max_length=8,
    description="拆解后的子查询列表, 至少 2 个、最多 8 个。"
                "如果原问题不需要拆解, 至少返回 [原问题, 一个等价改写]。",
)
```
**Problem:** `with_structured_output` does pass the field description to the LLM as part of the JSON schema, so this works. But the `description` is in the Pydantic schema, not in the user-prompt `DECOMPOSE_PROMPT` (line 192-198). Two places to maintain.
**Why P3:** Style preference.
**Fix:** Consolidate. Either:
- Put the schema constraints in the user prompt (cleaner separation of concerns).
- Or rely on the Pydantic `description` and remove the redundant text in `DECOMPOSE_PROMPT`.

---

## 6. 实施顺序 (哪些先做)

In order of dependency:

1. **Resolve P0-1** (broken import). One-line fix in `image_caption.py:746`. Add a test that imports the module without errors.

2. **Resolve P0-2** (lazy-greedy diversity weight). Pick Option A (match FastGPT) or Option B (justify the divergence). Update the `LazyGreedySelector._compute_marginal_gain` test in `test_lazy_greedy.py` to assert the exact gain formula.

3. **Resolve P0-3** (vision capability gate). Add the gate; update `ImageCaptionRunnable` to require a vision-capable model. Add a test.

4. **Resolve P0-4** (sequential → parallel + `httpx.AsyncClient` leak). Refactor the `for` loop to `asyncio.gather`. Add a test that asserts 2 URLs trigger 2 concurrent LLM calls.

5. **Apply P1-1** (image caption prompt → FastGPT verbatim). One-line change. No test change needed.

6. **Apply P1-2** (preserve image on caption failure). Add `failed_image_urls: list[str]` to the output. Update task 16's integration to handle this field.

7. **Apply P1-3** (data: URL handling). Add the short-circuit. Add a test.

8. **Apply P1-4** (document structured-output overhead). Docstring-only.

9. **Apply P1-5** (decomposition docstring). Add a header comment.

10. **Apply P1-6** (k=0/empty test). Add the test case to `test_lazy_greedy.py`.

11. **Apply P1-7** (naming convention). Pick `sub_queries` or `query_variants` and stick to it.

12. **Apply P2-1, P2-2, P2-3, P2-4, P2-5** as a doc cleanup pass.

13. **Apply P3-1, P3-2, P3-3, P3-4** in a follow-up commit.

After 1-4, the code is importable and the logic matches FastGPT. Items 1-4 are P0 blockers. Items 5-7 are P1 quality issues. Items 8-13 are P2/P3 cleanup.

---

## Appendix A: Confirmed FastGPT call sites

| File:line | Function | Purpose |
|---|---|---|
| `packages/service/core/ai/functions/queryExtension.ts:108-306` | `queryExtension()` | Core query extension: 8-rule system prompt, 3-output user prompt, Stage-1 json5 parse, Stage-2 lazy greedy |
| `packages/service/core/dataset/search/utils.ts:69-137` | `datasetSearchQueryExtension()` | Outer wrapper: gates on `llmModel && embeddingModel`, calls `queryExtension`, dedups by hash |
| `packages/service/core/ai/hooks/useTextCosine.ts:29-166` | `useTextCosine()` | Returns `lazyGreedyQuerySelection` + `embeddingModel` |
| `packages/service/core/dataset/search/defaultRecall/imageCaption.ts:33-125` | `getImageCaptionQueries()` | Per-image VLM captioning with `Promise.all` parallelism, vision-capability gate, per-image error isolation |
| `packages/service/core/dataset/search/utils.ts:50-61` | `normalizeImageToBase64()` | Conditional image base64 conversion (data: pass-through, env-gated) |
| `packages/service/core/ai/llm/utils.ts:47-124` | `filterGPTMessageByMaxContext()` | tiktoken-based history truncation preserving system/checkpoint messages |

## Appendix B: Decomposition does not exist in FastGPT — confirmation

```
$ grep -rln "decomposition\|decompose\|sub_query\|subQuery\|multi_step\|multi-hop\|complex.*query\|isComplex\|needDecompose" /Users/jung/pro/FastGPT/packages
(no results)
$ grep -rn "decompose" /Users/jung/pro/FastGPT/packages
docs/changelog.md only — not code
$ grep -rn "sub.query" /Users/jung/pro/FastGPT/packages
(no relevant matches)
```

The only "decompose" usage in the entire FastGPT repo is HTML/XML tag decomposition in chunker code, which is unrelated to query understanding. **The `QueryDecomposer` in task13.md is a spec-internal invention with zero FastGPT alignment.**

## Appendix C: Path inconsistency clarification (per prompt)

- `src/rag/pipeline/query_ext.py` (task13 target) — does not exist
- `src/rag/pipeline/image_caption.py` (task13 target) — does not exist
- `src/rag/retrieval/decomposition.py` (task13 target) — does not exist
- `src/rag/retrieval/lazy_greedy.py` (task13 target) — does not exist
- Per the main plan tree (`2026-06-10-python-rag-pipeline.md:314-317`):
  - `pipeline/` is for **request-shape orchestration** (query_ext, image_caption, parent_doc, etc.)
  - `retrieval/` is for **cross-cutting retrieval helpers** (decomposition, lazy_greedy, audit, citation_check)
- The split is **intentional** and consistent. task13.md respects the split.
- The `pipeline/` directory does not currently exist; only `retrieval/` exists with `trace.py` and `__init__.py`. The repo is **incomplete** w.r.t. the plan tree.
- **No path fix needed**; the inconsistency is between the *plan tree* and the *current state of the repo*.

## Appendix D: `get_m3_chat_model` — phantom import

```bash
$ grep -n "get_m3_chat_model" /Users/jung/pro/rag-pipeline/src/rag/infra/llm/chat.py
(no output)
$ grep -rn "get_m3_chat_model" /Users/jung/pro/rag-pipeline/src
/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task13.md:746
```

Only `task13.md:746` references this function. It is **not defined anywhere in the source tree**. The `MiniMax-M3` string appears in `config.py:37` as the default `openai_model`, but no factory function is named after it. The fix is to either:
1. Add `get_m3_chat_model(...)` to `chat.py` as a thin wrapper over `get_chat_model`, OR
2. Replace the import in `image_caption.py` with `get_chat_model` and pass the model name explicitly.

Recommend Option 2 (YAGNI — no need for a single-purpose factory when the underlying `get_chat_model(model="MiniMax-M3")` does the same thing).
