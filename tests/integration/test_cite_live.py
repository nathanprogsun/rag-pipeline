"""Cite stage 集成测试 — 真实 PG + 真实 embedding 链路。

不 mock embedding / LLM (LLM 由 mock gen 替代, 因为 gen 不在本任务范围):
- 真实 embedding: 通过 ``live_embed_model`` fixture (DashScope text-embedding-v4)
- 真实 PG: 真实 dataset + 真实 chunk + 真实 pgvector HNSW
- gen: 用 stub gen 生成含 ``[id](CITE)`` markers 的 response
- cite: 真实 SimpleCite + resolve_citation_positions

真实场景:
- SimpleCite 通过 orchestrator 真实编排: cite stage 落地到 result.citations
- resolve_citation_positions: 真实 markers + 真实 citation 位置
- 多 dataset fan-out 后的 cite: 跨 dataset 顺序 1-based
- image_caption modality: image_path 真实保留到 citation
- 端到端 round-trip: simple gen emits [1](CITE) [2](CITE) → position 落地
"""

from __future__ import annotations

import uuid

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rag.config import settings
from rag.domain.document import ChunkMetadata, ScoredDocument
from rag.domain.search import Citation, SearchRequest
from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.pipeline.cite import (
    SimpleCite,
    parse_inline_citations,
    resolve_citation_positions,
)
from rag.pipeline.orchestrator import PipelineOrchestrator
from rag.pipeline.subgraph import SearchSubgraph

EMBED_DIM: int = settings.openai_embedding_dim


# ─────────────────────────────────────────────────────────────────────────────
# 真实数据 fixtures & helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _create_dataset(
    db_session: AsyncSession, name: str
) -> uuid.UUID:
    ds = DatasetModel(
        id=uuid.uuid4(),
        name=name,
        embed_model=settings.openai_embedding_model,
        embed_dim=EMBED_DIM,
    )
    db_session.add(ds)
    await db_session.flush()
    return ds.id


async def _seed_chunks(
    db_session: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    texts: list[str],
    embed_model: Embeddings,
) -> list[ChunkModel]:
    """真实 embedding 入库。"""
    embeddings: list[list[float]] = await embed_model.aembed_documents(texts)
    chunks: list[ChunkModel] = []
    for content, emb in zip(texts, embeddings, strict=True):
        chunk = ChunkModel(
            dataset_id=dataset_id, text=content, embedding=emb
        )
        db_session.add(chunk)
        chunks.append(chunk)
    await db_session.flush()
    for chunk in chunks:
        await db_session.execute(
            text(
                "UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) "
                "WHERE id = :id"
            ),
            {"t": ChineseTokenizer().build_tsvector(chunk.text), "id": chunk.id},
        )
    await db_session.commit()
    return chunks


class _RepoRetriever:
    """Runnable adapter wrapping ChunkRepository。"""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        dataset_id: uuid.UUID,
        mode: str,
        embed_model: Embeddings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.dataset_id = dataset_id
        self.mode = mode
        self.embed_model = embed_model

    async def ainvoke(
        self,
        input: dict[str, object],
        config: object = None,
        **kwargs: object,
    ) -> list[ScoredDocument]:
        query = str(input["query"])
        raw_top_k = input.get("top_k", 10)
        top_k = raw_top_k if isinstance(raw_top_k, int) else 10

        async with self.session_factory() as session:
            repo = ChunkRepository(session)
            if self.mode == "vector":
                assert self.embed_model is not None
                query_emb = await self.embed_model.aembed_query(query)
                rows = await repo.search_by_vector(
                    query_emb, self.dataset_id, top_k
                )
            elif self.mode == "fulltext":
                rows = await repo.search_by_fulltext(query, self.dataset_id, top_k)
            else:
                msg = f"unknown mode: {self.mode}"
                raise ValueError(msg)

            return [
                ScoredDocument(
                    chunk_id=chunk.id,
                    dataset_id=chunk.dataset_id,
                    text=chunk.text,
                    score=score,
                    rank=i,
                    source=self.mode,  # type: ignore[arg-type]
                    modality=chunk.modality,
                    image_path=chunk.image_path,
                    metadata=ChunkMetadata(
                        dataset_id=chunk.dataset_id,
                        datasource=chunk.metadata.datasource,
                        filename=chunk.metadata.filename,
                        parent_title=chunk.metadata.parent_title,
                        chunk_index=chunk.metadata.chunk_index,
                    ),
                )
                for i, (chunk, score) in enumerate(rows)
            ]

    def invoke(
        self,
        input: dict[str, object],
        config: object = None,
        **kwargs: object,
    ) -> list[ScoredDocument]:
        from rag.infra.pg.runnable_sync import run_coroutine_sync

        return run_coroutine_sync(lambda: self.ainvoke(input, config, **kwargs))


def _make_subgraph(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dataset_id: uuid.UUID,
    embed_model: Embeddings,
    top_k: int = 10,
) -> SearchSubgraph:
    return SearchSubgraph(
        dataset_id=dataset_id,
        vector_retriever=_RepoRetriever(
            session_factory=session_factory,
            dataset_id=dataset_id,
            mode="vector",
            embed_model=embed_model,
        ),
        fulltext_retriever=_RepoRetriever(
            session_factory=session_factory,
            dataset_id=dataset_id,
            mode="fulltext",
        ),
        top_k=top_k,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Real cite scenarios
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_cite_through_orchestrator(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 1: orchestrator 接入 SimpleCite, 真实 PG/embedding。
    验证 cite 阶段把 final hits 转成 1-based Citation 列表, source_name 正确。
    """
    ds = await _create_dataset(db_session, "cite-real-1")
    seeded = await _seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式是 Python 的标志性语法糖。",
            "Python 是一门解释型、动态类型的高级编程语言。",
            "Java 是一门静态类型、编译型语言。",
        ],
        embed_model=live_embed_model,
    )

    subgraphs = {
        ds: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
            top_k=3,
        )
    }
    orch = PipelineOrchestrator(
        subgraphs=subgraphs,
        cite=SimpleCite(),
    )
    result = await orch.ainvoke(
        SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    )

    # citations 数量与 final hits 一致
    assert len(result.citations) == len(result._intermediate_hits)
    # source_name 严格 1-based
    assert [c.source_name for c in result.citations] == [
        f"src-{i}" for i in range(1, len(result.citations) + 1)
    ]
    # 每个 citation 都有真实 chunk_id (来自 seeded chunks)
    cited_chunk_ids = {c.chunk_id for c in result.citations}
    seeded_chunk_ids = {c.id for c in seeded}
    assert cited_chunk_ids.issubset(seeded_chunk_ids)
    # position 全部 None (未经过 resolve_citation_positions)
    assert all(c.position is None for c in result.citations)


@pytest.mark.asyncio
async def test_real_cite_image_caption_preserves_image_path(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 2: image_caption modality chunk 的 image_path 真实保留到 Citation。

    入库一个 image_caption chunk + 一个 text chunk, 验证 SimpleCite 把
    image_path 从 ScoredDocument 正确传到 Citation。
    """
    ds = await _create_dataset(db_session, "cite-img")
    text_emb = (
        await live_embed_model.aembed_documents(["Python 教程 列表推导式"])
    )[0]
    img_emb = (
        await live_embed_model.aembed_documents(["(image caption) Python 列表推导式代码截图"])
    )[0]

    text_chunk = ChunkModel(
        dataset_id=ds, text="Python 教程 列表推导式", embedding=text_emb
    )
    img_chunk = ChunkModel(
        dataset_id=ds,
        text="(image caption) Python 列表推导式代码截图",
        embedding=img_emb,
        modality="image_caption",
        image_path="/img/python-syntax.png",
    )
    db_session.add_all([text_chunk, img_chunk])
    await db_session.flush()
    for chunk in [text_chunk, img_chunk]:
        await db_session.execute(
            text(
                "UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) "
                "WHERE id = :id"
            ),
            {"t": ChineseTokenizer().build_tsvector(chunk.text), "id": chunk.id},
        )
    await db_session.commit()

    subgraphs = {
        ds: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
            top_k=10,
        )
    }
    orch = PipelineOrchestrator(subgraphs=subgraphs, cite=SimpleCite())
    result = await orch.ainvoke(
        SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    )

    # 找到 image_caption citation
    img_cite = next(
        (
            c
            for c in result.citations
            if c.image_path is not None
        ),
        None,
    )
    assert img_cite is not None, "image_caption citation 未保留 image_path"
    assert img_cite.image_path == "/img/python-syntax.png"


@pytest.mark.asyncio
async def test_real_cite_with_custom_source_name_fn(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 3: 自定义 source_name_fn (例如带 dataset 前缀)。
    验证 fn 真的被调用, 返回的 source_name 落到 Citation。
    """
    ds = await _create_dataset(db_session, "cite-custom")
    await _seed_chunks(
        db_session,
        dataset_id=ds,
        texts=["Python 列表推导式 教程。", "Python 数据分析 pandas 入门。"],
        embed_model=live_embed_model,
    )

    def name_with_dataset(doc: ScoredDocument, idx: int) -> str:
        return f"ds-{doc.dataset_id.hex[:6]}-src-{idx}"

    subgraphs = {
        ds: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
            top_k=5,
        )
    }
    orch = PipelineOrchestrator(
        subgraphs=subgraphs, cite=SimpleCite(source_name_fn=name_with_dataset)
    )
    result = await orch.ainvoke(
        SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    )

    # 每个 citation 都以 ds- 开头 (自定义 fn 生效)
    assert all(c.source_name.startswith("ds-") for c in result.citations)
    # source_name 含 dataset hex 前缀
    assert any(ds.hex[:6] in c.source_name for c in result.citations)


@pytest.mark.asyncio
async def test_real_cite_two_datasets_ordered_1_based(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 4: 跨 dataset fan-out 后, cite 仍按 1-based 顺序编号。
    验证 multi-dataset 场景下 citations[0..N-1] 严格按 _intermediate_hits 顺序。
    """
    ds_a = await _create_dataset(db_session, "cite-multi-a")
    ds_b = await _create_dataset(db_session, "cite-multi-b")
    await _seed_chunks(
        db_session,
        dataset_id=ds_a,
        texts=["Python 列表推导式 教程 A1", "Python 数据分析 A2"],
        embed_model=live_embed_model,
    )
    await _seed_chunks(
        db_session,
        dataset_id=ds_b,
        texts=["Python 教程 B1", "Python pandas B2"],
        embed_model=live_embed_model,
    )

    subgraphs = {
        ds_a: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_a,
            embed_model=live_embed_model,
            top_k=5,
        ),
        ds_b: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_b,
            embed_model=live_embed_model,
            top_k=5,
        ),
    }
    orch = PipelineOrchestrator(subgraphs=subgraphs, cite=SimpleCite())
    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=[ds_a, ds_b])
    )

    # 1-based 严格递增
    for i, c in enumerate(result.citations, start=1):
        assert c.source_name == f"src-{i}"
    # citations[i].chunk_id 与 _intermediate_hits[i].chunk_id 一一对应
    for c, hit in zip(result.citations, result._intermediate_hits, strict=True):
        assert c.chunk_id == hit.chunk_id


@pytest.mark.asyncio
async def test_real_cite_round_trip_with_gen_emitting_markers(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 5: 端到端 round-trip。
    cite stage → citations; gen stage → 真实 response 含 [id](CITE);
    resolve_citation_positions → 真实 position 落地。
    """
    ds = await _create_dataset(db_session, "cite-roundtrip")
    await _seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式语法糖",
            "Python 数据分析 pandas",
            "Java 静态类型语言",
        ],
        embed_model=live_embed_model,
    )

    async def marker_gen(
        docs: list[ScoredDocument],
        citations: list[Citation],
        req: SearchRequest,
    ) -> str:
        """Mock gen: 引用前 2 个 citation, emit [1](CITE) [2](CITE)。"""
        if not citations:
            return "no content"
        return (
            f"基于 {len(citations)} 条引用回答: "
            + " ".join(
                f"[{c.source_name.split('-')[-1]}](CITE)" for c in citations[:2]
            )
        )

    subgraphs = {
        ds: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
            top_k=5,
        )
    }
    orch = PipelineOrchestrator(
        subgraphs=subgraphs, cite=SimpleCite(), gen=marker_gen
    )
    result = await orch.ainvoke(
        SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    )

    # response 含 [1](CITE) 和 [2](CITE)
    assert "[1](CITE)" in result.response
    assert "[2](CITE)" in result.response
    # parse_inline_citations 验证
    parsed = parse_inline_citations(result.response)
    assert 1 in parsed
    assert 2 in parsed
    # resolve_citation_positions: 把 marker offset 填到 Citation.position
    resolved = resolve_citation_positions(result.response, result.citations)
    # 至少有 2 个 citation 拿到非 None position
    positioned = [c for c in resolved if c.position is not None]
    assert len(positioned) >= 2


@pytest.mark.asyncio
async def test_real_cite_empty_dataset_yields_empty_citations(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 6: 空 dataset → empty retrieval → empty citations。"""
    ds = await _create_dataset(db_session, "cite-empty")
    subgraphs = {
        ds: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
        )
    }
    orch = PipelineOrchestrator(subgraphs=subgraphs, cite=SimpleCite())
    result = await orch.ainvoke(
        SearchRequest(query="anything", dataset_ids=[ds])
    )
    assert result.citations == []
    assert result._intermediate_hits == []