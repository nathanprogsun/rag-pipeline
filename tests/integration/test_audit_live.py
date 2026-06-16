"""Audit + Citation check 集成测试 — 真实 PG + 真实 embedding + JSONL file。

不 mock:
- 真实 embedding: ``live_embed_model`` fixture
- 真实 PG: 真实 dataset + chunk + pgvector HNSW
- 真实 orchestrator (含 SimpleCite 触发 citation_count > 0)
- 真实 JSONL file write: tmp_path / audit.jsonl
- 真实 AuditTap.record → 真实读回

场景:
- 真实 audit tap 通过 orchestrator 链路写入有效 NDJSON
- 真实 citation_check 端到端: gen emit markers → cite → check
- 真实 round-trip: write → read_jsonl_records 还原 records
- 真实 multiple requests: 多行 NDJSON, 每行独立
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest
from rag.infra.observability.audit import AuditRecord, AuditTap, read_jsonl_records
from rag.infra.text.citation_check import CitationChecker
from rag.search.orchestrator import SearchPipeline
from rag.search.post.cite import SimpleCite
from tests.integration._db_helpers import (
    create_dataset,
    seed_chunks,
)
from tests.integration._retriever import make_subgraph

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_audit_records_orchestrator_run(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 1: orchestrator → AuditTap → 真实 NDJSON file。
    验证: 真实 PG + 真实 embedding 跑完后, 写出的 NDJSON 含正确字段。
    """
    ds = await create_dataset(db_session, "audit-real-1")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式 教程。",
            "Python 数据分析 pandas 入门。",
        ],
        embed_model=live_embed_model,
    )
    subgraphs = {
        ds: make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
            top_k=5,
        )
    }
    orch = SearchPipeline(subgraphs=subgraphs, cite=SimpleCite())

    # 跑 orchestrator + 写 audit
    audit_path = tmp_path / "audit.jsonl"
    tap = AuditTap(audit_path, sample_rate=1.0, sync=True)
    req = SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    result = await orch.ainvoke(req)
    rec = AuditRecord.from_search_result(req, result, request_id="test-001")
    await tap.record(rec)
    tap.close()

    # 读回 NDJSON, 验证结构
    records = read_jsonl_records(audit_path)
    assert len(records) == 1
    rec_dict = records[0]
    assert rec_dict["request_id"] == "test-001"
    assert rec_dict["query"] == "Python 列表推导式"
    assert ds in [uuid.UUID(x) for x in rec_dict["dataset_ids"]]
    assert rec_dict["citation_count"] == len(result.citations)
    assert rec_dict["intermediate_hits_count"] == len(result._intermediate_hits)
    assert rec_dict["retrieval_top_k"] == req.retrieval.top_k
    assert rec_dict["retrieval_use_rerank"] == req.retrieval.use_rerank
    assert "timestamp" in rec_dict


@pytest.mark.asyncio
async def test_real_audit_multiple_requests_ndjson(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 2: 多个连续请求 → 多行 NDJSON, 每行独立 JSON。"""
    ds = await create_dataset(db_session, "audit-multi")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=["Python 教程。", "Python 数据分析。"],
        embed_model=live_embed_model,
    )
    subgraphs = {
        ds: make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
        )
    }
    orch = SearchPipeline(subgraphs=subgraphs, cite=SimpleCite())

    audit_path = tmp_path / "audit.jsonl"
    tap = AuditTap(audit_path, sample_rate=1.0, sync=True)

    queries = ["Python 教程", "Python 数据分析", "Python"]
    for i, q in enumerate(queries):
        req = SearchRequest(query=q, dataset_ids=[ds])
        result = await orch.ainvoke(req)
        await tap.record(
            AuditRecord.from_search_result(req, result, request_id=f"req-{i}")
        )
    tap.close()

    # 读回
    records = read_jsonl_records(audit_path)
    assert len(records) == 3
    assert [r["query"] for r in records] == queries
    assert [r["request_id"] for r in records] == ["req-0", "req-1", "req-2"]
    # 每行独立 (不会因换行污染 JSON 解析)
    for r in records:
        assert "request_id" in r
        assert "query" in r


@pytest.mark.asyncio
async def test_real_audit_parent_doc_window_captured(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 3: req.context.parent_doc_window > 0 → audit 记录该字段。"""
    from rag.domain.search import ContextConfig

    ds = await create_dataset(db_session, "audit-window")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=["Python 教程。"],
        embed_model=live_embed_model,
    )
    subgraphs = {
        ds: make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
        )
    }
    orch = SearchPipeline(subgraphs=subgraphs)

    audit_path = tmp_path / "audit.jsonl"
    tap = AuditTap(audit_path, sample_rate=1.0, sync=True)

    req = SearchRequest(
        query="Python",
        dataset_ids=[ds],
        context=ContextConfig(parent_doc_window=3),
    )
    result = await orch.ainvoke(req)
    await tap.record(AuditRecord.from_search_result(req, result))
    tap.close()

    records = read_jsonl_records(audit_path)
    assert records[0]["parent_doc_window"] == 3


@pytest.mark.asyncio
async def test_real_audit_failed_dataset_tracked(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 4: missing dataset_id → failed_dataset_ids 进入 audit。"""
    ds_registered = await create_dataset(db_session, "audit-fail")
    await seed_chunks(
        db_session,
        dataset_id=ds_registered,
        texts=["Python 教程。"],
        embed_model=live_embed_model,
    )
    subgraphs = {
        ds_registered: make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_registered,
            embed_model=live_embed_model,
        )
    }
    orch = SearchPipeline(subgraphs=subgraphs)
    missing = uuid.uuid4()  # NOT registered

    audit_path = tmp_path / "audit.jsonl"
    tap = AuditTap(audit_path, sample_rate=1.0, sync=True)

    req = SearchRequest(query="Python", dataset_ids=[ds_registered, missing])
    result = await orch.ainvoke(req)
    await tap.record(AuditRecord.from_search_result(req, result))
    tap.close()

    records = read_jsonl_records(audit_path)
    failed = [uuid.UUID(x) for x in records[0]["failed_dataset_ids"]]
    assert missing in failed
    assert ds_registered not in failed


@pytest.mark.asyncio
async def test_real_citation_check_end_to_end(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 5: orchestrator + cite + gen emit markers + CitationChecker。

    验证: 真实 LLM 输出的 [id](CITE) markers (mock gen 模拟) 经过
    CitationChecker 校验, 与真实 citations 一致 → valid=True。
    """
    ds = await create_dataset(db_session, "cite-check-e2e")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式 教程。",
            "Python 数据分析 入门。",
            "Python 异步 asyncio 入门。",
        ],
        embed_model=live_embed_model,
    )
    subgraphs = {
        ds: make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
            top_k=3,
        )
    }

    async def marker_gen(
        docs: list[ScoredDocument],
        citations: list[Citation],
        req: SearchRequest,
    ) -> str:
        """Mock gen: 引用前 2 个 citation (out of N)."""
        if not citations:
            return "no content"
        return "see [1](CITE) and [2](CITE) for more"

    orch = SearchPipeline(subgraphs=subgraphs, cite=SimpleCite(), gen=marker_gen)
    result = await orch.ainvoke(
        SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    )

    # CitationChecker 校验: response 含 [1](CITE) 和 [2](CITE)
    checker = CitationChecker()
    check_result = checker.check(result.response, result.citations)
    assert check_result.valid is True
    assert sorted(check_result.referenced_unique) == [1, 2]
    # 至少有 1 个 orphan citation (gen 只引用前 2 个, 第 3 个 orphan)
    assert len(check_result.orphan_citation_indices) >= 1


@pytest.mark.asyncio
async def test_real_citation_check_invalid_marker(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 6: gen emit [99](CITE) 超出 citations 范围 → CitationChecker 检出。"""
    ds = await create_dataset(db_session, "cite-check-invalid")
    await seed_chunks(
        db_session,
        dataset_id=ds,
        texts=["Python 教程。"],
        embed_model=live_embed_model,
    )
    subgraphs = {
        ds: make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
        )
    }

    async def bad_gen(
        docs: list[ScoredDocument],
        citations: list[Citation],
        req: SearchRequest,
    ) -> str:
        return "this cites [99](CITE) which is out of range"

    orch = SearchPipeline(subgraphs=subgraphs, cite=SimpleCite(), gen=bad_gen)
    result = await orch.ainvoke(SearchRequest(query="Python", dataset_ids=[ds]))

    checker = CitationChecker()
    check_result = checker.check(result.response, result.citations)
    assert check_result.valid is False
    assert 99 in check_result.out_of_range_ids
