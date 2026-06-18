"""SearchPipeline 集成测试 — 真实 PG + 真实 embedding + 真实 NDJSON audit。

不 mock:
- 真实 embedding: ``live_embed_model`` fixture (DashScope text-embedding-v4)
- 真实 PG: 真实 dataset + 真实 chunk (embedding 由 live_embed_model 生成)
- 真实 VectorRetriever / FulltextRetriever (调 live_embed_model.aembed_query + ChunkRepository)
- 真实 SearchPipeline (含 SimpleCite 触发 citation_count > 0)
- 真实 JSONL file write
- LLM: mock (因为真实 LLM 调用昂贵且不在本任务范围; 通过 mock llm.ainvoke)

场景:
- 真实 SearchPipeline → 真实 orchestrator + 真实 audit
- 真实 ainvoke → SearchResult 含 _intermediate_hits / citations / response
- 真实 audit round-trip: write → read_jsonl_records
- 真实 embedding 链路: aembed_documents (seed) → aembed_query (query) → pgvector HNSW
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession

from rag.domain.search import SearchRequest
from rag.infra.observability.audit import AuditTap, read_jsonl_records
from rag.search.orchestrator import SearchPipeline
from tests.integration._db_helpers import create_dataset, seed_chunks

# ─────────────────────────────────────────────────────────────────────────────
# Helpers — real PG seeding with real embeddings
# ─────────────────────────────────────────────────────────────────────────────


def _fake_llm(response_text: str = "answer [1](CITE) and [2](CITE)") -> MagicMock:
    """Mock LLM returning a fixed response with [id](CITE) markers."""
    llm = MagicMock()
    ai = MagicMock()
    ai.content = response_text
    llm.ainvoke = AsyncMock(return_value=ai)
    return llm


# ─────────────────────────────────────────────────────────────────────────────
# Real end-to-end scenarios (live_embed_model + real PG + real retriever)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_build_full_pipeline_end_to_end(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
) -> None:
    """真实场景 1: 真实 embedding API + 真实 PG + 真实 retriever 全链路。

    不 monkeypatch 任何 retriever. 真实:
    - live_embed_model.aembed_documents(seed texts) → 写入 chunk.embedding
    - VectorRetriever.search 内部调 live_embed_model.aembed_query(query)
      → 真实 DashScope API → 真实 1536-dim 向量
    - ChunkRepository.search_by_vector → pgvector HNSW 检索
    - SimpleCite + make_llm_gen → SearchResult

    验证: query 能召回 seed 的相关 chunk.
    """
    ds = await create_dataset(db_session, "full-pipeline-1")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式 教程。",
            "Python 数据分析 pandas 入门。",
            "Java 是一门静态类型、编译型语言。",
        ],
        embed_model=live_embed_model,
    )

    pipeline = SearchPipeline(embedder=live_embed_model, llm=_fake_llm())

    req = SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    result = await pipeline.ainvoke(req)

    # SearchResult shape 验证
    assert result.response == "answer [1](CITE) and [2](CITE)"
    assert len(result.citations) >= 1
    assert len(result._intermediate_hits) >= 1
    # 真实 cosine 召回: 至少召回 Python 相关 chunk (排除 Java)
    texts_in_hits = {hit.text for hit in result._intermediate_hits}
    assert any("Python" in t for t in texts_in_hits), (
        f"真实 cosine 应至少召回 1 个 Python chunk, got {texts_in_hits}"
    )
    assert result.failed_dataset_ids == []


@pytest.mark.asyncio
async def test_real_build_full_pipeline_with_audit(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 2: 真实 audit tap + 真实 NDJSON 落盘 + 真实 retrieval。

    验证:
    - 真实 embedding → 真实召回 → SimpleCite 填充 citations
    - req.audit=True → AuditRecord.from_search_result → AuditTap.record
    - 真实 NDJSON file 写入 → read_jsonl_records 真实读回
    """
    ds = await create_dataset(db_session, "full-pipeline-audit")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 数据分析 pandas 入门。",
            "Python 列表推导式 教程。",
            "Java 静态类型语言。",
        ],
        embed_model=live_embed_model,
    )

    audit_path = tmp_path / "audit.jsonl"
    pipeline = SearchPipeline(
        embedder=live_embed_model,
        llm=_fake_llm(),
        audit_tap=AuditTap(audit_path, sample_rate=1.0, sync=True),
    )
    req = SearchRequest(query="Python 数据分析", dataset_ids=[ds], audit=True)

    await pipeline.ainvoke(req)

    # 真实 NDJSON 写入 + 读回
    records = read_jsonl_records(audit_path)
    assert len(records) == 1
    assert records[0]["query"] == "Python 数据分析"
    assert records[0]["citation_count"] >= 1
    assert records[0]["intermediate_hits_count"] >= 1
    assert records[0]["parent_doc_window"] == 0  # 未启用


@pytest.mark.asyncio
async def test_real_build_full_pipeline_failed_dataset(
    live_embed_model: Embeddings,
) -> None:
    """真实场景 3: 不存在的 dataset_id → 空召回 + response fallback。

    不需要 db_session (没有 seed 数据)。真实 embedding API 仍被调用
    (VectorRetriever.search → aembed_query), 但检索结果为空 (PG 无对应 dataset)。
    """
    pipeline = SearchPipeline(embedder=live_embed_model, llm=_fake_llm())

    fake_ds = uuid.uuid4()  # PG 中无对应 chunk
    req = SearchRequest(query="test", dataset_ids=[fake_ds])

    result = await pipeline.ainvoke(req)

    # 不 crash, 召回为空, response 走 fallback
    assert result.failed_dataset_ids == []
    assert result.citations == []
    assert result._intermediate_hits == []
    assert result.response == "no relevant content found"


@pytest.mark.asyncio
async def test_real_build_full_pipeline_multiple_requests(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 4: 多次 ainvoke → 真实 embedding 多次调用 + 多行 NDJSON 落盘。

    验证:
    - 每次 request 都真实调 live_embed_model.aembed_query (无缓存)
    - NDJSON 每行独立记录 (1 request = 1 line)
    """
    ds = await create_dataset(db_session, "full-pipeline-multi")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=["Python 教程。"],
        embed_model=live_embed_model,
    )

    audit_path = tmp_path / "audit.jsonl"
    pipeline = SearchPipeline(
        embedder=live_embed_model,
        llm=_fake_llm(response_text="answer"),
        audit_tap=AuditTap(audit_path, sample_rate=1.0, sync=True),
    )

    queries = ["Python", "Python 教程", "Python 数据分析"]
    for q in queries:
        req = SearchRequest(query=q, dataset_ids=[ds], audit=True)
        await pipeline.ainvoke(req)

    records = read_jsonl_records(audit_path)
    assert len(records) == 3
    assert [r["query"] for r in records] == queries


@pytest.mark.asyncio
async def test_real_build_full_pipeline_cosine_ranking(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
) -> None:
    """真实场景 5: 真实 cosine 语义排序 — 高度相关 chunk 排前。

    seed 4 个 chunk, 其中"Python 列表推导式"与 query "Python 列表推导式" 语义最接近,
    应被排到 top (高 cosine similarity).
    """
    ds = await create_dataset(db_session, "full-pipeline-rank")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Java 是一门静态类型、编译型语言, 强调面向对象。",  # 不相关
            "Python 教程: 列表推导式是 Python 的标志性语法糖。",  # 高度相关
            "Python 列表推导式: [x*2 for x in range(10)] 简洁高效。",  # 高度相关
            "Python 数据分析 pandas 入门。",  # 中等相关
        ],
        embed_model=live_embed_model,
    )

    pipeline = SearchPipeline(embedder=live_embed_model, llm=_fake_llm())
    req = SearchRequest(query="Python 列表推导式", dataset_ids=[ds])

    result = await pipeline.ainvoke(req)

    # 验证至少召回了一个相关 chunk
    texts_in_hits = [hit.text for hit in result._intermediate_hits]
    assert any("列表推导式" in t for t in texts_in_hits), (
        f"应召回含 '列表推导式' 的 chunk, got {texts_in_hits}"
    )
    # Java 不应出现在 top results
    assert not any("Java" in t for t in texts_in_hits[:2]), (
        f"Java chunk 不应在 top-2, got {texts_in_hits[:2]}"
    )
