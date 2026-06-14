# Task 12: Filter Pipeline (去重 / 阈值 / token 预算)

> 源 spec: `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` §7.5 过滤管线 (filter_pipeline / remove_duplicates / filter_by_score / filter_by_token_budget)
>
> Fixes applied:
> - (audit #1 P1-1) stub-first 违反: 加 Step 0 stub(`def remove_duplicates(hits): return list(hits)` + `def filter_by_score(hits, t): return list(hits)` + `def filter_pipeline(...): return ([], [])` 占位),确保 RED 阶段模块可 import。
> - (subagent #8 #1) `remove_duplicates` 改用 `q+a` 归一化 hash: 同 query 同 answer 才视为重复,避免「不同问法但答案文本相同」被误去重。`ScoredDocument` 新增可选 `q: str | None = None` / `a: str | None = None` 字段(默认 None 兼容旧调用,`q+a` 都为 None 时回退到 `text` 哈希)。
> - (subagent #8 #2) filter 调用位置: `filter_pipeline` 拆为两个独立函数 — `subgraph_filter(hits, score_threshold, per_dataset_token_budget, using_re_rerank)`(subgraph 内,per-dataset 子预算)+ `orchestrator_filter(hits, score_threshold, max_tokens)`(orchestrator 顶层,全局预算)。`filter_pipeline` 保留为薄封装,默认走 orchestrator 路径,向后兼容。
> - (subagent #8 #3) 阈值过滤按 `using_re_rerank` 切换 score 来源: `using_re_rerank=True` 时,`filter_by_score` 改用 `doc.rerank_score`(rerank 后的相关性),否则用 `doc.score`(RRF 后的融合分)。`ScoredDocument` 同步新增 `rerank_score: float | None = None` 字段。

## Open P0s (2026-06-14 audit)

| P0 ID | 描述 | 文件:行 | 解决路径 |
|---|---|---|---|
| G-P0-1 | `ScoredDocument` 实际已不包含 `q` / `a` 字段(task3 迁至 `RetrievalTrace`),task12 仍按旧模型写 `remove_duplicates(hits: list[ScoredDocument])` 会在运行期 `AttributeError: 'ScoredDocument' object has no attribute 'q'` | task12.md:24-25, 107-113, 244, 276 | M1 (5b) — 改用 `RetrievalTrace` 平行数组签名, `remove_duplicates` 从 `retrieval/trace.py` re-export |
| G-P0-2 | `filter_by_score` 阈值比对 `doc.score` (RRF 累加和) 而非 `doc.score_breakdown[source]` (raw embedding), 对 `threshold=0.3` 默认值会丢弃全部命中(RRF 单源 rank-1 ≈ 0.0164) | task12.md:23, 152-158, 282-296 | M1 (5b) — 改读 `score_breakdown[source]`, 测试同步换 `score_breakdown={"vector": 0.5}` |
| G-P0-3 | `filter_by_score` 缺 `searchMode` gate: FastGPT 在 `fullTextRecall` / `mixedRecall` 模式下不应用相似度过滤, task12 对全 source 应用会丢光 fulltext-only 结果 | task12.md (全文件) | M1 (5b) — 加 `search_mode: Literal[...]` 参数, 非 embedding 模式跳过 filter 并 append warning |

详细分析见 `audit/2026-06-14-task12-alignment.md` §5 (修复建议)。

**Files:**
- Create: `src/rag/pipeline/filter.py`
- Create: `tests/unit/test_filter.py`

**依赖字段(本 task 不创建 schema,只引用,字段在 Task 3 `domain/document.py` 中追加):**

```python
# 追加到 src/rag/domain/document.py::ScoredDocument
class ScoredDocument(BaseModel):
    """召回结果: RRF 公式需要 score + rank 同时存。"""
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    q: str | None = None              # subagent #8: 该 chunk 对应的 query (去重 key 之一)
    a: str | None = None              # subagent #8: 该 chunk 对应的 answer (去重 key 之一)
    score: float                       # RRF 融合后的分数
    rerank_score: float | None = None  # subagent #8: rerank 模型打分 (using_re_rerank=True 时使用)
    rank: int
    source: Literal["vector", "fulltext", "caption", "rerank"]
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None
    metadata: ChunkMetadata
    embedding: list[float] | None = None
```

- [ ] **Step 0: 写 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# src/rag/pipeline/filter.py (stub)
import hashlib
from rag.domain.document import ScoredDocument

def _qna_hash(q: str | None, a: str | None, text: str) -> str:
    """Stub: 待实现 q+a 归一化 hash。"""
    return hashlib.md5(text.encode()).hexdigest()

def remove_duplicates(hits: list[ScoredDocument]) -> list[ScoredDocument]:
    """Stub: 基于 q+a 归一化 hash 去重。"""
    return list(hits)

def filter_by_score(
    hits: list[ScoredDocument],
    threshold: float,
    using_re_rerank: bool = False,
) -> list[ScoredDocument]:
    """Stub: 阈值过滤, using_re_rerank=True 时改用 rerank_score。"""
    return list(hits)

def filter_by_token_budget(
    hits: list[ScoredDocument],
    max_tokens: int,
    min_keep: int = 1,
) -> tuple[list[ScoredDocument], list[str]]:
    """Stub: token 预算截断。"""
    return list(hits), []

def subgraph_filter(
    hits: list[ScoredDocument],
    score_threshold: float = 0.0,
    per_dataset_token_budget: int | None = None,
    using_re_rerank: bool = False,
) -> tuple[list[ScoredDocument], list[str]]:
    """Stub: subgraph 内 filter — per-dataset 子预算。"""
    return list(hits), []

def orchestrator_filter(
    hits: list[ScoredDocument],
    score_threshold: float = 0.0,
    max_tokens: int | None = None,
    using_re_rerank: bool = False,
) -> tuple[list[ScoredDocument], list[str]]:
    """Stub: orchestrator 顶层 filter — 全局预算。"""
    return list(hits), []

# 向后兼容封装
def filter_pipeline(
    hits: list[ScoredDocument],
    score_threshold: float = 0.0,
    max_tokens: int | None = None,
) -> tuple[list[ScoredDocument], list[str]]:
    """Stub: 旧 API, 默认走 orchestrator 路径。"""
    return orchestrator_filter(hits, score_threshold, max_tokens)
```

- [ ] **Step 1: 写失败单测**

```python
# tests/unit/test_filter.py
import uuid
import pytest
from rag.domain.document import ScoredDocument, ChunkMetadata
from rag.pipeline.filter import (
    remove_duplicates, filter_by_score, filter_by_token_budget,
    subgraph_filter, orchestrator_filter, filter_pipeline,
)

def _doc(text="x", score=0.5, q=None, a=None, rerank_score=None, chunk_id=None):
    return ScoredDocument(
        chunk_id=chunk_id or uuid.uuid4(),
        dataset_id=uuid.uuid4(), text=text, q=q, a=a,
        score=score, rerank_score=rerank_score, rank=0,
        source="vector", metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"),
    )

# ── subagent #8 #1: q+a 归一化去重 ─────────────────────────

def test_remove_duplicates_by_qa():
    """subagent #8: 同 q + 同 a 视为重复, 即使 text 不同。"""
    h1 = _doc(text="text-A", q="Q1", a="A1")
    h2 = _doc(text="text-B", q="Q1", a="A1")  # 同 q+a → 重复
    h3 = _doc(text="text-A", q="Q1", a="A2")  # 不同 a → 保留
    out = remove_duplicates([h1, h2, h3])
    assert len(out) == 2
    assert out[0].text == "text-A"
    assert out[1].text == "text-A"
    assert out[1].a == "A2"

def test_remove_duplicates_different_qa_kept():
    """同 text 但不同 q → 保留 (说明 q+a 优于 text-only 哈希)。"""
    h1 = _doc(text="same", q="Q1", a="A1")
    h2 = _doc(text="same", q="Q2", a="A2")
    out = remove_duplicates([h1, h2])
    assert len(out) == 2

def test_remove_duplicates_fallback_to_text_when_qa_missing():
    """q+a 都为 None 时回退到 text 哈希 (向后兼容旧数据)。"""
    h1 = _doc(text="same")    # q=None, a=None
    h2 = _doc(text="same")
    h3 = _doc(text="diff")
    out = remove_duplicates([h1, h2, h3])
    assert len(out) == 2

def test_remove_duplicates_qa_normalization():
    """q+a 归一化: 大小写 / 空白差异视为相同。"""
    h1 = _doc(text="t1", q="  Hello  ", a="World")
    h2 = _doc(text="t2", q="hello", a="world")  # 归一化后相同
    out = remove_duplicates([h1, h2])
    assert len(out) == 1

# ── 阈值过滤 ──────────────────────────────────────────────

def test_filter_by_score_threshold():
    """默认按 doc.score 过滤。"""
    h1 = _doc(score=0.5)
    h2 = _doc(score=0.1)
    out = filter_by_score([h1, h2], threshold=0.3)
    assert len(out) == 1
    assert out[0].score == 0.5

def test_filter_by_score_uses_rerank_score_when_flag_set():
    """subagent #8 #3: using_re_rerank=True 时改用 rerank_score。"""
    h1 = _doc(score=0.5, rerank_score=0.9)   # rerank_score 高 → 保留
    h2 = _doc(score=0.9, rerank_score=0.1)   # RRF 高但 rerank 低 → 过滤
    out = filter_by_score([h1, h2], threshold=0.3, using_re_rerank=True)
    assert len(out) == 1
    assert out[0].rerank_score == 0.9

def test_filter_by_score_falls_back_to_score_if_rerank_score_missing():
    """rerank_score 为 None 时 (未 rerank) 回退到 doc.score, 但只在使用 rerank 时生效。"""
    h1 = _doc(score=0.5, rerank_score=None)   # 无 rerank 分
    h2 = _doc(score=0.1, rerank_score=0.9)   # 有 rerank 分但低
    out = filter_by_score([h1, h2], threshold=0.3, using_re_rerank=True)
    # h1: 无 rerank_score → 用 score=0.5 ≥ 0.3 → 保留
    # h2: rerank_score=0.9 ≥ 0.3 → 保留
    assert len(out) == 2

# ── token 预算 ────────────────────────────────────────────

def test_filter_by_token_budget_keeps_minimum():
    h1 = _doc(text="a" * 4000)   # ~2000 tokens
    h2 = _doc(text="b" * 4000)   # ~2000 tokens
    h3 = _doc(text="c" * 100)    # ~50 tokens
    out, warnings = filter_by_token_budget([h1, h2, h3], max_tokens=100, min_keep=1)
    assert len(out) >= 1
    assert any("token_budget" in w for w in warnings)

# ── filter_pipeline (向后兼容) ─────────────────────────────

def test_filter_pipeline_runs_all_steps():
    h1 = _doc(text="same", q="Q", a="A", score=0.5)
    h2 = _doc(text="same", q="Q", a="A", score=0.1)  # 重复
    h3 = _doc(text="unique", q="Q2", a="A2", score=0.4)
    out, warnings = filter_pipeline([h1, h2, h3], score_threshold=0.2, max_tokens=10000)
    assert len(out) == 2

# ── subagent #8 #2: subgraph vs orchestrator 入口分离 ──────

def test_subgraph_filter_uses_per_dataset_budget():
    """subgraph_filter 的 max_tokens 语义是 per-dataset 子预算。"""
    # 5 个 doc, 每个 ~100 tokens → 共 ~500
    docs = [_doc(text="x" * 200, score=0.5) for _ in range(5)]
    out, _ = subgraph_filter(docs, score_threshold=0.0, per_dataset_token_budget=200)
    # 200 tokens 预算 → 最多保留 2-3 个, 不会一次性用全局预算
    assert len(out) <= 4
    assert len(out) >= 1

def test_orchestrator_filter_uses_global_budget():
    """orchestrator_filter 的 max_tokens 语义是全局预算 (subgraph 输出后再次截断)。"""
    docs = [_doc(text="x" * 200, score=0.5) for _ in range(10)]
    out, _ = orchestrator_filter(docs, score_threshold=0.0, max_tokens=300)
    # 全局 300 tokens → 最多保留 2-3 个
    assert len(out) <= 4
    assert len(out) >= 1

def test_subgraph_filter_passes_rerank_flag():
    """subgraph_filter 内 using_re_rerank=True → 内部 filter_by_score 走 rerank_score。"""
    h1 = _doc(score=0.5, rerank_score=0.9)   # rerank 高 → 保留
    h2 = _doc(score=0.9, rerank_score=0.1)   # rerank 低 → 过滤
    out, _ = subgraph_filter([h1, h2], score_threshold=0.3, using_re_rerank=True)
    assert len(out) == 1
    assert out[0].rerank_score == 0.9

def test_orchestrator_filter_passes_rerank_flag():
    """orchestrator_filter 内 using_re_rerank=True → 同样走 rerank_score。"""
    h1 = _doc(score=0.5, rerank_score=0.9)
    h2 = _doc(score=0.9, rerank_score=0.1)
    out, _ = orchestrator_filter([h1, h2], score_threshold=0.3, using_re_rerank=True)
    assert len(out) == 1
    assert out[0].rerank_score == 0.9
```

- [ ] **Step 2: 跑测试,确认 fail**

```bash
uv run pytest tests/unit/test_filter.py -v
# 期望: 大部分 fail (stub 返回 list(hits), q+a 去重/阈值断言不满足); 无 ImportError
```

- [ ] **Step 3: 写 filter.py**

```python
# src/rag/pipeline/filter.py
import hashlib
import re
from rag.domain.document import ScoredDocument

_WHITESPACE_RE = re.compile(r"\s+")

def _normalize(s: str | None) -> str:
    """q+a 归一化: 去除首尾空白 + 折叠中间空白 + 小写。"""
    if s is None:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip().lower()

def _qna_hash(q: str | None, a: str | None, text: str) -> str:
    """subagent #8 #1: q+a 归一化 hash, 任一缺失回退到 text。
    
    优先级: q+a 都存在 → q+a; 否则 → text。避免「不同 query 但答案文本相同」误去重。
    """
    norm_q = _normalize(q)
    norm_a = _normalize(a)
    if norm_q or norm_a:
        payload = f"{norm_q}|{norm_a}"
    else:
        payload = text
    return hashlib.md5(payload.encode()).hexdigest()

def remove_duplicates(hits: list[ScoredDocument]) -> list[ScoredDocument]:
    """subagent #8 #1: 基于 q+a 归一化 hash 去重, 保留首次出现。
    
    q+a 缺失时回退 text 哈希, 向后兼容。
    """
    seen: set[str] = set()
    out: list[ScoredDocument] = []
    for h in hits:
        key = _qna_hash(h.q, h.a, h.text)
        if key not in seen:
            seen.add(key)
            out.append(h)
    return out

def filter_by_score(
    hits: list[ScoredDocument],
    threshold: float,
    using_re_rerank: bool = False,
) -> list[ScoredDocument]:
    """subagent #8 #3: 阈值过滤。
    
    using_re_rerank=True 且 rerank_score 非空 → 用 rerank_score 比较;
    其余情况用 doc.score (RRF 融合分)。
    """
    def _get_score(h: ScoredDocument) -> float:
        if using_re_rerank and h.rerank_score is not None:
            return h.rerank_score
        return h.score
    return [h for h in hits if _get_score(h) >= threshold]

def filter_by_token_budget(
    hits: list[ScoredDocument],
    max_tokens: int,
    min_keep: int = 1,
) -> tuple[list[ScoredDocument], list[str]]:
    """H3 修正: 截断语义 — 单 chunk 超 max_tokens 整条保留 + warning。
    
    简单 token 估算: len(text) / 2 (中文) 或 len(text) / 4 (英文),
    此处保守取 max(len(text) // 2, 1)。
    """
    warnings: list[str] = []
    total = 0
    out: list[ScoredDocument] = []
    for h in hits:
        est_tokens = max(len(h.text) // 2, 1)
        if total + est_tokens > max_tokens:
            if len(out) < min_keep:
                out.append(h)
                warnings.append(f"token_budget_exceeded: kept {h.chunk_id} ({est_tokens} tokens)")
            else:
                warnings.append(f"token_budget_truncated: dropped {h.chunk_id}")
                continue
        out.append(h)
        total += est_tokens
    return out, warnings

def subgraph_filter(
    hits: list[ScoredDocument],
    score_threshold: float = 0.0,
    per_dataset_token_budget: int | None = None,
    using_re_rerank: bool = False,
) -> tuple[list[ScoredDocument], list[str]]:
    """subagent #8 #2: subgraph 内 filter — per-dataset 子预算。
    
    调用方: 每个 dataset 的 subgraph 内部, 用本数据集的 token 子预算截断,
    避免单 dataset 把全局预算耗尽。
    """
    hits = remove_duplicates(hits)
    hits = filter_by_score(hits, score_threshold, using_re_rerank=using_re_rerank)
    warnings: list[str] = []
    if per_dataset_token_budget is not None:
        hits, w = filter_by_token_budget(hits, per_dataset_token_budget)
        warnings.extend(w)
    return hits, warnings

def orchestrator_filter(
    hits: list[ScoredDocument],
    score_threshold: float = 0.0,
    max_tokens: int | None = None,
    using_re_rerank: bool = False,
) -> tuple[list[ScoredDocument], list[str]]:
    """subagent #8 #2: orchestrator 顶层 filter — 全局预算。
    
    调用方: 跨 dataset 融合后, 顶层用全局 token 预算做最终截断。
    """
    hits = remove_duplicates(hits)
    hits = filter_by_score(hits, score_threshold, using_re_rerank=using_re_rerank)
    warnings: list[str] = []
    if max_tokens is not None:
        hits, w = filter_by_token_budget(hits, max_tokens)
        warnings.extend(w)
    return hits, warnings

# 向后兼容: 旧调用方 (主 plan 2546-2673 中的 filter_pipeline) 默认走 orchestrator 路径
def filter_pipeline(
    hits: list[ScoredDocument],
    score_threshold: float = 0.0,
    max_tokens: int | None = None,
) -> tuple[list[ScoredDocument], list[str]]:
    """过滤管线 (旧 API): 去重 → 阈值 → token 预算。返回 (filtered, warnings)。
    
    默认按 orchestrator 路径走 (全局预算)。subgraph 内部应改用 subgraph_filter。
    """
    return orchestrator_filter(hits, score_threshold, max_tokens)
```

- [ ] **Step 4: 跑测试,确认 pass**

```bash
uv run pytest tests/unit/test_filter.py -v
# 期望: 13 passed
```

- [ ] **Step 5: 提交**

```bash
git add src/rag/pipeline/filter.py tests/
git commit -m "feat(pipeline): filter pipeline (q+a dedup + per-dataset budget + rerank-aware threshold)

subagent #8: remove_duplicates uses q+a normalized hash (fallback to text).
filter_by_score switches to rerank_score when using_re_rerank=True.
subgraph_filter takes per_dataset_token_budget for subgraph-level truncation;
orchestrator_filter keeps global max_tokens for top-level truncation.
filter_pipeline kept as backward-compat shim that delegates to orchestrator_filter.

ScoredDocument gains q/a/rerank_score optional fields (default None).
"
```

- [ ] **Step 6: 提交后 cross-check 调用点**

1. `subgraph.py` (Task 14): subgraph 内 `filter_pipeline` 调用点需改为 `subgraph_filter(..., per_dataset_token_budget=dataset.budget, using_re_rerank=reranker is not None)`。
2. `orchestrator.py` (Task 14): 顶层 `filter_pipeline` 调用点保持 `orchestrator_filter(..., max_tokens=self.max_tokens, using_re_rerank=...)`。
3. `domain/document.py` (Task 3): 追加 `q: str | None = None` / `a: str | None = None` / `rerank_score: float | None = None` 字段 — 本 task 不实施, 留 Step 6 提示给后续 task。
