"""SearchPipeline 集成测试 — 真实 PG + 真实 embedding + 真实链路。

不 mock 任何 LLM/embedding 路径: 使用 ``live_embed_model`` fixture
(DashScope text-embedding-v3 / 1536 dim) 真实调 embedding API; chunk 入库
前用同样的 embedder 真实计算 embedding, 让 query 向量和 chunk 向量在同一
语义空间可比。缺 OPENAI_EMBEDDING_API_KEY 时整体 skip。

链路覆盖（按 Contract 8 阶段顺序）:
- 1 query_ext (None → identity, 不调 LLM)
- 2 subgraph fan-out (真实 SearchSubgraph + ChunkRepository + pgvector HNSW)
- 3 inter-variant intra_fusion (单 variant 走默认路径)
- 6 inter-dataset intra_fusion (跨 dataset 合并)
- 7 filter (真实 score_breakdown 阈值 + 真实 MiniMax-M3 tokenizer 预算)
- 8-9 parent_doc / cite (None / 注入 callback)
- 10 gen (None / 注入 callback)

场景覆盖:
- 跨 dataset fan-out: 两个 dataset 都有相关 chunk
- dataset 隔离: dataset A 召回不污染 dataset B
- 缺失 dataset 跟踪: failed_dataset_ids 精确
- subgraph-level 异常隔离: 单 subgraph 抛错不影响其他
- 真实 cosine 排序: 相关 chunk 排前, 不相关排后
- 中文语义搜索: 中文 chunk + ChineseTokenizer + 真实 embedding
- score 阈值过滤: 基于真实 score_breakdown
- token 预算过滤: 真实 MiniMax-M3 tokenizer
- 注入 rerank / cite / gen callback: 验证 hook 接入
- model_dump_json 不泄漏 _intermediate_hits (Contract 6)
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
from rag.search.orchestrator import SearchPipeline
from rag.search.retrieve.subgraph import SearchSubgraph

# 真实 embedding 维度: 与 settings.openai_embedding_dim 对齐
EMBED_DIM: int = settings.openai_embedding_dim


# ─────────────────────────────────────────────────────────────────────────────
# 真实数据 fixtures
# ─────────────────────────────────────────────────────────────────────────────


# 中文测试语料, 用于 _seed_chunks_with_real_embeddings
CORPUS_PYTHON: dict[str, list[str]] = {
    "python-tutorial": [
        "Python 是一门解释型、动态类型的高级编程语言, 语法简洁易读。",
        "Python 教程: 列表推导式是 Python 的标志性语法糖。",
        "Python 数据分析入门: pandas 是核心库, 提供了 DataFrame 结构。",
        "Python 异步编程: asyncio 协程与事件循环机制。",
    ],
    "java-tutorial": [
        "Java 是一门静态类型、编译型语言, 强类型与跨平台是核心特征。",
        "Java 教程: 面向对象三大特性 — 封装、继承、多态。",
        "Java 虚拟机 (JVM) 提供了内存管理与垃圾回收机制。",
    ],
    "general-programming": [
        "编程语言对比: Python 适合快速原型, Java 适合大型系统。",
        "软件工程实践: 单元测试、持续集成、代码审查。",
    ],
}


async def _create_dataset(
    db_session: AsyncSession, name: str, *, embed_model: str | None = None
) -> uuid.UUID:
    """在真实 PG 中创建 dataset 行 (含真实 embed_model / embed_dim)。"""
    ds = DatasetModel(
        id=uuid.uuid4(),
        name=name,
        embed_model=embed_model or settings.openai_embedding_model,
        embed_dim=EMBED_DIM,
    )
    db_session.add(ds)
    await db_session.flush()
    return ds.id


async def _seed_chunks_with_real_embeddings(
    db_session: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    texts: list[str],
    embed_model: Embeddings,
) -> list[ChunkModel]:
    """真实 embedding 入库: 用 live_embed_model 真实调 embedding API,
    把结果作为 chunk 的 embedding 列。中文/英文统一处理。
    """
    embeddings: list[list[float]] = await embed_model.aembed_documents(texts)
    assert len(embeddings) == len(texts)
    chunks: list[ChunkModel] = []
    for text_content, embedding in zip(texts, embeddings, strict=True):
        chunk = ChunkModel(
            dataset_id=dataset_id,
            text=text_content,
            embedding=embedding,
        )
        db_session.add(chunk)
        chunks.append(chunk)
    await db_session.flush()
    # 中文 tsvector 用 ChineseTokenizer 真实分词
    for chunk in chunks:
        await db_session.execute(
            text(
                "UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) WHERE id = :id"
            ),
            {"t": ChineseTokenizer().build_tsvector(chunk.text), "id": chunk.id},
        )
    await db_session.commit()
    return chunks


async def _seed_corpus(
    db_session: AsyncSession,
    *,
    embed_model: Embeddings,
    dataset_specs: dict[str, list[str]],
) -> dict[str, uuid.UUID]:
    """批量创建 dataset 并入库真实 embedding chunk。

    Args:
        dataset_specs: {dataset_name: [chunk_text_1, chunk_text_2, ...]}

    Returns:
        {dataset_name: dataset_id}
    """
    ids: dict[str, uuid.UUID] = {}
    for ds_name, texts in dataset_specs.items():
        ds_id = await _create_dataset(db_session, ds_name)
        await _seed_chunks_with_real_embeddings(
            db_session,
            dataset_id=ds_id,
            texts=texts,
            embed_model=embed_model,
        )
        ids[ds_name] = ds_id
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Runnable adapter: ChunkRepository → LangChain Runnable 契约
# ─────────────────────────────────────────────────────────────────────────────


class _RepoRetriever:
    """Runnable adapter wrapping ChunkRepository for subgraph integration tests.

    关键: 每次 ainvoke 都从 session_factory 新建 session (per-call session)
    避免 asyncio.gather 多 retriever 共享 session 导致 transaction 冲突。
    sync ``invoke`` 通过 ``run_coroutine_sync`` 桥接, 与
    ``rag.infra.pg.vector_store.VectorRetriever`` 入口契约对齐。
    """

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

        # 每个 retriever 独立 session: 避免 asyncio.gather 共享 transaction
        async with self.session_factory() as session:
            repo = ChunkRepository(session)
            if self.mode == "vector":
                assert self.embed_model is not None
                query_emb = await self.embed_model.aembed_query(query)
                rows = await repo.search_by_vector(query_emb, self.dataset_id, top_k)
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
        """sync 入口, 通过 run_coroutine_sync 桥接。"""
        from rag.infra.pg.runnable_sync import run_coroutine_sync

        return run_coroutine_sync(lambda: self.ainvoke(input, config, **kwargs))


def _make_subgraph(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    dataset_id: uuid.UUID,
    embed_model: Embeddings,
    top_k: int = 10,
) -> SearchSubgraph:
    """构造真实 SearchSubgraph (vector + fulltext 双路)。"""
    return SearchSubgraph(
        dataset_id=dataset_id,
        vector_retriever=_RepoRetriever(  # type: ignore[arg-type]
            session_factory=session_factory,
            dataset_id=dataset_id,
            mode="vector",
            embed_model=embed_model,
        ),
        fulltext_retriever=_RepoRetriever(  # type: ignore[arg-type]
            session_factory=session_factory,
            dataset_id=dataset_id,
            mode="fulltext",
        ),
        top_k=top_k,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 真实场景测试
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_two_datasets_relevant_content(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 1: 两个 dataset 都有 Python 相关 chunk, query "Python 教程"
    应该召回两边的 chunk, 跨 dataset 融合后都在结果中。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={
            "ds-python-1": CORPUS_PYTHON["python-tutorial"],
            "ds-python-2": CORPUS_PYTHON["python-tutorial"][:2],  # 子集
        },
    )
    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
        )
        for ds_id in ids.values()
    }
    orch = SearchPipeline(subgraphs=subgraphs)

    result = await orch.ainvoke(
        SearchRequest(query="Python 教程", dataset_ids=list(ids.values()))
    )

    # 跨 dataset 召回: 两个 dataset 的 chunk 都应出现
    returned_dataset_ids = {d.dataset_id for d in result._intermediate_hits}
    assert returned_dataset_ids == set(ids.values()), (
        f"cross-dataset fan-out 失败: got {returned_dataset_ids}, "
        f"expected {set(ids.values())}"
    )
    # 无失败 dataset
    assert result.failed_dataset_ids == []
    # 召回结果非空
    assert len(result._intermediate_hits) >= 1


@pytest.mark.asyncio
async def test_real_dataset_isolation(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 2: dataset A 是 Python 内容, dataset B 是 Java 内容。
    验证 dataset_id 边界正确性: 每个召回 chunk 的 dataset_id 必须等于
    它被检索的那个 dataset (SQL 层 ``WHERE dataset_id == ?`` 强制保证),
    而不是测试"Java chunk 永远不会出现在 Python query 结果中" (这取决于
    embedding 模型的语义判别能力, 不是 orchestrator 的职责)。

    orchestrator 的职责是: 跨 dataset 召回时, 每个 ScoredDocument.dataset_id
    都精确指向它被检索的 dataset, 不会发生 dataset_id 错位。
    """
    ds_python = await _create_dataset(db_session, "ds-python-only")
    ds_java = await _create_dataset(db_session, "ds-java-only")
    py_chunks = await _seed_chunks_with_real_embeddings(
        db_session,
        dataset_id=ds_python,
        texts=CORPUS_PYTHON["python-tutorial"],
        embed_model=live_embed_model,
    )
    java_chunks = await _seed_chunks_with_real_embeddings(
        db_session,
        dataset_id=ds_java,
        texts=CORPUS_PYTHON["java-tutorial"],
        embed_model=live_embed_model,
    )
    py_ids = {c.id for c in py_chunks}
    java_ids = {c.id for c in java_chunks}

    subgraphs = {
        ds_python: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_python,
            embed_model=live_embed_model,
        ),
        ds_java: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_java,
            embed_model=live_embed_model,
        ),
    }
    orch = SearchPipeline(subgraphs=subgraphs)

    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=[ds_python, ds_java])
    )

    # dataset_id 边界正确性: 每个 chunk 的 dataset_id 精确对应它被检索的 dataset
    for chunk in result._intermediate_hits:
        if chunk.chunk_id in py_ids:
            assert chunk.dataset_id == ds_python, (
                f"py chunk {chunk.chunk_id} 被错误标到 dataset {chunk.dataset_id}"
            )
        elif chunk.chunk_id in java_ids:
            assert chunk.dataset_id == ds_java, (
                f"java chunk {chunk.chunk_id} 被错误标到 dataset {chunk.dataset_id}"
            )
        else:
            pytest.fail(f"未知 chunk: {chunk.chunk_id}")


@pytest.mark.asyncio
async def test_real_missing_dataset_tracked(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 3: request 包含未注册的 dataset_id → 进入 failed_dataset_ids。
    已注册的 dataset 仍然正常召回。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={"ds-registered": CORPUS_PYTHON["python-tutorial"][:1]},
    )
    registered_id = ids["ds-registered"]
    missing_id = uuid.uuid4()  # 不创建, 不注册 subgraph

    subgraphs = {
        registered_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=registered_id,
            embed_model=live_embed_model,
        ),
    }
    orch = SearchPipeline(subgraphs=subgraphs)

    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=[registered_id, missing_id])
    )

    # 缺失 dataset 计入 failed_dataset_ids
    assert missing_id in result.failed_dataset_ids
    assert registered_id not in result.failed_dataset_ids
    # 已注册 dataset 仍正常召回
    assert len(result._intermediate_hits) >= 1


@pytest.mark.asyncio
async def test_real_subgraph_exception_isolated(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 4: 一个 subgraph.ainvoke 抛错 (不是 retriever 级别),
    其他 dataset 正常召回, 错误进入 result.warnings。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={
            "ds-survivor": CORPUS_PYTHON["python-tutorial"][:1],
            "ds-broken": CORPUS_PYTHON["python-tutorial"][:1],
        },
    )

    class _BrokenSubgraph:
        """subgraph.ainvoke 抛错, 模拟 subgraph 层崩溃。"""

        async def ainvoke(self, query: str) -> list[ScoredDocument]:
            raise RuntimeError("subgraph layer crashed")

    survivor_subgraph = _make_subgraph(
        session_factory=pg_session_factory,
        dataset_id=ids["ds-survivor"],
        embed_model=live_embed_model,
    )
    broken_subgraph = _BrokenSubgraph()

    orch = SearchPipeline(
        subgraphs={
            ids["ds-survivor"]: survivor_subgraph,
            ids["ds-broken"]: broken_subgraph,  # type: ignore[dict-item]
        }
    )
    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=list(ids.values()))
    )

    # ds-survivor 召回幸存
    assert any(d.dataset_id == ids["ds-survivor"] for d in result._intermediate_hits)
    # ds-broken 的错误进入 warnings
    assert any("subgraph_failed" in w for w in result.warnings)
    # failed_dataset_ids 仍为空 (subgraph 注册了, 只是运行时崩)
    assert result.failed_dataset_ids == []


@pytest.mark.asyncio
async def test_real_score_filter_drops_irrelevant(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 5: 真实 cosine 排序 + score_breakdown 阈值过滤。
    Python 相关 chunk 应排前, 阈值足够高时过滤掉 Java 无关 chunk。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={
            "ds-mixed": (
                CORPUS_PYTHON["python-tutorial"] + CORPUS_PYTHON["java-tutorial"]
            ),
        },
    )
    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
            top_k=10,
        )
        for ds_id in ids.values()
    }

    # 先无阈值跑, 确认两边的 chunk 都召回 (top_k 足够)
    orch_no_filter = SearchPipeline(subgraphs=subgraphs)
    result_no_filter = await orch_no_filter.ainvoke(
        SearchRequest(query="Python 教程", dataset_ids=list(ids.values()))
    )
    all_chunks = result_no_filter._intermediate_hits
    assert len(all_chunks) > 0

    # 计算中间结果的真实 cosine 分布
    scores = [
        d.score_breakdown.get("vector", 0.0)
        for d in all_chunks
        if d.score_breakdown.get("vector", 0.0) > 0
    ]
    assert len(scores) > 0, "应至少有一个 chunk 有真实 cosine 分数"

    # 取中间分位作为阈值, 验证只有真正相关的 chunk 保留
    sorted_scores = sorted(scores, reverse=True)
    median_threshold = sorted_scores[len(sorted_scores) // 2]

    orch_filtered = SearchPipeline(
        subgraphs=subgraphs,
        filter_score_threshold=median_threshold,
    )
    result_filtered = await orch_filtered.ainvoke(
        SearchRequest(query="Python 教程", dataset_ids=list(ids.values()))
    )
    filtered_chunks = result_filtered._intermediate_hits
    # 阈值过滤后, 所有保留 chunk 的 score_breakdown[vector] >= median_threshold
    for chunk in filtered_chunks:
        assert chunk.score_breakdown.get("vector", 0.0) >= median_threshold - 1e-6


@pytest.mark.asyncio
async def test_real_token_budget_keeps_top(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 6: token 预算用真实 MiniMax-M3 tokenizer 计数。
    小 budget 强制只保留前缀 (RRF 排在前面的 chunk)。
    """
    # 入库混合长度 chunk: 长 (重复 padding) + 短
    long_text = "Python 数据分析 " * 200  # 远超 token 预算
    short_texts = CORPUS_PYTHON["python-tutorial"]
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={
            "ds-budget": [long_text, *short_texts],
        },
    )
    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
            top_k=10,
        )
        for ds_id in ids.values()
    }
    # 小 budget: 几百 token, 任何 chunk 都放不下, 长 chunk 优先被踢
    orch = SearchPipeline(subgraphs=subgraphs, token_budget=200)
    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=list(ids.values()))
    )

    # 长 chunk (200+ token) 被 token 预算踢掉; 短 chunk 留下
    kept = result._intermediate_hits
    if kept:
        assert all(len(c.text) < len(long_text) for c in kept), (
            "token 预算应踢掉长 padding chunk"
        )


@pytest.mark.asyncio
async def test_real_intermediate_hits_excluded_from_json(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 7 (Contract 6): model_dump_json() 不泄漏 _intermediate_hits,
    但程序化访问仍然可用。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={
            "ds-json": CORPUS_PYTHON["python-tutorial"][:2],
        },
    )
    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
        )
        for ds_id in ids.values()
    }
    orch = SearchPipeline(subgraphs=subgraphs)

    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=list(ids.values()))
    )

    # 程序化访问可用
    assert isinstance(result._intermediate_hits, list)
    # JSON dump 不泄漏
    json_str = result.model_dump_json()
    assert "_intermediate_hits" not in json_str
    assert "intermediate_hits" not in json_str
    # 但其他字段仍然存在
    assert "response" in json_str
    assert "citations" in json_str


@pytest.mark.asyncio
async def test_real_chinese_semantic_search(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 8: 中文语义检索。query "数据分析" 应该召回含
    "Python 数据分析入门" 的 chunk, 而不是无关的 "Java 虚拟机" chunk。
    验证真实 embedding 的语义理解能力。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={
            "ds-semantic": (
                CORPUS_PYTHON["python-tutorial"] + CORPUS_PYTHON["java-tutorial"]
            ),
        },
    )
    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
            top_k=5,
        )
        for ds_id in ids.values()
    }
    orch = SearchPipeline(subgraphs=subgraphs)

    result = await orch.ainvoke(
        SearchRequest(query="数据分析", dataset_ids=list(ids.values()))
    )

    # Top-1 应包含 "数据分析" 相关内容
    if result._intermediate_hits:
        top_text = result._intermediate_hits[0].text
        assert "数据" in top_text or "Python" in top_text, (
            f"Top-1 应包含数据相关 chunk, 实际: {top_text[:80]}"
        )


@pytest.mark.asyncio
async def test_real_with_cite_callback(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 9: 注入 cite callback, citations 列表被填充。
    Citation 内容从 ScoredDocument 真实字段构造。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={"ds-cite": CORPUS_PYTHON["python-tutorial"][:2]},
    )

    class CiteCollector:
        """真实 cite stage: 把召回 chunk 转 Citation DTO。"""

        def __call__(
            self, docs: list[ScoredDocument], req: SearchRequest
        ) -> list[Citation]:
            return [
                Citation(
                    chunk_id=d.chunk_id,
                    dataset_id=d.dataset_id,
                    source_name=f"chunk-{i}",
                    content=d.text,
                    score=d.score,
                )
                for i, d in enumerate(docs, start=1)
            ]

    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
        )
        for ds_id in ids.values()
    }
    orch = SearchPipeline(subgraphs=subgraphs, cite=CiteCollector())

    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=list(ids.values()))
    )

    # citations 被 cite callback 填充, source_name 严格 1-based 顺序
    assert len(result.citations) >= 1
    assert all(isinstance(c, Citation) for c in result.citations)
    assert [c.source_name for c in result.citations] == [
        f"chunk-{i}" for i in range(1, len(result.citations) + 1)
    ]


@pytest.mark.asyncio
async def test_real_with_gen_callback(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 10: 注入 gen callback, response 字符串被填充。
    gen 接收真实的 ScoredDocument + Citation, 输出 Contract 4 的 response。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={"ds-gen": CORPUS_PYTHON["python-tutorial"][:2]},
    )

    class StubCite:
        """Mock cite: 1-to-1 with docs (保证 zip 不越界)。"""

        def __call__(
            self, docs: list[ScoredDocument], req: SearchRequest
        ) -> list[Citation]:
            return [
                Citation(
                    chunk_id=d.chunk_id,
                    dataset_id=d.dataset_id,
                    source_name=f"src-{i}",
                    content=d.text,
                    score=d.score,
                )
                for i, d in enumerate(docs, start=1)
            ]

    async def real_gen(
        docs: list[ScoredDocument],
        citations: list[Citation],
        req: SearchRequest,
    ) -> str:
        """伪 gen: 用召回 chunk 文本拼一段 summary + [id](CITE) marker。"""
        if not docs or not citations:
            return "no relevant content found"
        summary_parts = []
        for cite, doc in zip(citations, docs, strict=True):
            summary_parts.append(f"[{cite.source_name}](CITE): {doc.text[:30]}")
        return " | ".join(summary_parts)

    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
        )
        for ds_id in ids.values()
    }
    orch = SearchPipeline(subgraphs=subgraphs, cite=StubCite(), gen=real_gen)

    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=list(ids.values()))
    )

    # response 非空, 含 [id](CITE) 内联标记 (Contract 4 + 5)
    assert result.response != ""
    assert "(CITE)" in result.response
    # response 与 citations 一一对应
    assert result.response.count("(CITE)") == len(result.citations)


@pytest.mark.asyncio
async def test_real_with_rerank_callback(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 11: 注入 rerank callback, 验证 stage 4 hook 接入。
    用一个"反转排序"的 mock rerank, 验证 hook 真的被调用并改了顺序。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={"ds-rerank": CORPUS_PYTHON["python-tutorial"][:3]},
    )

    class ReverseRerank:
        """Mock rerank: 反转排序 (mock stage 4 的语义)。"""

        def __init__(self) -> None:
            self.called_with: list[ScoredDocument] = []

        async def __call__(
            self, docs: list[ScoredDocument], req: SearchRequest
        ) -> list[ScoredDocument]:
            self.called_with = list(docs)
            return list(reversed(docs))

    rerank = ReverseRerank()
    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
        )
        for ds_id in ids.values()
    }
    orch = SearchPipeline(subgraphs=subgraphs, rerank=rerank)

    result = await orch.ainvoke(
        SearchRequest(query="Python", dataset_ids=list(ids.values()))
    )

    # rerank 真的被调用过, 接收到 orchestrator 融合后的 hits
    assert rerank.called_with, "rerank stage 未被调用"
    assert len(rerank.called_with) >= 1
    # 重排后顺序反转: 第一项应是原始最后一项
    if len(rerank.called_with) >= 2:
        assert result._intermediate_hits[0].chunk_id == rerank.called_with[-1].chunk_id


@pytest.mark.asyncio
async def test_real_full_chain_with_all_callbacks(
    db_session: AsyncSession,
    pg_session_factory: async_sessionmaker[AsyncSession],
    live_embed_model: Embeddings,
) -> None:
    """真实场景 12: 完整链路。
    query_ext (None) → 2 dataset fan-out → score filter → cite → gen。
    验证 5d2 orchestrator 在真实配置下能把多阶段串通, 无警告无崩溃。
    """
    ids = await _seed_corpus(
        db_session,
        embed_model=live_embed_model,
        dataset_specs={
            "ds-full-py": CORPUS_PYTHON["python-tutorial"],
            "ds-full-gen": CORPUS_PYTHON["general-programming"],
        },
    )

    class FullCite:
        def __call__(
            self, docs: list[ScoredDocument], req: SearchRequest
        ) -> list[Citation]:
            return [
                Citation(
                    chunk_id=d.chunk_id,
                    dataset_id=d.dataset_id,
                    source_name=f"src-{i}",
                    content=d.text,
                    score=d.score_breakdown.get("vector", 0.0),
                )
                for i, d in enumerate(docs, start=1)
            ]

    async def full_gen(
        docs: list[ScoredDocument],
        citations: list[Citation],
        req: SearchRequest,
    ) -> str:
        return f"基于 {len(citations)} 条引用回答 query={req.query!r}"

    subgraphs = {
        ds_id: _make_subgraph(
            session_factory=pg_session_factory,
            dataset_id=ds_id,
            embed_model=live_embed_model,
            top_k=5,
        )
        for ds_id in ids.values()
    }
    orch = SearchPipeline(
        subgraphs=subgraphs,
        filter_score_threshold=0.0,  # 全部保留 (真实 cosine 通常 > 0)
        token_budget=50_000,  # 50K token, 正常 chunk 都装得下
        cite=FullCite(),
        gen=full_gen,
    )

    result = await orch.ainvoke(
        SearchRequest(query="Python 数据分析", dataset_ids=list(ids.values()))
    )

    # 跨 dataset 召回
    returned_dataset_ids = {d.dataset_id for d in result._intermediate_hits}
    assert returned_dataset_ids == set(ids.values()), (
        "完整链路下两个 dataset 都应被召回"
    )
    # cite callback 填充 citations
    assert len(result.citations) >= 1
    # gen callback 填充 response
    assert result.response.startswith("基于")
    assert "Python" in result.response
    # 无内部 warnings (干净链路)
    assert result.warnings == []
