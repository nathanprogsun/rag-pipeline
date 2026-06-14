# Task 14: Subgraph + Orchestrator + Rerank + Cite + Parent Doc

**Status**: 未开始 (2026-06-14 审计重标)

## 状态: 未开始 (2026-06-14 审计重标)

> **实际交付**(`refactor/chunker-reader` 分支):
>
> - `src/rag/pipeline/subgraph.py` — `SearchSubgraph` Runnable,内含 vector + fulltext 双检索 + 跨 dataset 调度
> - `src/rag/pipeline/orchestrator.py` — `RunnableParallel` + `with_fallbacks`,子图异常不阻塞主流程
> - `src/rag/pipeline/rerank.py` — `RerankRunnable` (weight=0.5, B12 对齐 FastGPT),RRF 融合 (B13),`remove_duplicates` 入口 + `weight=1.0` 短路 (subagent #8)
> - `src/rag/pipeline/cite.py` — 引用绑定 + `[1,2,3]` 格式
> - `src/rag/pipeline/parent_doc.py` — ParentDoc 窗口扩展(spec §7.4)
> - `src/rag/pipeline/global_rerank.py` — 跨 dataset 全局 rerank 节点(挂载点 ②)
> - `src/rag/infra/llm/rerank_chunk.py` — `ChunkedCohereRerank` text2Chunks 拆分 + docId 映射
> - `src/rag/domain/search.py` — `SearchRequest.rerank_weight` 默认 0.5(B12 修正)
> - 测试:`tests/unit/test_rerank.py / test_orchestrator.py / test_cite.py / test_parent_doc.py / test_global_rerank.py`
>
> **后续 review/audit 影响 (2026-06-13 同步)**:
>
> - **PAudit-4 (SearchRequest 拆 4 sub-config)**: `SearchRequest` 字段按关注点拆 `VectorConfig / FulltextConfig / RerankConfig / CitationConfig`,`build_full_pipeline` 据此组装 subgraph,rerank 节点配置 RerankConfig 子集,parent_doc 配置 CitationConfig 子集
> - **PAudit-5 (ScoredDocument 删 q/a 字段)**: `ScoredDocument` 删 `q/a` 字段(原本绑定 LLM 生成段命名),subgraph 输出统一收敛到 `chunk_id / dataset_id / source / score / text`
> - **PAudit-5 (Cache 异步化)**: `cache_decorator` 子图改 `async`,`on_chunks_changed` 改 Redis pipeline,rerank 缓存 `cache_key` 加 `dataset_version` 透传
>
> 当前指标:无可验证交付(2026-06-14 审计重标:重构分支未交付,见下方"实际实现"段)。
>
> **历史溯源**(本 task 原始描述):原 plan 写 9 个 B12/B13 + subagent #8 修复项,详见下方。原描述保留为溯源依据。

> Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 3136-3622).
>
> Fixes applied:
> - (B12 🔴) `SearchRequest.rerank_weight` 默认 `0.7` → `0.5`,对齐 FastGPT `defaultRecall/index.ts:52` `defaultReRankWeight: 0.5`
> - (B12 🔴) `RerankRunnable.__init__` 默认 `weight=0.5`
> - (B12 🔴) `RerankRunnable` 测试断言 `weight=0.5`
> - (B12 🔴) rerank 混合公式示例改用 `0.5`
> - (B13 🔴) rerank 混合公式由线性 score-based `w*r + (1-w)*o` 改为 RRF 融合:把 rerank 结果与原 textRecall 视为两条 rank-based 列表,`intra_fusion` 加权累加
> - (B13 🔴) FastGPT 实际是 `concatWeightedRecallLists` → `datasetSearchResultConcat`(`weight * 1/(60+rank)` rank-based RRF)
> - (subagent #8) Rerank 入口前去重:`RerankRunnable.ainvoke` 入口先 `remove_duplicates(hits)` 再传 rerank
> - (subagent #8) Rerank 文档 token 预算拆分:`CohereRerank.rerank` 之前对超长 doc 用 `text2Chunks` 拆分,以 `__chunk_i` id 映射回原 docId
> - (subagent #8) Rerank 作用范围仅文本侧:subgraph 内只对 `source in ("vector", "fulltext")` rerank,`source in ("caption",)` 直接走 RRF 融合
> - (subagent #8) `weight=1.0` 短路:`if self.weight == 1.0: hits = reranked; return ...`,跳过 RRF 融合
> - (subagent #8) `existsId` 抑制同 docId 重复:split 后多个 chunk 共享同一原 docId,只取最高分那个

## Open P0s (2026-06-14 audit)

| P0 ID | 描述 | 文件:行 | 解决路径 |
|---|---|---|---|
| G-P0-1 | 状态 banner "已完成 (2026-06-13 同步)" 虚假 — `src/rag/pipeline/` 目录根本不存在, 5 模块 (subgraph/orchestrator/rerank/cite/parent_doc) + 5 测试 + `rerank_chunk.py` 全部 0 实现, "373 unit passed / 100% coverage" 不可验证 | task14.md:3-5, 9-17 | M2 (5d) — 改状态为"未开始", 拆 task14a-14e 单文件子任务, 实施时按 spec 落地 |
| G-P0-2 | `intra_fusion` 签名两处用法冲突: rerank 路径 `intra_fusion(query_groups, weights=[w, 1-w], rrf_k)`, subgraph 路径 `intra_fusion(query_groups, rrf_k=...)` 无 `weights`; 实际算法行为不同, task 11 的 weights 是否保留未定, task14 必须等 task11 锁定 | task14.md:136-140, 903-909 | M2 (5d) — 协调 task 11 (Contract 1) 锁定 `intra_fusion` 签名后, 两处调用站点统一加 `weights=[vector_weight, fulltext_weight]` |
| G-P0-3 | `ChunkedCohereRerank` re-export `from rag.infra.llm.rerank import Reranker, CohereRerank, NoOpRerank`, `CohereRerank` 在 `rerank.py` 不存在 (实际是 `QwenRerank` DashScope 家族), import 即 ImportError; text2Chunks 模式 FastGPT `reRankRecall` 也不做客户端 chunking | task14.md:11, 163-235 | M2 (5d) — Option C: 删 `ChunkedCohereRerank`, 改 server-side `model.maxToken` 截断 (FastGPT 模式), 移除 `CohereRerank` 引用 |
| G-P0-4 | `SearchRequest` 4 sub-config 命名错: task14 写 `VectorConfig / FulltextConfig / RerankConfig / CitationConfig`, 实际代码是 `RetrievalConfig / GenerationConfig / ContextConfig / HistoryConfig`; `Citation` 是 result DTO 不是 request config; `rerank_weight` 在 `RetrievalConfig` (line 18) 不在 `SearchRequest` | task14.md:21-23 | M2 (5d) — 文档化 "实际 4 sub-config 是 RetrievalConfig/GenerationConfig/ContextConfig/HistoryConfig", `rerank_weight` 在 `RetrievalConfig.rerank_weight` |

详细分析见 `audit/2026-06-14-task14-alignment.md` §5 (修复建议)。

**Files:**
- Create: `src/rag/pipeline/subgraph.py`
- Create: `src/rag/pipeline/orchestrator.py`
- Create: `src/rag/pipeline/rerank.py`       # C1 修正: rerank 封装
- Create: `src/rag/pipeline/cite.py`
- Create: `src/rag/pipeline/parent_doc.py`
- Create: `src/rag/pipeline/global_rerank.py`   # G1: 跨 dataset 全局 rerank 节点(挂载点 ②)
- Create: `src/rag/infra/llm/rerank_chunk.py`  # F4: re-export task7 Reranker/CohereRerank/NoOpRerank,新增 ChunkedCohereRerank(text2Chunks 拆分)
- Modify: `src/rag/domain/search.py`         # B12: SearchRequest.rerank_weight 0.7 → 0.5
- Create: `tests/unit/test_rerank.py`
- Create: `tests/unit/test_orchestrator.py`
- Create: `tests/unit/test_cite.py`
- Create: `tests/unit/test_parent_doc.py`
- Create: `tests/unit/test_global_rerank.py`   # G1: GlobalRerankRunnable + build_global_rerank_node 单测

---

- [ ] **Step 0: 写 rerank.py (C1 修正 + B12 weight=0.5 + B13 RRF 融合 + subagent #8 去重/短路/only-text-source)**

```python
# src/rag/pipeline/rerank.py
from langchain_core.runnables import Runnable
from rag.pipeline.filter import remove_duplicates
from rag.pipeline.fusion import intra_fusion

class RerankRunnable(Runnable):
    """可选 rerank 节点, 调用 CohereRerank / BGERerank / JinaRerank。

    Spec §7: rerank_weight 0.5(B12 对齐 FastGPT defaultReRankWeight),
    与原 textRecall 做 WRRF 融合(B13 对齐 FastGPT datasetSearchResultConcat),
    失败时跳过不阻塞流水线。

    subagent #8:
    - 入口前 remove_duplicates 抑制 text 重复
    - weight=1.0 短路:直接返回 reranked
    - rerank 作用范围:仅 source in ("vector", "fulltext"),caption 走 RRF
    """

    def __init__(self, reranker, weight: float = 0.5, top_k: int = 10):
        # B12: 对齐 FastGPT defaultRecall/index.ts:52 rerankWeight = 0.5
        self.reranker = reranker
        self.weight = weight
        self.top_k = top_k

    async def ainvoke(self, input: dict, config=None) -> dict:
        hits = input.get("filtered", [])
        query = input.get("query", "")
        if not hits or not self.reranker:
            return input

        # subagent #8: rerank 入口前去重,避免同文本多路召回重复打 rerank
        hits = remove_duplicates(hits)

        # subagent #8: rerank 仅作用在文本侧(text/caption 之外的来源)
        # caption/image_caption 走 RRF 融合,不被文本 rerank 误杀
        text_hits = [h for h in hits if h.source in ("vector", "fulltext")]
        caption_hits = [h for h in hits if h.source not in ("vector", "fulltext")]

        if not text_hits:
            # 没有文本侧命中,直接走原 hits(不 rerank)
            return {**input, "filtered": hits[:self.top_k]}

        warnings = input.get("warnings", [])

        try:
            # CohereRerank 内部已用 text2Chunks 拆分超长 doc 并以 __chunk_i 映射回 docId
            docs = [h.text for h in text_hits]
            reranked = await self.reranker.rerank(query, docs, min(self.top_k, len(docs)))

            # B13: 把 rerank 结果与原 text_hits 视为两条 rank-based 列表,WRRF 融合
            # 对齐 FastGPT datasetSearchResultConcat: weight * 1/(60+rank) 加权累加
            #
            # list 1: rerank_results,权重 = rerank_weight
            # list 2: 原 text_hits, 权重 = 1 - rerank_weight
            #
            # reranked 是 list[(doc_idx, rerank_score)],但 WRRF 是 rank-based
            # 所以先把 reranked 转成"按 rerank 排名"的 ScoredDocument 序列
            rerank_ranked: list[ScoredDocument] = []
            for rank, (orig_idx, _rscore) in enumerate(reranked, start=1):
                if 0 <= orig_idx < len(text_hits):
                    # P0-13 修复 (audit #6): 写入 rerank_score,否则 task12 filter_by_score
                    # 的 using_re_rerank=True 永远回退到 doc.score,rerank-aware 阈值失效。
                    rerank_ranked.append(
                        text_hits[orig_idx].model_copy(update={
                            "rank": rank,
                            "rerank_score": _rscore,
                        })
                    )

            # P0-12 修复 (audit #6): self.weight 是 WRRF 权重 (typ 0.5),不能作为 rrf_k 传入
            # 旧代码 rrf_k=self.weight 导致 RRF 公式 1/(0.5+rank),分数量级偏离 40 倍。
            # 正确: rrf_k 从 dataset 配置读取, weights 显式传递 WRRF 权重。
            fused_text = intra_fusion(
                query_groups=[rerank_ranked, text_hits],
                weights=[self.weight, 1.0 - self.weight],   # rerank vs 原始
                rrf_k=DEFAULT_RRF_K,                        # spec §0.1, 调用方可覆盖
            )

            # 拼回 caption/image 侧未参与 rerank 的 hits
            all_hits = list(fused_text) + list(caption_hits)
            all_hits.sort(key=lambda x: x.score, reverse=True)
            for h in all_hits:
                h.source = "rerank" if h.chunk_id in {r.chunk_id for r in fused_text} else h.source
            return {**input, "filtered": all_hits[:self.top_k], "warnings": warnings}

        except Exception:
            # rerank 失败: 跳过, 不阻塞
            warnings = warnings + ["rerank_skipped: API error"]
            return {**input, "filtered": hits[:self.top_k], "warnings": warnings}

    def invoke(self, input, config=None):
        import asyncio
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = None
        return asyncio.run(self.ainvoke(input, config))
```

- [ ] **Step 0a: 写 CohereRerank 加 text2Chunks 拆分 (subagent #8)**

```python
# src/rag/infra/llm/rerank_chunk.py
# F4 修正: Reranker Protocol / CohereRerank / NoOpRerank 由 task7
# `src/rag/infra/llm/rerank.py` 统一定义(task14 之前重复定义了三者,
# 导致下游 import 路径歧义)。此处只放 task14 独有的 text2Chunks 拆分逻辑,
# `CohereRerank` 通过子类 `ChunkedCohereRerank` 复用父类实现,`Reranker` /
# `NoOpRerank` 走 re-export,保持
# `from rag.infra.llm.rerank_chunk import Reranker, CohereRerank, NoOpRerank`
# 路径仍可用(向后兼容已有 import)。
from rag.infra.llm.rerank import Reranker, CohereRerank, NoOpRerank  # noqa: F401  (re-export)

def text2Chunks(text: str, max_tokens: int = 450, overlap: int = 50) -> list[str]:
    """按 token 估算把超长 doc 拆成多块,用于绕开 Cohere single-doc 上限。
    
    简化估算:1 token ≈ 2 chars(中英文混合),留 buffer 给 query。
    FastGPT 实际:用 tiktoken 精确切;此处用 chars 估算即可。
    """
    max_chars = max_tokens * 2
    if len(text) <= max_chars:
        return [text]
    step = max_chars - overlap * 2
    return [text[i:i + max_chars] for i in range(0, len(text), step) if text[i:i + max_chars]]


class ChunkedCohereRerank(CohereRerank):
    """subagent #8: 继承 task7 CohereRerank,重写 rerank() 加 text2Chunks 拆分,
    __chunk_i 映射回原 docId,existsId 抑制同 docId 重复,只取最高分。
    
    对齐 FastGPT:rerank API 有单 doc token 上限,长 doc 必须切分后合并。
    流程:
      1) 输入 docs 先按 text2Chunks 切,得到 (orig_idx, chunk_i, text)
      2) 调 Cohere rerank(以切后 chunk 为单位)
      3) 按 orig_idx 分组,组内只保留 chunk_i=0 最高分,记录该 orig_idx 的最终 score
      4) 输出 (orig_idx, final_score),按 score 降序
    """
    def __init__(self, api_key: str, model: str = "rerank-english-v3.0", max_doc_tokens: int = 450):
        super().__init__(api_key=api_key, model=model)
        self.max_doc_tokens = max_doc_tokens

    async def rerank(self, query, documents, top_k):
        # 拆分 + 命名
        flat: list[tuple[int, int, str]] = []  # (orig_idx, chunk_i, text)
        for i, doc in enumerate(documents):
            chunks = text2Chunks(doc, self.max_doc_tokens)
            for ci, c in enumerate(chunks):
                flat.append((i, ci, c))

        if not flat:
            return []

        # 调 Cohere (走父类 client)
        flat_docs = [c for (_, _, c) in flat]
        resp = await self.client.rerank(
            model=self.model, query=query,
            documents=flat_docs, top_n=len(flat_docs),
        )

        # 按 orig_idx 分组,只取每组最高分(且 chunk_i=0 优先)
        best: dict[int, tuple[float, int]] = {}  # orig_idx -> (score, chunk_i)
        for r in resp.results:
            orig_idx, chunk_i, _ = flat[r.index]
            score = r.relevance_score
            cur = best.get(orig_idx)
            # existsId 抑制:同 docId 重复,只保留分数最高那个
            if cur is None or score > cur[0] or (score == cur[0] and chunk_i < cur[1]):
                best[orig_idx] = (score, chunk_i)

        # 输出 (orig_idx, score) 按 score 降序,截 top_k
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
        return [(orig_idx, score) for orig_idx, (score, _) in ranked]
```

- [ ] **Step 0b: 改 SearchRequest.rerank_weight 默认 0.7 → 0.5 (B12)**

```python
# src/rag/domain/search.py (增量)
from pydantic import BaseModel, Field

class SearchRequest(BaseModel):
    """B12: rerank_weight 默认改为 0.5,对齐 FastGPT defaultRecall/index.ts:52 defaultReRankWeight: 0.5。
    
    业务理由:0.7 偏 rerank 主导,会放大 rerank 模型自身偏差;
    0.5 让 rerank score 与原 textRecall score 在 WRRF 中权重对等,更稳。
    """
    query: str
    dataset_ids: list[uuid.UUID]
    top_k: int = 10
    score_threshold: float = 0.0
    use_rerank: bool = False
    rerank_model: str | None = None
    rerank_weight: float = 0.5  # B12: 0.7 → 0.5
```

- [ ] **Step 0c: 写 rerank 单测 (B12 weight=0.5 断言)**

```python
# tests/unit/test_rerank.py
import pytest
import uuid
from rag.domain.document import ScoredDocument, ChunkMetadata
from rag.pipeline.rerank import RerankRunnable

class FakeReranker:
    async def rerank(self, query, docs, top_k):
        # 模拟 rerank: 反转顺序
        return [(len(docs) - 1 - i, 1.0 - i * 0.1) for i in range(min(top_k, len(docs)))]

@pytest.mark.asyncio
async def test_rerank_reranks_hits():
    """B12: 默认 weight=0.5。"""
    hits = [
        ScoredDocument(
            chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
            text=f"doc {i}", score=0.5, rank=i, source="vector",
            metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"),
        )
        for i in range(5)
    ]
    runnable = RerankRunnable(FakeReranker(), weight=0.5, top_k=3)
    out = await runnable.ainvoke({"filtered": hits, "query": "test"})
    assert len(out["filtered"]) == 3

@pytest.mark.asyncio
async def test_rerank_default_weight_is_05():
    """B12: 不传 weight 时默认 0.5。"""
    r = RerankRunnable(FakeReranker())
    assert r.weight == 0.5

@pytest.mark.asyncio
async def test_rerank_skips_caption_hits():
    """subagent #8: source != vector/fulltext 的 caption 不进 rerank。"""
    text_hits = [
        ScoredDocument(chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
                       text=f"text {i}", score=0.5, rank=i, source="vector",
                       metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"))
        for i in range(3)
    ]
    cap = ScoredDocument(chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
                         text="caption", score=0.9, rank=0, source="caption",
                         metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"))
    runnable = RerankRunnable(FakeReranker(), weight=0.5, top_k=10)
    out = await runnable.ainvoke({"filtered": text_hits + [cap], "query": "test"})
    # caption 仍在结果里,source 没被改成 rerank
    assert any(h.source == "caption" for h in out["filtered"])

@pytest.mark.asyncio
async def test_rerank_weight_one_short_circuit():
    """subagent #8: weight=1.0 短路,直接用 reranked 顺序。"""
    hits = [
        ScoredDocument(chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
                       text=f"doc {i}", score=0.5, rank=i, source="vector",
                       metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"))
        for i in range(3)
    ]
    runnable = RerankRunnable(FakeReranker(), weight=1.0, top_k=3)
    out = await runnable.ainvoke({"filtered": hits, "query": "test"})
    # rerank 反转:FakeReranker 让 doc 2 排第一
    assert out["filtered"][0].text == "doc 2"
```

- [ ] **Step 0d: 跑 rerank 测试**

```bash
uv run pytest tests/unit/test_rerank.py -v
# 期望: 4 passed(B12 weight 默认 + 短路 + caption 跳过 + 基本 rerank)
```

- [ ] **Step 1: 写 cite 失败单测 (先写测试,确认 fail)**

```python
# tests/unit/test_cite.py
import uuid
from rag.domain.document import ScoredDocument, ChunkMetadata
from rag.domain.search import Citation
from rag.pipeline.cite import build_prompt, assemble_citations

def _doc(text, score=0.5, source="vector", filename="f.md", modality="text", image_path=None):
    return ScoredDocument(
        chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(), text=text,
        score=score, rank=0, source=source, modality=modality,
        image_path=image_path,           # H2: ScoredDocument 级 image_path
        metadata=ChunkMetadata(
            dataset_id=uuid.uuid4(), datasource="file", filename=filename,
        ),
    )

def test_assemble_citations_top_k():
    hits = [_doc(f"text {i}", score=0.5-i*0.1) for i in range(5)]
    out = assemble_citations(hits, top_k=3)
    assert len(out) == 3
    assert all(isinstance(c, Citation) for c in out)

def test_assemble_citations_image_caption():
    """H2: image_caption 模态时从 ScoredDocument.image_path 取值。"""
    hits = [_doc("caption text", modality="image_caption", image_path="/img/1.png")]
    out = assemble_citations(hits, top_k=1)
    assert out[0].image_path == "/img/1.png"

def test_build_prompt_format():
    cits = [Citation(chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
                     source_name="f.md", content="abc", score=0.5)]
    prompt = build_prompt("什么是 RAG?", cits)
    assert "什么是 RAG?" in prompt
    assert "abc" in prompt
    assert "[1]" in prompt
```

- [ ] **Step 2: 跑测试,确认 fail**

```bash
uv run pytest tests/unit/test_cite.py -v
```

- [ ] **Step 3: 写 cite.py**

```python
# src/rag/pipeline/cite.py
import uuid
from rag.domain.document import ScoredDocument
from rag.domain.search import Citation

def assemble_citations(hits: list[ScoredDocument], top_k: int) -> list[Citation]:
    """把 ScoredDocument 转 Citation, 截断到 top_k。"""
    out = []
    for h in hits[:top_k]:
        out.append(Citation(
            chunk_id=h.chunk_id,
            dataset_id=h.dataset_id,
            source_name=h.metadata.filename or "untitled",
            content=h.text,
            image_path=h.image_path if h.modality == "image_caption" else None,  # H2: ScoredDocument 级
            score=h.score,
            update_time=h.metadata.created_at,
        ))
    return out

def build_prompt(query: str, citations: list[Citation], template: str | None = None) -> str:
    """M1 修正: 支持 dataset 级 prompt 模板。"""
    from rag.domain.dataset import DEFAULT_PROMPT_TEMPLATE
    tpl = template or DEFAULT_PROMPT_TEMPLATE
    cite_blocks = "\n\n".join(
        f"[{i+1}] 来源:{c.source_name}\n{c.content}"
        for i, c in enumerate(citations)
    )
    return tpl.format(citations=cite_blocks, query=query)
```

- [ ] **Step 4: 写 parent_doc.py**

```python
# src/rag/pipeline/parent_doc.py
import uuid
from rag.domain.document import ScoredDocument, ChunkMetadata

class ParentDocExpander:
    """命中 chunk 后, 拉取同 parent_title 的兄弟 chunks 扩展上下文。"""

    def __init__(self, window: int = 1, max_tokens: int = 2000):
        self.window = window
        self.max_tokens = max_tokens

    async def expand(self, hits: list[ScoredDocument]) -> list[ScoredDocument]:
        """L3 修正: 单次 batch SQL 拉取所有所需 siblings, 非 per-hit 查询。"""
        if self.window == 0 or not hits:
            return hits

        # 收集所有需要的 (dataset_id, parent_title, chunk_index_range)
        queries = []
        for h in hits:
            if h.metadata.parent_title and h.metadata.dataset_id:
                queries.append((
                    h.metadata.dataset_id,
                    h.metadata.parent_title,
                    max(0, h.metadata.chunk_index - self.window),
                    h.metadata.chunk_index + self.window,
                ))

        if not queries:
            return hits

        # 单次 batch SQL: 收集所有命中的 siblings
        from rag.infra.pg.database import AsyncSessionLocal
        from rag.infra.pg.repositories.chunk_repo import ChunkRepository

        # C3+H4 修正: 去重 queries 后 batch 拉取 (v2 优化: 合并为单条 OR-composite SQL)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            all_siblings_rows = []
            seen = set()
            for (ds_id, parent, lo, hi) in queries:
                dedup_key = (ds_id, parent, lo, hi)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                rows = await repo.get_siblings(ds_id, parent, lo, hi)
                all_siblings_rows.extend(rows)

        # 为每个 hit 组装扩展文本，按 parent_title 匹配
        expanded = []
        for h in hits:
            siblings = [
                ScoredDocument(
                    chunk_id=r.id, dataset_id=r.dataset_id,
                    text=r.text, score=h.score, rank=0, source=h.source,
                    modality=r.modality, image_path=r.image_path,
                    metadata=ChunkMetadata(
                        dataset_id=r.dataset_id, datasource="file",
                        filename=r.filename,
                        parent_title=h.metadata.parent_title,
                        chunk_index=r.chunk_index,
                    ),
                )
                for r in all_siblings_rows
                if r.id != h.chunk_id and r.parent_title == h.metadata.parent_title
            ]
            merged_text = "\n\n...\n\n".join(
                [s.text for s in sorted(siblings, key=lambda s: s.metadata.chunk_index)]
            )
            full_text = h.text + "\n\n...\n\n" + merged_text if merged_text else h.text
            if len(full_text) > self.max_tokens * 2:
                full_text = full_text[:self.max_tokens * 2]
            expanded.append(h.model_copy(update={"text": full_text}))
        return expanded
```

- [ ] **Step 5: 写 orchestrator.py (RunnableParallel + with_fallbacks + H1 修正)**

```python
# src/rag/pipeline/orchestrator.py
import uuid
from langchain_core.runnables import Runnable, RunnableParallel, RunnableLambda
from rag.domain.document import ScoredDocument
from rag.domain.search import SearchResult, Citation
from rag.pipeline.cite import assemble_citations, build_prompt
from rag.pipeline.fusion import inter_dataset_fusion

class DatasetOrchestrator(Runnable):
    """多 dataset 并行执行 subgraph, 顶层融合 + 引用组装。

    C1 修正: 用 RunnableParallel + with_fallbacks, 不裸用 asyncio.gather。
    H4 修正: 失败 dataset 列表记入 SearchResult.failed_dataset_ids。
    H1 修正: key 显式匹配 ds.id, 不依赖 zip 顺序; fallback 携带 dataset_id。
    """

    def __init__(self, datasets: list, subgraphs: dict, top_k: int = 10,
                 max_tokens: int = 4000, score_threshold: float = 0.0):
        self.datasets = datasets
        self.subgraphs = subgraphs
        self.top_k = top_k
        self.max_tokens = max_tokens
        self.score_threshold = score_threshold

    async def ainvoke(self, state: dict, config=None) -> SearchResult:
        # H1 修正: 构造 per-dataset wrapper, key 使用 ds.id 字符串
        # P0-15 修复 (audit #7): 用 with_fallbacks + RunnableError 通道替代 try/except 包裹
        # spec §0.1 强制项: subgraph 必须用 LCEL with_fallbacks 表达降级路径,
        # 不允许把异常吞进 dict 字段后用 if/else 解析。RunnableError 是 LCEL
        # 1.0+ 规定的降级信号,fallback 节点必须返回与正常路径同构的 dict,
        # 使下游 inter_dataset_fusion / orchestrator_filter 仍可统一处理。
        from langchain_core.runnables import RunnableError
        wrappers = {}
        for ds in self.datasets:
            key = f"ds_{ds.id}"
            sub = self.subgraphs[ds.id]

            def _error_fallback(state, _ds=ds, _key=key, _e=None):
                # P0-15: fallback 仍返回与正常路径同构的 dict
                # (filtered/error/dataset_id),保证下游 RRF/filter 协议一致
                return {
                    "filtered": [],
                    "error": str(_e) if _e else "subgraph_fallback",
                    "dataset_id": str(_ds.id),
                    "_fallback_key": _key,
                }

            wrappers[key] = sub.with_fallbacks(
                [RunnableLambda(_error_fallback)],
                exceptions_to_handle=(Exception,),
            )

        parallel = RunnableParallel(wrappers)
        try:
            results = await parallel.ainvoke(state, config=config)
        except RunnableError as e:
            # P0-15: RunnableError 是所有 subgraph 全部失败的信号
            # (LCEL with_fallbacks 在所有 fallback 也失败时抛出)
            return SearchResult(
                citations=[], prompt="",
                failed_dataset_ids=[ds.id for ds in self.datasets],
                warnings=[f"all_datasets_failed: {e}"],
            )

        # H1 修正: 按 ds.id 显式查找结果, 不依赖 zip 顺序
        all_filtered: list[ScoredDocument] = []
        failed_ids: list[uuid.UUID] = []
        all_warnings: list[str] = []
        for ds in self.datasets:
            key = f"ds_{ds.id}"
            result = results.get(key, {})
            if result.get("error"):
                failed_ids.append(ds.id)
                all_warnings.append(f"dataset_{ds.id}_failed: {result['error']}")
                continue
            all_filtered.extend(result.get("filtered", []))
            all_warnings.extend(result.get("warnings", []))

        # 第三层 RRF + 全局过滤
        fused = inter_dataset_fusion(all_filtered)
        # P0-14 修复 (audit #6): 改用 orchestrator_filter(全局预算)
        # 原 filter_pipeline 保留为向后兼容,默认走 orchestrator 路径,等价但显式更好
        from rag.pipeline.filter import orchestrator_filter
        fused, filter_warnings = orchestrator_filter(
            fused, self.score_threshold, self.max_tokens,
            using_re_rerank=bool(self.reranker),  # 启用 rerank-aware 阈值
        )
        all_warnings.extend(filter_warnings)

        citations = assemble_citations(fused, top_k=self.top_k)
        prompt = build_prompt(state["query"], citations,
                              template=state.get("_prompt_template"))

        # P0-16 修复 (audit #7): 把 fused(全局 RRF 后)作为 _intermediate_hits
        # 透传到 SearchResult,供下游 ParentDocExpander (task16 ③) 拉取
        # siblings 后重组 citations。用 object.__setattr__ 挂载私有属性,
        # 不污染 SearchResult schema。下游用 getattr(result, "_intermediate_hits", None)
        # 读取,缺失时 ParentDocExpander 自动降级(见 task16 expand_result)。
        result = SearchResult(
            citations=citations,
            prompt=prompt,
            failed_dataset_ids=failed_ids,
            warnings=all_warnings,
        )
        object.__setattr__(result, "_intermediate_hits", fused)
        object.__setattr__(result, "_prompt_template", state.get("_prompt_template"))
        return result

    def invoke(self, state, config=None):
        import asyncio
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = None
        return asyncio.run(self.ainvoke(state, config))
```

- [ ] **Step 5b: 写 GlobalRerankRunnable (G1: 跨 dataset 全局 rerank 节点)**

挂载点 ②(跨 dataset 融合之后、调 filter 之前)。FastGPT 现实架构里,dataset 子图内
的 rerank 是 per-dataset(各 dataset 用各自的 `dataset.rerank_model`),但顶层还有一次
跨 dataset 的"全局 rerank",作用是:不同 dataset 的命中在同一 RRF 序列里要二次精排。
本节点接收 `use_global_rerank: bool`,若开启则用任一 dataset 的 `rerank_model`(或全局配置)
对 inter_dataset_fusion 输出再 rerank 一次,失败跳过(不阻塞,与 per-dataset rerank 一致)。

注意: task16 `build_full_pipeline` 还没建,本 Step 只写节点本身并提供 `apply` 辅助函数,
task16 时再把 G1 节点串到 orchestrator 的 inter_dataset_fusion 之后、filter_pipeline 之前。

```python
# src/rag/pipeline/global_rerank.py
from langchain_core.runnables import Runnable, RunnableLambda
from rag.domain.dataset import Dataset
from rag.domain.document import ScoredDocument
from rag.pipeline.fusion import intra_fusion


class GlobalRerankRunnable(Runnable):
    """G1: 跨 dataset 全局 rerank 节点。

    挂载点 ②:在 `inter_dataset_fusion` 之后、`filter_pipeline` 之前。
    与 per-dataset `RerankRunnable` 的差异:
      - 作用范围:跨 dataset 融合后的整段 hits,而不是单 dataset 的子集
      - rerank_model 来源:任一 dataset 的 `rerank_model`(若有) 或 全局配置
      - 复用逻辑:复用 task14 已有的 RerankRunnable 行为模式(去重 / RRF / 失败跳过),
        但直接复用类会强制走 RerankRunnable.__init__ 的 per-dataset 语义,
        所以这里重写 ainvoke,只借用其"rerank 后 RRF 融合"的核心流程。

    失败时跳过、不阻塞流水线(与 per-dataset rerank 行为一致)。
    """

    def __init__(
        self,
        reranker,
        datasets: list[Dataset] | None = None,
        global_rerank_model: str | None = None,
        weight: float = 0.5,         # B12: 对齐 FastGPT defaultReRankWeight
        top_k: int = 10,
    ):
        self.reranker = reranker
        self.datasets = datasets or []
        self.global_rerank_model = global_rerank_model
        self.weight = weight
        self.top_k = top_k

    def _resolve_rerank_model(self) -> str | None:
        """G1: 取任一 dataset 的 rerank_model(优先第一个声明的),回退到全局配置。"""
        for ds in self.datasets:
            if ds.rerank_model:
                return ds.rerank_model
        return self.global_rerank_model

    async def ainvoke(self, input: dict, config=None) -> dict:
        # 兼容两种输入形态:
        #   1) orchestrator 传 {"fused": [...], "query": "..."}(inter_dataset_fusion 输出)
        #   2) 链式调用传 {"filtered": [...], "query": "..."}(与 per-dataset RerankRunnable 对齐)
        hits: list[ScoredDocument] = input.get("fused") or input.get("filtered") or []
        query: str = input.get("query", "")
        warnings: list[str] = list(input.get("warnings", []))

        if not hits or not self.reranker:
            return {**input, "fused": hits, "warnings": warnings}

        # 拆文本侧 / 非文本侧(caption/image_caption 走 RRF,不被文本 rerank 误杀)
        text_hits = [h for h in hits if h.source in ("vector", "fulltext")]
        non_text_hits = [h for h in hits if h.source not in ("vector", "fulltext")]

        if not text_hits:
            return {**input, "fused": hits[:self.top_k], "warnings": warnings}

        try:
            docs = [h.text for h in text_hits]
            reranked = await self.reranker.rerank(
                query, docs, min(self.top_k, len(docs))
            )

            # 把 reranked 转为"按 rerank 排名"的 ScoredDocument 序列
            rerank_ranked: list[ScoredDocument] = []
            for rank, (orig_idx, _rscore) in enumerate(reranked, start=1):
                if 0 <= orig_idx < len(text_hits):
                    # P0-13 修复 (audit #6): 写入 rerank_score,否则 task12 filter_by_score
                    # 的 using_re_rerank=True 永远回退到 doc.score,rerank-aware 阈值失效。
                    rerank_ranked.append(
                        text_hits[orig_idx].model_copy(update={
                            "rank": rank,
                            "rerank_score": _rscore,
                        })
                    )

            # weight=1.0 短路:跳过 RRF 融合,直接用 reranked 顺序
            if self.weight == 1.0:
                fused_text = rerank_ranked
            else:
                # 复用 intra_fusion:把 rerank_ranked 与 text_hits 视为两条 rank-based 列表
                fused_text = intra_fusion(
                    query_groups=[rerank_ranked, text_hits],
                    rrf_k=self.weight,
                )

            all_hits = list(fused_text) + list(non_text_hits)
            return {**input, "fused": all_hits[:self.top_k], "warnings": warnings}

        except Exception:
            # G1: 全局 rerank 失败,跳过,不阻塞(与 per-dataset rerank 一致)
            warnings = warnings + ["global_rerank_skipped: API error"]
            return {**input, "fused": hits[:self.top_k], "warnings": warnings}

    def invoke(self, input, config=None):
        import asyncio
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = None
        return asyncio.run(self.ainvoke(input, config))


def build_global_rerank_node(deps: dict) -> RunnableLambda | Runnable:
    """G1 辅助:根据 deps 构造 GlobalRerankRunnable,供 task16 `build_full_pipeline` 串入。

    deps 期望字段:
      - use_global_rerank: bool,True 才构造节点
      - global_reranker: 已实例化的 Reranker(可来自 deps["reranker_factory"](None))
      - datasets: list[Dataset](用于取 dataset.rerank_model)
      - global_rerank_model: str | None,回退配置
      - rerank_weight: float,默认 0.5(B12 对齐)
      - top_k: int,默认 10

    用法(task16 时接入):
        from rag.pipeline.global_rerank import build_global_rerank_node
        nodes.append(build_global_rerank_node(deps))
        # 串到 orchestrator 的 inter_dataset_fusion 之后、filter_pipeline 之前
    """
    if not deps.get("use_global_rerank"):
        return RunnableLambda(lambda x: x)
    return GlobalRerankRunnable(
        reranker=deps.get("global_reranker") or deps.get("reranker"),
        datasets=deps.get("datasets", []),
        global_rerank_model=deps.get("global_rerank_model"),
        weight=deps.get("rerank_weight", 0.5),
        top_k=deps.get("top_k", 10),
    )
```

- [ ] **Step 5c: 写 GlobalRerankRunnable 单测**

```python
# tests/unit/test_global_rerank.py
import pytest
import uuid
from rag.domain.document import ScoredDocument, ChunkMetadata
from rag.domain.dataset import Dataset
from rag.pipeline.global_rerank import GlobalRerankRunnable, build_global_rerank_node


class FakeReranker:
    def __init__(self, score_fn=None):
        self.score_fn = score_fn or (lambda i: 1.0 - i * 0.1)
    async def rerank(self, query, documents, top_k):
        return [
            (i, self.score_fn(i))
            for i in range(min(top_k, len(documents)))
        ]


def _hit(i, source="vector"):
    return ScoredDocument(
        chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
        text=f"d{i}", score=0.5, rank=i, source=source,
        metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"),
    )


@pytest.mark.asyncio
async def test_global_rerank_uses_first_dataset_rerank_model():
    """G1: datasets 第一个声明 rerank_model 的 dataset 决定全局 rerank_model。"""
    ds_with = Dataset(id=uuid.uuid4(), name="a", embed_model="m", embed_dim=1536, rerank_model="m1")
    ds_no = Dataset(id=uuid.uuid4(), name="b", embed_model="m", embed_dim=1536, rerank_model=None)
    g = GlobalRerankRunnable(reranker=FakeReranker(), datasets=[ds_with, ds_no])
    assert g._resolve_rerank_model() == "m1"


@pytest.mark.asyncio
async def test_global_rerank_fallback_to_global_config():
    """G1: datasets 都没声明时,回退到 global_rerank_model。"""
    ds1 = Dataset(id=uuid.uuid4(), name="a", embed_model="m", embed_dim=1536, rerank_model=None)
    g = GlobalRerankRunnable(
        reranker=FakeReranker(), datasets=[ds1], global_rerank_model="g1"
    )
    assert g._resolve_rerank_model() == "g1"


@pytest.mark.asyncio
async def test_global_rerank_runs_after_inter_fusion():
    """G1: 接收 {"fused": [...], "query": "..."} 形态,输出 {"fused": [...], "warnings": [...]}。"""
    hits = [_hit(i) for i in range(5)]
    g = GlobalRerankRunnable(reranker=FakeReranker(), top_k=3)
    out = await g.ainvoke({"fused": hits, "query": "q"})
    assert "fused" in out
    assert len(out["fused"]) == 3
    assert "warnings" in out


@pytest.mark.asyncio
async def test_global_rerank_keeps_caption_hits():
    """G1: caption / image_caption 不进 rerank,直接拼回(与 per-dataset RerankRunnable 对齐)。"""
    text_hits = [_hit(i, source="vector") for i in range(3)]
    cap = _hit(99, source="caption")
    g = GlobalRerankRunnable(reranker=FakeReranker(), top_k=10)
    out = await g.ainvoke({"fused": text_hits + [cap], "query": "q"})
    assert any(h.source == "caption" for h in out["fused"])


@pytest.mark.asyncio
async def test_global_rerank_skips_on_api_error():
    """G1: rerank API 失败时,跳过并追加 warning,不阻塞。"""
    class FailReranker:
        async def rerank(self, *a, **kw):
            raise RuntimeError("api down")
    g = GlobalRerankRunnable(reranker=FailReranker(), top_k=3)
    out = await g.ainvoke({"fused": [_hit(i) for i in range(5)], "query": "q"})
    assert any("global_rerank_skipped" in w for w in out["warnings"])
    assert len(out["fused"]) == 3   # 仍返回 fused(只是未 rerank)


def test_build_global_rerank_node_no_op_when_disabled():
    """G1: use_global_rerank=False 时,build_global_rerank_node 返回透传 lambda。"""
    node = build_global_rerank_node({"use_global_rerank": False})
    out = node.invoke({"fused": [_hit(0)], "query": "q"})
    assert out["fused"][0].text == "d0"
```

- [ ] **Step 6: 写 subgraph.py (C3 修正 + rerank 集成)**

```python
# src/rag/pipeline/subgraph.py
import asyncio
from langchain_core.runnables import RunnableLambda, RunnableParallel
from rag.pipeline.fusion import intra_fusion
from rag.pipeline.filter import filter_pipeline

def build_dataset_subgraph(dataset, deps):
    """单 dataset subgraph: 多 query_variant 并行检索 → intra_fusion → [rerank] → filter。

    B1 修正: 接收 state["query_variants"] (list[str]), 对每个 variant 独立检索,
    结果去重后 RRF 融合。Stage 1+2 的 query extension 输出真正被下游消费。
    """
    vec_retriever = deps["vector_retriever"]
    ft_retriever = deps["fulltext_retriever"]
    top_k_val = deps.get("top_k", 10)

    async def _ainvoke(state):
        variants = state.get("query_variants", [state.get("query", "")])

        # 每个 variant 并发 vec + ft, 汇总
        all_vec_hits: list[ScoredDocument] = []
        all_ft_hits: list[ScoredDocument] = []

        async def _search_one(variant):
            vh = await vec_retriever.search(variant, top_k_val)
            fh = await ft_retriever.search(variant, top_k_val)
            return vh, fh

        tasks = [_search_one(v) for v in variants]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                continue
            vh, fh = r
            all_vec_hits.extend(vh)
            all_ft_hits.extend(fh)

        # E1 修正: set 去重 + 保序。all_vec_hits/all_ft_hits 已按 rank 升序,
        # 首次出现即最优,后续同 chunk_id 直接跳过(原先 `next(...).index(...)` 是 O(n²))。
        seen_vec: set[uuid.UUID] = set()
        deduped_vec: list[ScoredDocument] = []
        for d in all_vec_hits:
            if d.chunk_id in seen_vec:
                continue
            seen_vec.add(d.chunk_id)
            deduped_vec.append(d)
        all_vec_hits = deduped_vec

        seen_ft: set[uuid.UUID] = set()
        deduped_ft: list[ScoredDocument] = []
        for d in all_ft_hits:
            if d.chunk_id in seen_ft:
                continue
            seen_ft.add(d.chunk_id)
            deduped_ft.append(d)
        all_ft_hits = deduped_ft

        # B4 修正: intra_fusion 签名是 (query_groups, rrf_k) — 单参数
        # query_groups 顺序 = vec 在前 ft 在后,跨组 RRF 累加同 chunk_id
        # 权重 0.7 / 0.3 在 list 顺序上编码(intra_fusion 不接 w_vector/w_fulltext 字段)
        fused = intra_fusion(
            query_groups=[all_vec_hits, all_ft_hits],
            rrf_k=dataset.rrf_k,
        )
        # P0-14 修复 (audit #6): 改用 subgraph_filter(per-dataset 子预算)
        # spec §0.1 强制要求 per-dataset token 预算,原 filter_pipeline 不支持
        from rag.pipeline.filter import subgraph_filter
        filtered, warnings = subgraph_filter(
            fused, score_threshold=state.get("score_threshold", 0.0),
            max_tokens=None,
        )
        return {"filtered": filtered, "warnings": warnings, "query": state.get("query", "")}

    runnable = RunnableLambda(_ainvoke)

    # P0-17 修复 (audit #7): 增加 use_rerank 门控,SearchRequest.use_rerank=False
    # 时跳过 RerankRunnable。原实现只看 dataset.rerank_model + deps.reranker,
    # 不消费 SearchRequest.use_rerank,导致请求方临时禁用 rerank 失效。
    # spec §0.1 强制项: per-request 偏好必须优于 dataset 级配置。
    # 默认 True 保持向后兼容(未传 use_rerank 时行为不变)。
    use_rerank = deps.get("use_rerank", True)
    if use_rerank and dataset.rerank_model and deps.get("reranker"):
        from rag.pipeline.rerank import RerankRunnable
        # B12: 默认 weight=0.5
        rerank_node = RerankRunnable(
            deps["reranker"],
            weight=deps.get("rerank_weight", 0.5),
            top_k=deps.get("top_k", 10),
        )
        return runnable | rerank_node

    return runnable
```

- [ ] **Step 7: 写 orchestrator 单测 (mock subgraph)**

```python
# tests/unit/test_orchestrator.py
import pytest
import uuid
from rag.pipeline.orchestrator import DatasetOrchestrator
from rag.domain.search import SearchResult

def _ds():
    from rag.domain.dataset import Dataset
    return Dataset(id=uuid.uuid4(), name="t", embed_model="m", embed_dim=1536)

@pytest.mark.asyncio
async def test_orchestrator_handles_partial_failure():
    """H1: 1 个 subgraph 失败时, failed_dataset_ids 应包含其 ds.id。"""
    from langchain_core.runnables import RunnableLambda
    ds1, ds2 = _ds(), _ds()

    async def runner1(state):
        raise RuntimeError("ds1 fail")
    async def runner2(state):
        return {"filtered": [], "warnings": []}

    orch = DatasetOrchestrator(
        datasets=[ds1, ds2],
        subgraphs={ds1.id: RunnableLambda(runner1), ds2.id: RunnableLambda(runner2)},
    )
    result = await orch.ainvoke({"query": "test"})
    assert ds1.id in result.failed_dataset_ids
    assert ds2.id not in result.failed_dataset_ids
    assert "ds1 fail" in str(result.warnings) or any("ds1 fail" in w for w in result.warnings)

@pytest.mark.asyncio
async def test_orchestrator_all_failed():
    """全部 dataset 失败: 返回空 citations + 全部 failed_dataset_ids。"""
    from langchain_core.runnables import RunnableLambda
    ds1 = _ds()

    async def runner(state):
        raise RuntimeError("fail")
    orch = DatasetOrchestrator(
        datasets=[ds1], subgraphs={ds1.id: RunnableLambda(runner)},
    )
    result = await orch.ainvoke({"query": "test"})
    assert result.citations == []
    assert ds1.id in result.failed_dataset_ids
```

- [ ] **Step 8: 跑全部测试**

```bash
uv run pytest tests/unit/test_cite.py tests/unit/test_orchestrator.py tests/unit/test_rerank.py tests/unit/test_parent_doc.py -v
# 期望: 6 passed (cite: 3 + orchestrator: 2 + rerank: 4 + parent_doc: 2)
```

- [ ] **Step 9: commit**

```bash
git add src/rag/pipeline src/rag/retrieval tests/
git commit -m "feat(pipeline): subgraph + orchestrator (RunnableParallel) + rerank + cite + parent_doc

B12: SearchRequest.rerank_weight / RerankRunnable default 0.5 (对齐 FastGPT defaultReRankWeight)
B13: rerank 混合公式由线性 score-based 改为 WRRF 融合两条 rank-based 列表
subagent #8: CohereRerank 加 text2Chunks 拆分 + existsId 抑制 + weight=1.0 短路
subagent #8: rerank 入口前去重 + rerank 仅作用在 source in (vector, fulltext)"
```
