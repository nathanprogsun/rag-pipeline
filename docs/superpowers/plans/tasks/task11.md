# Task 11: Fusion (intra + inter WRRF)

> Source spec: `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md`
> - Topology diagram: lines 133-144 (三层 RRF, 1 dataset × N query variants)
> - Constants table: lines 374-376 (`RRF_K=60`, `vector_weight=0.7`, `fulltext_weight=0.3`)
> - `Dataset.rrf_k` field: line 403
> - `Dataset.rerank_weight` field: line 489
> - §7.2 Intra RRF formula (WRRF): lines 902-932
> - §7.3 Inter-dataset RRF (equal weight, no `w`): lines 935-939
>
> Fixes applied:
> - (B4 🔴 Blocker) `intra_fusion` 改为接受 `query_groups: list[list[ScoredDocument]]`,**每组内 `enumerate(start=1)` 计算局部 rank,跨组 RRF 累加同 `chunk_id` 的 score**。原版用 `vector_hits` / `fulltext_hits` 两路无法支撑多 query_variant 并行检索的真实拓扑(子图实际是 1 dataset × N 个 query variant 的 N 路)。
> - (audit #1 P1-1) stub-first 违反: 加 Step 0 stub(`def intra_fusion(query_groups): return []` + `def inter_dataset_fusion(hits): return []` 占位),确保 RED 阶段模块可 import 而非 ImportError。
> - (audit #2 P1-5) `RRF_K=60` → 引用 `Dataset.rrf_k: int = 60`(已在 spec 第 403 行定义,本文件不再重复声明,改用 `from rag.domain.dataset import Dataset` + 调用方 `intra_fusion(..., rrf_k=dataset.rrf_k)`)。
> - (subagent #5) 边界: `len(candidates) <= self.k: return list(candidates)`(返回副本避免下游 mutation 污染输入)。`intra_fusion` 与 `inter_dataset_fusion` 都加该 fast-path。
> - (P0-1, audit G-P0-1) `ScoredDocument` 新增 `score_breakdown: dict[str, float]`,fusion 在每次 sighting 时按 `source` 写 `max`,保留 per-source raw score。`score` 字段保持 RRF 累加和(供排序),`score_breakdown` 保留 per-source 原值,实现 Option A(详见 `src/rag/domain/document.py` 字段 docstring)。
> - (P0-2, audit G-P0-2, B4 走到底) `query_groups[g]` 语义锁定为**单个 query variant 的合并结果**(已由 task 12+ 的召回层在 vector+fulltext 之间合并);`weights[g]` 是 per-query-variant trust weight。删除旧 docstring 中"vector_weight / fulltext_weight"的矛盾注释。
> - (P0-3, audit G-P0-3) 第 3 行原引用 `2026-06-10-python-rag-pipeline.md:2416-2542`(plan 仅 505 行,范围不存在)已替换为上方 spec 文件的真实行号。

## Open P0s (2026-06-14 audit)

**无 Open P0s** — 3 个 P0 (P0-1 score_breakdown 字段, P0-2 query_variant 语义, P0-3 行号引用) 已在 round 1 修复。

详细分析见 `audit/2026-06-14-task11-alignment.md`。

**Files:**
- Create: `src/rag/pipeline/__init__.py`
- Create: `src/rag/pipeline/fusion.py`
- Create: `tests/unit/test_fusion.py`
- Modify: `src/rag/domain/document.py` (P0-1: 新增 `score_breakdown` 字段)

- [ ] **Step 0: 写 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# src/rag/pipeline/fusion.py (stub)
import uuid
from rag.domain.document import ScoredDocument

DEFAULT_RRF_K = 60   # 默认值, 可被 dataset.rrf_k 覆盖 (Cormack 2009)

def intra_fusion(
    query_groups: list[list[ScoredDocument]],
    weights: list[float] | None = None,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[ScoredDocument]:
    """Stub: 待实现 N 路 RRF 跨组融合 (P0-2 走 query_variant 语义).

    见下方 Step 3 docstring 完整说明。返回空列表占位。
    `score_breakdown` (P0-1) 在 stub 阶段不写,实现阶段在每次 sighting 时填充。
    """
    return []

def inter_dataset_fusion(
    hits: list[ScoredDocument],
    rrf_k: int = DEFAULT_RRF_K,
) -> list[ScoredDocument]:
    """Stub: 待实现跨 dataset RRF 融合."""
    return []
```

- [ ] **Step 1: 写失败单测**

```python
# tests/unit/test_fusion.py
import uuid
import pytest
from rag.domain.document import ScoredDocument, ChunkMetadata
from rag.domain.dataset import Dataset
from rag.pipeline.fusion import intra_fusion, inter_dataset_fusion, DEFAULT_RRF_K

def _doc(chunk_id_str, score=0.0, source="vector", dataset_id=None):
    ds = dataset_id or uuid.uuid4()
    return ScoredDocument(
        chunk_id=uuid.UUID(chunk_id_str), dataset_id=ds,
        text="x", score=score, rank=0, source=source,
        metadata=ChunkMetadata(dataset_id=ds, datasource="file"),
    )

def test_intra_wrrf_formula():
    """B4: 1 组 1 chunk, 局部 rank=1, score = 1/(rrf_k+1) (单组默认 w=1.0)."""
    hits = [_doc("00000000-0000-0000-0000-000000000001")]
    fused = intra_fusion([hits])
    assert abs(fused[0].score - 1.0 / (DEFAULT_RRF_K + 1)) < 1e-6

def test_intra_dual_group_same_chunk():
    """B4: 同一 chunk_id 出现在 2 个 query_group, RRF score 跨组累加。
    group1 局部 rank=1 → 1/(k+1); group2 局部 rank=1 → 1/(k+1); 总分 2/(k+1).
    """
    group1 = [_doc("00000000-0000-0000-0000-000000000001")]
    group2 = [_doc("00000000-0000-0000-0000-000000000001")]
    fused = intra_fusion([group1, group2])
    expected = 2.0 / (DEFAULT_RRF_K + 1)
    assert abs(fused[0].score - expected) < 1e-6

def test_intra_sort_descending():
    """3 组各 1 个不同 chunk, score 相同 → 返回顺序稳定, 长度 = 3."""
    g1 = [_doc("00000000-0000-0000-0000-000000000001")]
    g2 = [_doc("00000000-0000-0000-0000-000000000002")]
    g3 = [_doc("00000000-0000-0000-0000-000000000003")]
    fused = intra_fusion([g1, g2, g3])
    assert len(fused) == 3
    assert all(f.score == 1.0 / (DEFAULT_RRF_K + 1) for f in fused)

def test_intra_local_rank_per_group():
    """B4 关键不变式: group1 中第 1 个 chunk 的 rank 是 1(不是全局 rank 1).
    group1 长度 3, 第 1 个 chunk 局部 rank=1 → 1/(k+1);
    group2 长度 1, 第 1 个 chunk 局部 rank=1 → 1/(k+1);
    同 chunk 跨组累加 → 2/(k+1).
    """
    a = _doc("00000000-0000-0000-0000-000000000001")
    b = _doc("00000000-0000-0000-0000-000000000002")
    c = _doc("00000000-0000-0000-0000-000000000003")
    group1 = [a, b, c]   # a 局部 rank=1
    group2 = [a]          # a 局部 rank=1
    fused = intra_fusion([group1, group2])
    # a 应排第一, score = 2/(k+1)
    assert fused[0].chunk_id == a.chunk_id
    assert abs(fused[0].score - 2.0 / (DEFAULT_RRF_K + 1)) < 1e-6

def test_intra_respects_dataset_rrf_k():
    """audit #2 P1-5: 调用方传 dataset.rrf_k 即可覆盖默认值."""
    ds = Dataset(
        id=uuid.uuid4(), name="t", embed_model="m", embed_dim=1536,
        rrf_k=30,  # 非默认
    )
    hits = [_doc("00000000-0000-0000-0000-000000000001")]
    fused = intra_fusion([hits], rrf_k=ds.rrf_k)
    assert abs(fused[0].score - 1.0 / (ds.rrf_k + 1)) < 1e-6

def test_intra_uses_rrf_k_not_default_when_dataset_overrides():
    """audit #2 P1-5: dataset.rrf_k=10 → score = 1/(10+1)=1/11, 不等于 1/(60+1)."""
    hits = [_doc("00000000-0000-0000-0000-000000000001")]
    fused_default = intra_fusion([hits])
    fused_custom = intra_fusion([hits], rrf_k=10)
    assert fused_default[0].score != fused_custom[0].score
    assert abs(fused_custom[0].score - 1.0 / 11) < 1e-6

def test_inter_dataset_fusion():
    """跨 dataset RRF 累加."""
    ds1, ds2 = uuid.uuid4(), uuid.uuid4()
    hits = [
        _doc("00000000-0000-0000-0000-000000000001", dataset_id=ds1),
        _doc("00000000-0000-0000-0000-000000000002", dataset_id=ds2),
    ]
    fused = inter_dataset_fusion(hits)
    assert len(fused) == 2
    # 都排在 rank 1 (enumerate start=1)
    assert abs(fused[0].score - 1.0 / (DEFAULT_RRF_K + 1)) < 1e-6

def test_inter_does_not_mutate_input():
    """subagent #5: 返回副本, 不修改入参."""
    hits = [
        _doc("00000000-0000-0000-0000-000000000001"),
        _doc("00000000-0000-0000-0000-000000000002"),
    ]
    before = list(hits)
    inter_dataset_fusion(hits)
    assert hits == before   # 入参不变

# ---------- P0-1 新增: score_breakdown 行为 ----------

def test_intra_score_breakdown_max_semantics():
    """P0-1 (G-P0-1, Option A): 同一 chunk 在同一 query_group 中以同一 source
    出现两次,fusion 取 max 而非累加;不同 source 各自保留 max。
    """
    cid = "00000000-0000-0000-0000-000000000001"
    # 同一 query variant (group0) 内:vector rank=1 score=0.5;vector rank=3 score=0.9
    group0 = [
        _doc(cid, score=0.5, source="vector"),
        _doc("00000000-0000-0000-0000-000000000002", score=0.8, source="vector"),
        _doc(cid, score=0.9, source="vector"),  # 同 source 第二次出现, raw 更高
    ]
    # 第二个 query variant (group1) 内:同一 chunk 来自 fulltext,raw 0.7
    group1 = [_doc(cid, score=0.7, source="fulltext")]
    fused = intra_fusion([group0, group1])
    assert len(fused) == 2
    # 找到目标 chunk
    target = next(f for f in fused if f.chunk_id == uuid.UUID(cid))
    # score_breakdown 应该是 max(0.5, 0.9) for vector + 0.7 for fulltext
    assert target.score_breakdown == pytest.approx({"vector": 0.9, "fulltext": 0.7})
    # .score 字段保持 RRF 累加和(用于排序)
    expected_rrf = (
        1.0 / (DEFAULT_RRF_K + 1)   # group0 rank=1 vector
        + 1.0 / (DEFAULT_RRF_K + 3) # group0 rank=3 vector
        + 1.0 / (DEFAULT_RRF_K + 1) # group1 rank=1 fulltext
    )
    assert abs(target.score - expected_rrf) < 1e-6

def test_intra_score_breakdown_preserves_per_source_max_across_query_variants():
    """P0-1: 同一 chunk 在两个 query variant (group) 中以同一 source 出现,
    跨 group 也取 max(对齐 FastGPT concatScore.find(type).value = max(...)).
    """
    cid = "00000000-0000-0000-0000-000000000001"
    # query variant A: vector rank=2, raw 0.6
    group_a = [
        _doc("00000000-0000-0000-0000-000000000002", score=0.9, source="vector"),
        _doc(cid, score=0.6, source="vector"),
    ]
    # query variant B: vector rank=1, raw 0.8 (更高)
    group_b = [_doc(cid, score=0.8, source="vector")]
    fused = intra_fusion([group_a, group_b])
    target = fused[0]
    assert target.chunk_id == uuid.UUID(cid)
    # 跨 group 同一 source 取 max: 0.8 (不是 0.6 + 0.8 = 1.4)
    assert target.score_breakdown == pytest.approx({"vector": 0.8})
```

- [ ] **Step 2: 跑测试,确认 fail**

```bash
uv run pytest tests/unit/test_fusion.py -v
# 期望: 大部分 fail (stub 返回 [], score/len 断言不满足); 无 ImportError
# P0-1 新增 2 个 score_breakdown 测试也会 fail (stub 不写 score_breakdown)
```

- [ ] **Step 3: 写 fusion.py (B4 修正 + P0-1 + P0-2 修正: 跨 query_group RRF + score_breakdown)**

```python
# src/rag/pipeline/fusion.py
import math
import uuid
from rag.domain.document import ScoredDocument

DEFAULT_RRF_K = 60   # 默认值, 可被 dataset.rrf_k 覆盖 (Cormack 2009)

def intra_fusion(
    query_groups: list[list[ScoredDocument]],
    weights: list[float] | None = None,   # P0-11 修复: per-query-variant 权重, 长度 = len(query_groups)
    rrf_k: int = DEFAULT_RRF_K,       # spec §0.1: per-dataset 可配 (Dataset.rrf_k)
) -> list[ScoredDocument]:
    """第一层融合: N 路 query_variant 加权 RRF (Weighted RRF, WRRF)。

    P0-2 走到底 (B4 拓扑): 每个 ``query_groups[g]`` 是单个 query variant 的
    **已经合并 (vector+fulltext) 的检索结果**,由 task 12+ 召回层在更上游
    完成 vector/fulltext 的合并后再传入本函数。``weights[g]`` 是 per-query-variant
    trust weight (例如对改写 query 给较低权重),默认等权 1.0。

    公式: score(c) = Σ_g  w_g / (rrf_k + rank_g(c))   其中 w_g = weights[g]

    P0-1 (Option A): 每次 sighting 时在 ``score_breakdown[d.source]`` 写
    ``max(prev, d.score)``,保留 per-source raw score (对齐 FastGPT
    ``concatScore.find(type).value = max(...)``)。``score`` 字段被 RRF 累加和
    覆盖,``score_breakdown`` 保留原始 raw similarity 供下游阈值过滤使用。

    subagent #5: 返回新 list, 不修改入参。
    """
    all_hits: list[ScoredDocument] = [d for g in query_groups for d in g]
    if not all_hits:
        return []

    if weights is None:
        weights = [1.0] * len(query_groups)
    assert len(weights) == len(query_groups),         f"weights length {len(weights)} != query_groups length {len(query_groups)}"

    by_chunk: dict[uuid.UUID, ScoredDocument] = {}
    for g_idx, group in enumerate(query_groups):
        w_g = weights[g_idx]
        # B4 关键: 每组内 enumerate(start=1) 局部 rank, 不跨组延续
        for rank, d in enumerate(group, start=1):
            rrf_contribution = w_g / (rrf_k + rank)
            existing = by_chunk.get(d.chunk_id)
            if existing is None:
                # P0-1: 首次 sighting — 初始化 score_breakdown, 写入该 source 的 raw score
                new_breakdown = {d.source: d.score}
                by_chunk[d.chunk_id] = d.model_copy(update={
                    "score": rrf_contribution,
                    "rank": rank,
                    "source": d.source,
                    "score_breakdown": new_breakdown,
                })
            else:
                # P0-1: 重复 sighting — 同 source 取 max (max-per-type 语义)
                new_breakdown = dict(existing.score_breakdown)
                prev = new_breakdown.get(d.source, -math.inf)
                new_breakdown[d.source] = max(prev, d.score)
                by_chunk[d.chunk_id] = existing.model_copy(update={
                    "score": existing.score + rrf_contribution,
                    "score_breakdown": new_breakdown,
                })
    return sorted(by_chunk.values(), key=lambda x: x.score, reverse=True)

def inter_dataset_fusion(
    hits: list[ScoredDocument],
    rrf_k: int = DEFAULT_RRF_K,       # spec §0.1: per-dataset 可配 (Dataset.rrf_k)
) -> list[ScoredDocument]:
    """第三层: 跨 dataset RRF 融合, dataset 间等权。

    P0-1: 同样在每次 sighting 时按 source 写 max 到 score_breakdown。
    注: 跨 dataset 调用方传入前,每个 dataset 内部已走完 intra_fusion,
    同一 chunk 跨 dataset 出现时其 ``source`` 字段已是 intra 阶段的
    最后写入值 (单一 source);此处按该 source 继续 max 合并。

    subagent #5: 返回新 list, 不修改入参。
    """
    if not hits:
        return []

    by_chunk: dict[uuid.UUID, ScoredDocument] = {}
    for rank, d in enumerate(hits, start=1):
        rrf_contribution = 1.0 / (rrf_k + rank)
        existing = by_chunk.get(d.chunk_id)
        if existing is None:
            new_breakdown = {d.source: d.score}
            by_chunk[d.chunk_id] = d.model_copy(update={
                "score": rrf_contribution,
                "rank": rank,
                "score_breakdown": new_breakdown,
            })
        else:
            new_breakdown = dict(existing.score_breakdown)
            prev = new_breakdown.get(d.source, -math.inf)
            new_breakdown[d.source] = max(prev, d.score)
            by_chunk[d.chunk_id] = existing.model_copy(update={
                "score": existing.score + rrf_contribution,
                "score_breakdown": new_breakdown,
            })
    return sorted(by_chunk.values(), key=lambda x: x.score, reverse=True)
```

- [ ] **Step 4: 跑测试,确认 pass**

```bash
uv run pytest tests/unit/test_fusion.py -v
# 期望: 10 passed (8 原有 + 2 P0-1 新增)
```

- [ ] **Step 5: 提交**

```bash
git add src/rag/pipeline/fusion.py src/rag/domain/document.py tests/
git commit -m "feat(pipeline): N-way intra-fusion + inter-dataset WRRF

B4 fix: intra_fusion now takes query_groups (list[list[ScoredDocument]]),
local rank enumerate(start=1) per group, RRF accumulates across groups on
same chunk_id. Aligns with the real topology (1 dataset × N query variants).

audit #2 P1-5: rrf_k param still default 60 but caller passes dataset.rrf_k.
subagent #5: both functions return new lists, never mutate inputs.

P0-1 (G-P0-1, Option A): ScoredDocument gains score_breakdown dict that
preserves per-source raw scores via max-per-source merge semantics
(aligns with FastGPT concatScore.find(type).value = max(...)). score
field stays as RRF accumulation sum used for sort.

P0-2 (G-P0-2, B4 走到底): query_groups[g] is now one query variant's
already-merged (vector+fulltext) result, weights[g] is the per-query-
variant trust weight. Removed contradictory 'vector_weight / fulltext_weight'
docstring line.
"
```

- [ ] **Step 6: 提交后 cross-check 调用点**

确认 `subgraph.py` / `orchestrator.py`(Task 14,后续 task)调用 `intra_fusion` 时传入 `query_groups: list[list[ScoredDocument]]` 而非旧的 `(vector_hits, fulltext_hits)` 双参数。需要在 Task 14 实施时同步更新调用方签名,并按 P0-2 语义传入 per-query-variant 权重(由 task 12+ 召回层在 vector+fulltext 合并后给出的 query variant 信任度)。
