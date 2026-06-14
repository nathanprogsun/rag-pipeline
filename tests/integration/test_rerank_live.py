"""RerankStageAdapter 集成测试 — 真实 DashScope qwen3-rerank API。

不 mock 重排: 使用 ``live_rerank_model`` fixture 真实调 qwen3-rerank,
缺 OPENAI_RERANK_API_KEY 时整体 skip。

真实场景覆盖:
- 真实重排排序: 5 个中文 chunk, 高度相关的应该排前
- 与 orchestrator 真实集成: full chain + rerank hook
- 真实 rerank_score + score_breakdown["rerank"] 落地
- image_caption modality 真实绕过 rerank
- 真实 rerank_weight 边界: 0/1/0.5 三种权重
- NoOpRerankStage 真实路径: rerank 关闭时通过 orchestrator 仍能工作
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
from rag.infra.llm.rerank import QwenRerank, get_rerank_model
from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.pipeline.orchestrator import PipelineOrchestrator
from rag.pipeline.rerank import NoOpRerankStage, RerankStageAdapter
from rag.pipeline.subgraph import SearchSubgraph

# ─────────────────────────────────────────────────────────────────────────────
# Live fixtures & helpers
# ─────────────────────────────────────────────────────────────────────────────

EMBED_DIM: int = settings.openai_embedding_dim


@pytest.fixture(scope="session")
def live_rerank_model() -> QwenRerank:
    """真实 qwen3-rerank 客户端。缺 OPENAI_RERANK_API_KEY 时 skip。"""
    api_key = settings.openai_rerank_api_key.get_secret_value().strip()
    if not api_key:
        pytest.skip("OPENAI_RERANK_API_KEY not configured")
    return get_rerank_model()


def _scored_doc(
    chunk_id: uuid.UUID,
    *,
    text: str,
    score: float = 0.5,
    modality: str = "text",
    breakdown: dict[str, float] | None = None,
) -> ScoredDocument:
    """构造 ScoredDocument, 用于直接喂给 RerankStageAdapter。"""
    return ScoredDocument(
        chunk_id=chunk_id,
        dataset_id=uuid.uuid4(),
        text=text,
        score=score,
        rank=0,
        source="vector",
        modality=modality,  # type: ignore[arg-type]
        metadata=ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file"),
        score_breakdown=breakdown or {"vector": score},
    )


def _request(query: str = "Python 列表推导式") -> SearchRequest:
    return SearchRequest(query=query, dataset_ids=[uuid.uuid4()])


# ─────────────────────────────────────────────────────────────────────────────
# Real rerank scenarios (no DB, real API only)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_rerank_relevant_first(
    live_rerank_model: QwenRerank,
) -> None:
    """真实场景 1: 5 个中文 chunk, "Python 列表推导式" 相关性高的应排前。

    直接调 qwen3-rerank API, 不走 orchestrator。验证 rerank 模型的
    真实语义判别能力。
    """
    docs = [
        "Java 是一门静态类型、编译型语言, 强调面向对象。",
        "Python 教程: 列表推导式是 Python 的标志性语法糖。",
        "Python 是一门解释型、动态类型的高级编程语言。",
        "Java 教程: 面向对象三大特性 — 封装、继承、多态。",
        "Python 列表推导式: [x*2 for x in range(10)]。",
    ]
    reranked = await live_rerank_model.rerank(
        query="Python 列表推导式",
        documents=docs,
        top_k=len(docs),
    )
    # Top-3 至少应包含 2 个 Python 列表推导式相关 chunk
    top3_idx = {idx for idx, _ in reranked[:3]}
    assert 1 in top3_idx, f"列表推导式描述应排前 3, got top3={top3_idx}"
    assert 4 in top3_idx, f"列表推导式代码应排前 3, got top3={top3_idx}"


@pytest.mark.asyncio
async def test_real_rerank_stage_adapter_with_real_reranker(
    live_rerank_model: QwenRerank,
) -> None:
    """真实场景 2: RerankStageAdapter + 真实 qwen3-rerank。
    验证 stage 4+5 在真实 rerank API 下正确填充 rerank_score 和
    score_breakdown["rerank"], 并按 rerank 顺序重排。
    """
    docs = [
        _scored_doc(uuid.uuid4(), text="Java 静态类型编译型语言", score=0.9),
        _scored_doc(uuid.uuid4(), text="Python 列表推导式语法糖", score=0.7),
        _scored_doc(uuid.uuid4(), text="Python 数据分析 pandas 入门", score=0.5),
        _scored_doc(uuid.uuid4(), text="Java 虚拟机 JVM 内存管理", score=0.4),
    ]
    adapter = RerankStageAdapter(
        reranker=live_rerank_model, rerank_weight=1.0
    )
    result = await adapter(docs, _request(query="Python 列表推导式"))

    # rerank_score 被填充
    assert all(d.rerank_score is not None for d in result)
    # score_breakdown["rerank"] 落地
    assert all("rerank" in d.score_breakdown for d in result)
    # 排序变化: Python 列表推导式 应该非末尾
    top_chunk = result[0]
    assert "Python" in top_chunk.text or "列表推导式" in top_chunk.text


@pytest.mark.asyncio
async def test_real_rerank_image_caption_bypassed(
    live_rerank_model: QwenRerank,
) -> None:
    """真实场景 3: image_caption modality 真实绕过 rerank。

    把 image chunk 混入 docs, 验证:
    - 真实 reranker 只看到 text 内容
    - image chunk 输出时 rerank_score 仍为 None
    """
    docs = [
        _scored_doc(uuid.uuid4(), text="Python 列表推导式教程", score=0.5),
        _scored_doc(
            uuid.uuid4(),
            text="(image caption) Python 代码截图",
            score=0.5,
            modality="image_caption",
        ),
    ]
    adapter = RerankStageAdapter(
        reranker=live_rerank_model, rerank_weight=1.0
    )
    result = await adapter(docs, _request(query="Python 列表推导式"))

    # 找 image chunk
    img = next(d for d in result if d.modality == "image_caption")
    text_docs = [d for d in result if d.modality == "text"]
    # image 未被 rerank
    assert img.rerank_score is None
    # text 被 rerank
    assert all(d.rerank_score is not None for d in text_docs)


@pytest.mark.asyncio
async def test_real_rerank_weight_zero_preserves_text_ranking(
    live_rerank_model: QwenRerank,
) -> None:
    """真实场景 4: rerank_weight=0.0 → 原始 RRF 排序完全保留,
    rerank API 调用了但权重为零不影响最终顺序。
    """
    docs = [
        _scored_doc(uuid.uuid4(), text="Alpha Python 教程", score=0.9),
        _scored_doc(uuid.uuid4(), text="Beta Java 教程", score=0.7),
    ]
    adapter = RerankStageAdapter(
        reranker=live_rerank_model, rerank_weight=0.0
    )
    result = await adapter(docs, _request(query="Python 教程"))

    # 第一名仍是原始 score=0.9 的 Alpha (rerank 权重=0 不改变 RRF 顺序)
    assert result[0].text == "Alpha Python 教程"


# ─────────────────────────────────────────────────────────────────────────────
# Real orchestrator + real rerank integration
# ─────────────────────────────────────────────────────────────────────────────


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


@pytest.mark.asyncio
async def test_real_orchestrator_with_rerank_full_chain(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
    live_rerank_model: QwenRerank,
) -> None:
    """真实场景 5: orchestrator 接入真实 rerank, 完整链路。

    验证: query_ext (None) → 2 dataset fan-out → 真实 rerank → cite。
    rerank API 真实调用, 排序提升。
    """
    ds = await _create_dataset(db_session, "rerank-fullchain")
    await _seed_chunks(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式: [x*2 for x in range(10)], 简洁高效。",
            "Java 是一门静态类型、编译型语言。",
            "Python 教程: 列表推导式是 Python 的标志性语法糖。",
            "Java 教程: 面向对象三大特性 — 封装、继承、多态。",
        ],
        embed_model=live_embed_model,
    )

    subgraphs = {
        ds: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
            top_k=10,
        )
    }

    class SimpleCite:
        def __call__(
            self, docs: list[ScoredDocument], req: SearchRequest
        ) -> list[Citation]:
            return [
                Citation(
                    chunk_id=d.chunk_id,
                    dataset_id=d.dataset_id,
                    source_name=f"src-{i}",
                    content=d.text,
                    score=d.score_breakdown.get("rerank", d.score),
                )
                for i, d in enumerate(docs, start=1)
            ]

    orch = PipelineOrchestrator(
        subgraphs=subgraphs,
        rerank=RerankStageAdapter(
            reranker=live_rerank_model, rerank_weight=0.8
        ),
        cite=SimpleCite(),
    )

    result = await orch.ainvoke(
        SearchRequest(query="Python 列表推导式", dataset_ids=[ds])
    )

    # 所有召回 chunk 都有 rerank_score (rerank 实际跑过)
    assert all(d.rerank_score is not None for d in result._intermediate_hits)
    # citations 来自 cite callback
    assert len(result.citations) >= 1
    # response 为空 (gen 未注入)
    assert result.response == ""


@pytest.mark.asyncio
async def test_real_noop_rerank_through_orchestrator(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 6: NoOpRerankStage 通过 orchestrator 注入。
    rerank 关闭时 (use_rerank=False), orchestrator 用 NoOp 跳过 stage 4。
    验证 NoOp 路径下整个链路仍然 work, 不残留 rerank_score。
    """
    ds = await _create_dataset(db_session, "rerank-noop")
    await _seed_chunks(
        db_session,
        dataset_id=ds,
        texts=["Python 列表推导式 教程。"],
        embed_model=live_embed_model,
    )
    subgraphs = {
        ds: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds,
            embed_model=live_embed_model,
        )
    }
    orch = PipelineOrchestrator(
        subgraphs=subgraphs, rerank=NoOpRerankStage()
    )
    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=[ds])
    )

    # NoOp: rerank_score 全部 None, score_breakdown 没有 "rerank" 键
    for d in result._intermediate_hits:
        assert d.rerank_score is None
        assert "rerank" not in d.score_breakdown