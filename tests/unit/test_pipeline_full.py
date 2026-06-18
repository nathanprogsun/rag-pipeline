"""Unit tests for ``SearchPipeline`` production assembly (5f).

Tests cover Contract 3 typed I/O:
- SearchPipeline production vs test mode validation
- SearchPipeline.ainvoke(SearchRequest) -> SearchResult contract
- make_llm_gen constructs GenFn with correct prompt structure
- LLM failure → fallback response

Tests use mocks for VectorRetriever / FulltextRetriever / LLM / Reranker
(orchestrator-level unit tests already cover the full chain).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rag.domain.document import ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult
from rag.search.generate.answer import make_llm_gen
from rag.search.orchestrator import SearchPipeline
from tests._fakes import ConstantEmbeddings

_FAKE_EMB_VECTOR = [0.0] * 1536
_FakeEmbeddings = ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)


def _req(query: str = "test") -> SearchRequest:
    return SearchRequest(query=query, dataset_ids=[uuid.uuid4()])


# ---------- SearchPipeline __init__ ----------


def test_production_mode_requires_embedder_and_llm() -> None:
    with pytest.raises(ValueError, match="embedder and llm"):
        SearchPipeline(embedder=_FakeEmbeddings)
    with pytest.raises(ValueError, match="embedder and llm"):
        SearchPipeline(llm=MagicMock())


def test_production_mode_accepts_embedder_and_llm() -> None:
    pipeline = SearchPipeline(embedder=_FakeEmbeddings, llm=MagicMock())
    assert pipeline._embedder is _FakeEmbeddings
    assert pipeline._llm is not None


def test_test_mode_requires_non_empty_subgraphs() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        SearchPipeline(subgraphs={})


def test_optional_rerank_client() -> None:
    rerank = MagicMock()
    pipeline = SearchPipeline(
        embedder=_FakeEmbeddings, llm=MagicMock(), rerank_client=rerank
    )
    assert pipeline._rerank_client is rerank


def test_optional_audit_tap() -> None:
    audit = MagicMock()
    pipeline = SearchPipeline(
        embedder=_FakeEmbeddings, llm=MagicMock(), audit_tap=audit
    )
    assert pipeline._audit_tap is audit


def test_default_weights() -> None:
    pipeline = SearchPipeline(embedder=_FakeEmbeddings, llm=MagicMock())
    assert pipeline._vector_weight == 0.7
    assert pipeline._fulltext_weight == 0.3
    assert pipeline._rrf_k == 60
    assert pipeline._rerank_weight == 0.7
    assert pipeline._token_budget == 960_000


# ---------- make_llm_gen ----------


async def test_make_llm_gen_calls_llm_with_messages() -> None:
    """make_llm_gen 应构造 system + user 消息并调 llm.ainvoke。"""
    from rag.domain.document import ChunkMetadata

    llm = MagicMock()
    ai_response = MagicMock()
    ai_response.content = "answer [1](CITE)"
    llm.ainvoke = AsyncMock(return_value=ai_response)

    gen = make_llm_gen(llm)
    ds = uuid.uuid4()
    docs = [
        ScoredDocument(
            chunk_id=uuid.uuid4(),
            dataset_id=ds,
            text="dummy",
            score=0.5,
            rank=0,
            source="vector",
            metadata=ChunkMetadata(datasource="file"),
        )
    ]
    citations = [
        Citation(
            chunk_id=uuid.uuid4(),
            dataset_id=ds,
            source_name="src-1",
            content="ctx content",
            score=0.5,
        )
    ]
    response = await gen(docs, citations, _req(query="Python"))

    assert response == "answer [1](CITE)"
    llm.ainvoke.assert_awaited_once()
    messages = llm.ainvoke.await_args.args[0]
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "[1] ctx content" in messages[1]["content"]
    assert "Python" in messages[1]["content"]


async def test_make_llm_gen_handles_string_response() -> None:
    """LLM 返回字符串而非 AIMessage 时, str() fallback。"""
    from rag.domain.document import ChunkMetadata

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value="raw string answer")

    gen = make_llm_gen(llm)
    ds = uuid.uuid4()
    docs = [
        ScoredDocument(
            chunk_id=uuid.uuid4(),
            dataset_id=ds,
            text="dummy",
            score=0.5,
            rank=0,
            source="vector",
            metadata=ChunkMetadata(datasource="file"),
        )
    ]
    response = await gen(
        docs,
        [
            Citation(
                chunk_id=uuid.uuid4(),
                dataset_id=ds,
                source_name="x",
                content="y",
                score=0.5,
            )
        ],
        _req(),
    )
    assert response == "raw string answer"


async def test_make_llm_gen_no_citations_returns_no_content_message() -> None:
    llm = MagicMock()
    llm.ainvoke = AsyncMock()
    gen = make_llm_gen(llm)
    response = await gen([], [], _req())
    assert response == "no relevant content found"
    llm.ainvoke.assert_not_awaited()


async def test_make_llm_gen_llm_failure_returns_error_message() -> None:
    """LLM 抛错 → 返回错误说明, 不抛异常。"""
    from rag.domain.document import ChunkMetadata

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM down"))
    gen = make_llm_gen(llm)
    ds = uuid.uuid4()
    docs = [
        ScoredDocument(
            chunk_id=uuid.uuid4(),
            dataset_id=ds,
            text="dummy",
            score=0.5,
            rank=0,
            source="vector",
            metadata=ChunkMetadata(datasource="file"),
        )
    ]
    response = await gen(
        docs,
        [
            Citation(
                chunk_id=uuid.uuid4(),
                dataset_id=ds,
                source_name="x",
                content="y",
                score=0.5,
            )
        ],
        _req(),
    )
    assert "LLM generation failed" in response


# ---------- Pipeline ainvoke ----------


async def test_pipeline_ainvoke_returns_search_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SearchPipeline.ainvoke(SearchRequest) → SearchResult with _intermediate_hits."""

    async def _mock_search_vector(
        self: object, query: str, top_k: int = 10
    ) -> list[ScoredDocument]:
        from rag.domain.document import ChunkMetadata

        return [
            ScoredDocument(
                chunk_id=uuid.uuid4(),
                dataset_id=self.dataset_id,  # type: ignore[attr-defined]
                text="hello",
                score=0.9,
                rank=0,
                source="vector",
                metadata=ChunkMetadata(datasource="file"),
            )
        ]

    async def _mock_search_fulltext(
        self: object, query: str, top_k: int = 10
    ) -> list[ScoredDocument]:
        return []

    monkeypatch.setattr(
        "rag.infra.pg.vector_store.VectorRetriever.search",
        _mock_search_vector,
    )
    monkeypatch.setattr(
        "rag.infra.pg.fulltext_store.FulltextRetriever.search",
        _mock_search_fulltext,
    )

    llm = MagicMock()
    ai = MagicMock()
    ai.content = "response [1](CITE)"
    llm.ainvoke = AsyncMock(return_value=ai)

    pipeline = SearchPipeline(embedder=_FakeEmbeddings, llm=llm)
    req = _req(query="hello")

    result = await pipeline.ainvoke(req)

    assert isinstance(result, SearchResult)
    assert result.response == "response [1](CITE)"
    assert len(result.citations) >= 1
    assert len(result._intermediate_hits) >= 1


async def test_pipeline_ainvoke_records_audit_when_req_audit_true(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """req.audit=True + audit_tap 配置 → NDJSON 写入。"""

    async def _mock_search_vector(
        self: object, query: str, top_k: int = 10
    ) -> list[ScoredDocument]:
        from rag.domain.document import ChunkMetadata

        return [
            ScoredDocument(
                chunk_id=uuid.uuid4(),
                dataset_id=self.dataset_id,  # type: ignore[attr-defined]
                text="hello",
                score=0.9,
                rank=0,
                source="vector",
                metadata=ChunkMetadata(datasource="file"),
            )
        ]

    async def _mock_search_fulltext(
        self: object, query: str, top_k: int = 10
    ) -> list[ScoredDocument]:
        return []

    monkeypatch.setattr(
        "rag.infra.pg.vector_store.VectorRetriever.search",
        _mock_search_vector,
    )
    monkeypatch.setattr(
        "rag.infra.pg.fulltext_store.FulltextRetriever.search",
        _mock_search_fulltext,
    )

    llm = MagicMock()
    ai = MagicMock()
    ai.content = "answer"
    llm.ainvoke = AsyncMock(return_value=ai)

    audit_path = tmp_path / "audit.jsonl"
    from rag.infra.observability.audit import AuditTap

    pipeline = SearchPipeline(
        embedder=_FakeEmbeddings,
        llm=llm,
        audit_tap=AuditTap(audit_path, sample_rate=1.0, sync=True),
    )
    req = SearchRequest(
        query="hello",
        dataset_ids=[uuid.uuid4()],
        audit=True,
    )

    await pipeline.ainvoke(req)

    text = audit_path.read_text(encoding="utf-8").strip()
    assert len(text.split("\n")) == 1
    import json

    parsed = json.loads(text.split("\n")[0])
    assert parsed["query"] == "hello"


async def test_pipeline_ainvoke_skips_audit_when_req_audit_false(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """req.audit=False → 即使 audit_tap 配置也不写。"""

    async def _mock_search_vector(
        self: object, query: str, top_k: int = 10
    ) -> list[ScoredDocument]:
        return []

    async def _mock_search_fulltext(
        self: object, query: str, top_k: int = 10
    ) -> list[ScoredDocument]:
        return []

    monkeypatch.setattr(
        "rag.infra.pg.vector_store.VectorRetriever.search",
        _mock_search_vector,
    )
    monkeypatch.setattr(
        "rag.infra.pg.fulltext_store.FulltextRetriever.search",
        _mock_search_fulltext,
    )

    llm = MagicMock()
    ai = MagicMock()
    ai.content = "answer"
    llm.ainvoke = AsyncMock(return_value=ai)

    audit_path = tmp_path / "audit.jsonl"
    from rag.infra.observability.audit import AuditTap

    pipeline = SearchPipeline(
        embedder=_FakeEmbeddings,
        llm=llm,
        audit_tap=AuditTap(audit_path, sample_rate=1.0, sync=True),
    )
    req = _req()

    await pipeline.ainvoke(req)

    assert not audit_path.exists()
