# Task 18: Eval L2 — Gold Set + Synthetic + Retrieval Metrics

> 源 spec: `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` §9.5 Eval 全栈 (L2 层: Gold Set + Synthetic + Retrieval Metrics) + §16 Gold Set 标注与维护
>
> Fixes applied:
> - (audit #1 P1-1) stub-first: 加 Step 0,`retrieval_metrics.py` / `synthetic.py` / `goldset.jsonl` 均有可 import 占位
> - (audit #2) RAGAS 指标补全提示: `run_ragas.py` 中除 faithfulness / answer_relevancy 外,加入 `context_entities_recall` + `noise_sensitivity` 字段预占位(Task 19 落地)
> - (subagent #4) `synthetic.py` 改用 `SyntheticQuestion` Pydantic schema 替代 `list[dict]`,字段: `question` / `relevant_chunk_id` / `irrelevant_chunk_ids` / `expected_entities` (为 entity-level recall 留口)
> - (subagent #5) `retrieval_metrics.py` 区分 `chunk_level_recall` (chunk UUID 匹配) 与 `entity_level_recall` (gold 中 expected entities 在召回 text 中命中率)

## Open P0s (2026-06-14 audit)

| P0 ID | 描述 | 文件:行 | 解决路径 |
|---|---|---|---|
| G-P0-1 | task18.md:3 引用 `2026-06-10-python-rag-pipeline.md:4384-4558`, plan 仅 505 行, 范围不存在; 真实 eval 章节在 spec file `2026-06-10-python-rag-pipeline-design.md:1307-1397` (§9.5.2/§9.5.3/§9.6) + `:1609-1652` (§16 Gold Set) | task18.md:3 | M4 (5h) — 改 spec 引用为文件名+章节标题, plan tree 用 `2026-06-10-python-rag-pipeline.md:155-160` |
| G-P0-2 | `goldset.jsonl` stub (line 58-61) 与 "real" (line 319-322) 都 `relevant_chunk_ids: []` + `irrelevant_chunk_ids: []`, 无 chunk-level ground truth, `chunk_level_recall` 无法端到端验证, spec §16.1 50-100 条目标不可达 | task18.md:58-61, 319-322 | M4 (5h) — Option A: 从 `tests/integration/test_ingest_e2e.py` fixture 跑出 2-3 条真实 chunk UUID 填入 goldset; 其余 47-97 条标 TODO 留 follow-up |
| G-P0-3 | `EvalRunner.ainvoke({"query": ..., "dataset_ids": []})` 假设 pipeline 接口 + `.citations` 字段, 但 pipeline (task 14/16) 与 cite (task 15) 都没落地; 60 行 orchestrator 类无测试, 端到端首次跑会撞 3 个未实现依赖 | task18.md:201-243 | M4 (5h) — Option A: 改 `EvalRunner(pipeline, goldset_path, search_fn: Callable[[str], Awaitable[list[ScoredDocument]]])` 注入 search_fn, 加 `test_eval_runner_smoke` 用 AsyncMock |

详细分析见 `audit/2026-06-14-task18-alignment.md` §5 (修复建议)。

**Files:**
- Create: `tests/eval/__init__.py`
- Create: `tests/eval/goldset.jsonl`
- Create: `tests/eval/retrieval_metrics.py`
- Create: `tests/eval/synthetic.py`
- Create: `tests/integration/test_eval_l2.py`

- [ ] **Step 0: 写 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# tests/eval/__init__.py
"""Eval utilities — stub 包初始化。"""
```

```python
# tests/eval/retrieval_metrics.py (stub)
def recall_at_k(hits, relevant, k):
    return 0.0
def precision_at_k(hits, relevant, k):
    return 0.0
def mrr(hits, relevant):
    return 0.0
def ndcg_at_k(hits, relevance, k):
    return 0.0
def hit_rate(hits, relevant):
    return 0.0
# subagent #5 stub: 区分 chunk-level 与 entity-level
def chunk_level_recall(hits, relevant_chunk_ids, k):
    return 0.0
def entity_level_recall(hits, expected_entities, k):
    return 0.0
```

```python
# tests/eval/synthetic.py (stub)
from pydantic import BaseModel

class SyntheticQuestion(BaseModel):
    question: str = ""
    relevant_chunk_id: str = ""
    irrelevant_chunk_ids: list[str] = []
    expected_entities: list[str] = []    # subagent #4 增字段

async def gen_synthetic_queries(chunks, llm, n: int = 50):
    return []
```

```jsonl
# tests/eval/goldset.jsonl (stub — 至少 1 行保证 jsonl 可解析)
{"id": "g-stub", "query": "", "relevant_chunk_ids": [], "irrelevant_chunk_ids": [], "ground_truth_answer": "", "tags": [], "difficulty": "easy", "created_at": "2026-06-10", "annotated_by": "nathan"}
```

- [ ] **Step 1: 写失败测试 (retrieval metrics + subagent #5: chunk/entity 双层)**

```python
# tests/integration/test_eval_l2.py
import pytest
from tests.eval.retrieval_metrics import (
    recall_at_k, precision_at_k, mrr, ndcg_at_k, hit_rate,
    chunk_level_recall, entity_level_recall,    # subagent #5 新增
)
from tests.eval.synthetic import SyntheticQuestion, gen_synthetic_queries   # subagent #4 Pydantic

def test_recall_at_k():
    hits = [1, 3, 5]
    relevant = {1, 2, 3}
    assert abs(recall_at_k(hits, relevant, k=3) - 2/3) < 1e-6

def test_precision_at_k():
    hits = [1, 3, 5, 7]
    relevant = {1, 3}
    assert abs(precision_at_k(hits, relevant, k=4) - 0.5) < 1e-6

def test_mrr():
    assert mrr([1, 2, 3], {2}) == 0.5
    assert mrr([1, 2, 3], {1}) == 1.0
    assert mrr([1, 2, 3], {99}) == 0.0

def test_ndcg_at_k():
    hits = [1, 2, 3]
    rels = {1: 1.0, 2: 0.5, 3: 0.0}
    assert ndcg_at_k(hits, rels, k=3) > 0.9

def test_hit_rate():
    assert hit_rate([1, 2, 3], {3}) == 1.0
    assert hit_rate([1, 2, 3], {4}) == 0.0

# ── subagent #5: chunk vs entity 双层 ──────────────────

def test_chunk_level_recall_matches_uuid_set():
    """chunk-level: 以 chunk_id 集合重合度计算。"""
    hits = ["c-1", "c-2", "c-3"]
    relevant = {"c-1", "c-4"}
    assert abs(chunk_level_recall(hits, relevant, k=3) - 0.5) < 1e-6

def test_entity_level_recall_substring_match():
    """entity-level: 期望 entity 在召回 text 中出现即视为命中。"""
    hits_text = ["Python 是一种编程语言", "Java 也用于后端"]
    expected = {"Python", "Rust"}   # "Rust" 没出现
    score = entity_level_recall(hits_text, expected, k=2)
    assert 0.0 < score < 1.0   # "Python" 命中, "Rust" 未命中
    assert abs(score - 0.5) < 1e-6

def test_entity_level_recall_no_entities_returns_zero():
    assert entity_level_recall(["any text"], set(), k=5) == 0.0

# ── subagent #4: SyntheticQuestion Pydantic schema ──────

def test_synthetic_question_defaults():
    q = SyntheticQuestion(
        question="什么是 RRF?",
        relevant_chunk_id="c-uuid-1",
    )
    assert q.irrelevant_chunk_ids == []
    assert q.expected_entities == []

def test_synthetic_question_with_entities():
    q = SyntheticQuestion(
        question="RRF 用在哪个场景?",
        relevant_chunk_id="c-uuid-1",
        expected_entities=["RRF", "vector search", "fulltext search"],
    )
    assert "RRF" in q.expected_entities
```

- [ ] **Step 2: 跑测试,确认 fail (stub 返回 0/空,断言失败 — 非 ImportError)**

```bash
uv run pytest tests/integration/test_eval_l2.py -v
# 期望: 至少 5 failed (stub 全部返回 0.0, 与非零断言冲突)
```

- [ ] **Step 3: 写 retrieval_metrics.py (含 subagent #5 双层 recall)**

```python
# tests/eval/retrieval_metrics.py
import math
import re

# ── 基础 L2 指标 ─────────────────────────────────────────

def recall_at_k(hits: list, relevant: set, k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(hits[:k]) & relevant) / len(relevant)

def precision_at_k(hits: list, relevant: set, k: int) -> float:
    if k == 0:
        return 0.0
    return len(set(hits[:k]) & relevant) / k

def mrr(hits: list, relevant: set) -> float:
    for i, h in enumerate(hits, 1):
        if h in relevant:
            return 1.0 / i
    return 0.0

def ndcg_at_k(hits: list, relevance: dict, k: int) -> float:
    dcg = sum(relevance.get(h, 0) / math.log2(i + 2) for i, h in enumerate(hits[:k]))
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum(r / math.log2(i + 2) for i, r in enumerate(ideal))
    return dcg / idcg if idcg > 0 else 0.0

def hit_rate(hits: list, relevant: set) -> float:
    return 1.0 if any(h in relevant for h in hits) else 0.0

# ── subagent #5: chunk-level vs entity-level 区分 ──────

def chunk_level_recall(hits: list[str], relevant_chunk_ids: set[str], k: int) -> float:
    """Chunk-level recall: 以 chunk UUID 集合重合度计算,衡量召回系统的'段召回'能力。"""
    if not relevant_chunk_ids:
        return 0.0
    hit_set = {str(h) for h in hits[:k]}
    return len(hit_set & relevant_chunk_ids) / len(relevant_chunk_ids)

def entity_level_recall(hits_text: list[str], expected_entities: set[str], k: int) -> float:
    """Entity-level recall: 期望 entity 在召回 text 中出现即视为命中,衡量'关键信息点'召回。

    与 chunk-level 的区别: chunk-level 评估"是否召回了正确的段",entity-level 评估"段里关键实体是否被检索到"。
    用于发现"召回了对的段但 rerank 把关键 entity 排序靠后"或"召回了邻段"的问题。
    """
    if not expected_entities:
        return 0.0
    corpus = " ".join(hits_text[:k])
    corpus_norm = corpus.lower()
    hit = sum(1 for ent in expected_entities if ent.lower() in corpus_norm)
    return hit / len(expected_entities)

# ── H9: L2 批量评估 orchestrator ────────────────────────

class EvalRunner:
    """批量 L2 评估: gold set → pipeline → metrics → aggregate report。"""

    def __init__(self, pipeline, goldset_path: str):
        import json
        from pathlib import Path
        self.pipeline = pipeline
        self.rows = [json.loads(l) for l in Path(goldset_path).read_text().splitlines() if l.strip()]

    async def run(self) -> dict:
        all_metrics = []
        for r in self.rows:
            result = await self.pipeline.ainvoke({"query": r["query"], "dataset_ids": []})
            hit_ids = [str(c.chunk_id) for c in result.citations]
            hit_texts = [c.content for c in result.citations]
            relevant = set(r.get("relevant_chunk_ids", []))
            irrelev = set(r.get("irrelevant_chunk_ids", []))
            entities = set(r.get("expected_entities", []))
            k = r.get("top_k", 10)
            all_metrics.append({
                "query": r["query"],
                "recall@k": recall_at_k(hit_ids, relevant, k),
                "precision@k": precision_at_k(hit_ids, relevant, k),
                "mrr": mrr(hit_ids, relevant),
                "hit_rate": hit_rate(hit_ids, relevant),
                # subagent #5: 双层 recall
                "chunk_level_recall@k": chunk_level_recall(hit_ids, relevant, k),
                "entity_level_recall@k": entity_level_recall(hit_texts, entities, k),
            })

        n = len(all_metrics)
        return {
            "n_queries": n,
            "mean_recall@k": sum(m["recall@k"] for m in all_metrics) / n if n else 0,
            "mean_precision@k": sum(m["precision@k"] for m in all_metrics) / n if n else 0,
            "mean_mrr": sum(m["mrr"] for m in all_metrics) / n if n else 0,
            "hit_rate": sum(m["hit_rate"] for m in all_metrics) / n if n else 0,
            # subagent #5
            "mean_chunk_level_recall@k": sum(m["chunk_level_recall@k"] for m in all_metrics) / n if n else 0,
            "mean_entity_level_recall@k": sum(m["entity_level_recall@k"] for m in all_metrics) / n if n else 0,
            "per_query": all_metrics,
        }
```

- [ ] **Step 4: 写 synthetic.py (subagent #4 修正: 完整 Pydantic schema + entity 字段)**

```python
# tests/eval/synthetic.py
import json
import random
import uuid
from pathlib import Path
from pydantic import BaseModel, Field

# ── subagent #4: Pydantic schema 替代 list[dict] ────────

class SyntheticQuestion(BaseModel):
    """LLM 生成的合成评测 query。

    字段:
      question: 自然语言问题
      relevant_chunk_id: 命中的 chunk UUID (用于 chunk-level recall)
      irrelevant_chunk_ids: hard negatives (用于 precision 评估)
      expected_entities: 期望出现的关键实体 (用于 entity-level recall, subagent #5)
    """
    question: str
    relevant_chunk_id: str
    irrelevant_chunk_ids: list[str] = Field(default_factory=list)
    expected_entities: list[str] = Field(default_factory=list)

async def gen_synthetic_queries(
    chunks: list, llm, n: int = 50,
) -> list[SyntheticQuestion]:
    """用 LLM 为随机 chunk 生成 question + 提取 expected entities, 作为 eval 数据。

    prompt 包含两段指令:
      1. 基于 chunk text 生成能用自然语言查询命中该段落的问题
      2. 列出该段落中 3-5 个最关键的实体/术语
    """
    if not chunks:
        return []
    sample = random.sample(chunks, min(n, len(chunks)))
    results: list[SyntheticQuestion] = []
    for chunk in sample:
        prompt = (
            "基于以下段落, 完成两件事:\n"
            "1. 生成一个能用自然语言查询命中该段落的问题\n"
            "2. 列出段落中 3-5 个最关键的实体/术语 (逗号分隔)\n"
            "输出 JSON: {\"question\": \"...\", \"entities\": [\"...\"]}\n\n"
            f"段落:\n{chunk.text[:500]}"
        )
        try:
            r = await llm.ainvoke(prompt)
            content = r.content if hasattr(r, "content") else str(r)
            # 解析 LLM JSON 输出 (宽松: 抽取 {...} 块)
            import re
            m = re.search(r"\{[\s\S]*\}", content)
            if m:
                data = json.loads(m.group(0))
                question = data.get("question", "").strip()
                entities = data.get("entities", [])
            else:
                # 回退: 整段视作 question, 实体留空
                question, entities = content.strip(), []
            if not question:
                continue
            results.append(SyntheticQuestion(
                question=question,
                relevant_chunk_id=str(chunk.id),
                expected_entities=[str(e) for e in entities if e],
            ))
        except Exception:
            continue
    return results
```

- [ ] **Step 5: 写 goldset.jsonl (M6 修正: 与 spec §16.1 格式对齐; subagent #5 加 expected_entities 字段)**

```jsonl
{"id": "g-001", "query": "什么是 RRF?", "relevant_chunk_ids": [], "irrelevant_chunk_ids": [], "ground_truth_answer": "", "expected_entities": ["RRF", "倒数排名融合"], "tags": ["concept"], "difficulty": "easy", "created_at": "2026-06-10", "annotated_by": "nathan"}
{"id": "g-002", "query": "pgvector HNSW 索引参数如何选?", "relevant_chunk_ids": [], "irrelevant_chunk_ids": [], "ground_truth_answer": "", "expected_entities": ["pgvector", "HNSW", "m", "ef_construction"], "tags": ["vector"], "difficulty": "medium", "created_at": "2026-06-10", "annotated_by": "nathan"}
```

- [ ] **Step 6: 跑测试**

```bash
uv run pytest tests/integration/test_eval_l2.py -v
# 期望: 10 passed (5 基础 + 2 chunk/entity + 1 entity empty + 2 synthetic pydantic)
```

- [ ] **Step 7: commit**

```bash
git add tests/eval tests/integration/test_eval_l2.py
git commit -m "feat(eval): L2 retrieval metrics + chunk/entity-level recall + SyntheticQuestion schema"
```

---

## 与 Task 19 (RAGAS) 衔接 (audit #2)

RAGAS 指标完整集合(spec §9.6 + audit #2 补全),由 Task 19 `run_ragas.py` 落地:

| 指标 | 维度 | 接入方式 |
|------|------|----------|
| `faithfulness` | LLM-judge | RAGAS 内置 |
| `answer_relevancy` | embedding similarity | RAGAS 内置 |
| `context_precision` | retrieval 排序 | RAGAS 内置 |
| `context_recall` | gold answer 覆盖率 | RAGAS 内置 |
| **`context_entities_recall`** (audit #2 补) | 实体级 | 复用 `entity_level_recall`, 接收 `expected_entities` |
| **`noise_sensitivity`** (audit #2 补) | hard negative 鲁棒性 | 利用 `irrelevant_chunk_ids`, 检查 retrieved 占比 |

Task 19 实现 `run_ragas.py` 时,从 `goldset.jsonl.expected_entities` 与 `irrelevant_chunk_ids` 字段读取并填入 RAGAS `EvaluationDataset`。

---

## 修复摘要 (Fix Manifest)

| 来源 | 位置 | 修改 |
|------|------|------|
| audit #1 P1-1 | Step 0 | 新增 stub (retrieval_metrics / synthetic / goldset), RED 阶段不因 ImportError 崩溃 |
| audit #2 | 衔接说明 | RAGAS 指标清单补 `context_entities_recall` + `noise_sensitivity`, Task 19 落地 |
| subagent #4 | `synthetic.py` | `SyntheticQuestion` Pydantic schema (替代 list[dict]), 加 `expected_entities` 字段 |
| subagent #5 | `retrieval_metrics.py` | 区分 `chunk_level_recall` (UUID 集合) 与 `entity_level_recall` (text 实体命中) |
| subagent #5 | `goldset.jsonl` | 增加 `expected_entities` 字段供 entity-level recall 使用 |
| subagent #5 | `EvalRunner.run` | 汇总 `mean_chunk_level_recall@k` + `mean_entity_level_recall@k` |
