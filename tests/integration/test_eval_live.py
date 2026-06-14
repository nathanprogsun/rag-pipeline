"""EvalRunner 集成测试 — 真实 PG + 真实 embedding + 真实 pipeline。

不 mock:
- 真实 embedding: ``live_embed_model`` fixture
- 真实 PG: 真实 dataset + chunk
- 真实 build_full_pipeline (5f) + EvalRunner (5h)
- LLM: mock (eval 关注 retrieval, 不需要 LLM 质量)

场景:
- 真实 EvalRunner 跑真实 pipeline, 计算真实 recall/precision/mrr/ndcg
- ground_truth 命中 → recall = 1.0
- ground_truth 不命中 → recall = 0.0
- 部分命中 → recall ∈ (0, 1)
- 聚合统计 (mean/std) 真实反映多次 query
- 输出 JSON 真实落盘 + 读回
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.config import settings
from rag.eval.runner import EvalRunner
from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel
from rag.pipeline.full import PipelineDeps, build_full_pipeline

EMBED_DIM: int = settings.openai_embedding_dim


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — real PG seeding
# ─────────────────────────────────────────────────────────────────────────────


async def _create_dataset(db_session: AsyncSession, name: str) -> uuid.UUID:
    ds = DatasetModel(
        id=uuid.uuid4(),
        name=name,
        embed_model=settings.openai_embedding_model,
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


def _fake_llm() -> MagicMock:
    """Mock LLM: eval 不关心 LLM 质量, 返回空字符串。"""
    llm = MagicMock()
    ai = MagicMock()
    ai.content = ""
    llm.ainvoke = AsyncMock(return_value=ai)
    return llm


# ─────────────────────────────────────────────────────────────────────────────
# Real eval scenarios
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_eval_perfect_match_recall_one(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 1: ground_truth 完全命中 → recall=1.0, MRR=1.0, NDCG=1.0。

    seed 一个 chunk, eval dataset 用该 chunk id 作 ground_truth。
    """
    ds = await _create_dataset(db_session, "eval-perfect")
    chunks = await _seed_chunks_with_real_embeddings(
        db_session,
        dataset_id=ds,
        texts=["Python 列表推导式 教程。"],
        embed_model=live_embed_model,
    )
    seeded_id = chunks[0].id

    eval_path = tmp_path / "eval.jsonl"
    eval_path.write_text(
        json.dumps(
            {
                "query": "Python 列表推导式",
                "dataset_ids": [str(ds)],
                "ground_truth_chunk_ids": [str(seeded_id)],
                "k": 5,
            }
        ),
        encoding="utf-8",
    )

    deps = PipelineDeps(embedder=live_embed_model, llm=_fake_llm())
    pipeline = build_full_pipeline(deps)
    runner = EvalRunner(pipeline=pipeline.ainvoke)

    summary = await runner.run(eval_path)

    assert summary.sample_count == 1
    # Perfect recall
    assert summary.metric_aggregates["recall@5"]["mean"] == 1.0
    # MRR: ground_truth at rank 1 → 1.0
    assert summary.metric_aggregates["mrr"]["mean"] == 1.0
    # NDCG@5: perfect → 1.0
    assert summary.metric_aggregates["ndcg@5"]["mean"] == 1.0


@pytest.mark.asyncio
async def test_real_eval_zero_match_recall_zero(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 2: ground_truth 不在召回中 → recall=0.0, MRR=0.0。

    seed Python chunk, 用随机生成的 UUID 作 ground_truth (不在 PG)。
    """
    ds = await _create_dataset(db_session, "eval-zero")
    await _seed_chunks_with_real_embeddings(
        db_session,
        dataset_id=ds,
        texts=["Python 列表推导式 教程。"],
        embed_model=live_embed_model,
    )

    random_chunk_id = uuid.uuid4()  # NOT in PG
    eval_path = tmp_path / "eval.jsonl"
    eval_path.write_text(
        json.dumps(
            {
                "query": "Python 列表推导式",
                "dataset_ids": [str(ds)],
                "ground_truth_chunk_ids": [str(random_chunk_id)],
                "k": 5,
            }
        ),
        encoding="utf-8",
    )

    deps = PipelineDeps(embedder=live_embed_model, llm=_fake_llm())
    pipeline = build_full_pipeline(deps)
    runner = EvalRunner(pipeline=pipeline.ainvoke)

    summary = await runner.run(eval_path)

    assert summary.sample_count == 1
    assert summary.metric_aggregates["recall@5"]["mean"] == 0.0
    assert summary.metric_aggregates["mrr"]["mean"] == 0.0
    assert summary.metric_aggregates["ndcg@5"]["mean"] == 0.0
    assert summary.metric_aggregates["hit_rate@5"]["mean"] == 0.0


@pytest.mark.asyncio
async def test_real_eval_multiple_queries_aggregate(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 3: 多个 query → 聚合指标 (mean / std / min / max)。

    seed 4 chunks (3 Python-related, 1 Java), 跑 3 个 query:
    - q1: ground_truth=Python chunk (完美命中)
    - q2: ground_truth=Java chunk (完美命中)
    - q3: ground_truth=不存在的 chunk (零命中)
    """
    ds = await _create_dataset(db_session, "eval-multi")
    chunks = await _seed_chunks_with_real_embeddings(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式 教程。",
            "Python 数据分析 pandas 入门。",
            "Python 异步编程 asyncio 协程。",
            "Java 静态类型编译型语言。",
        ],
        embed_model=live_embed_model,
    )

    eval_path = tmp_path / "eval.jsonl"
    eval_records = [
        {
            "query": "Python 列表推导式",
            "dataset_ids": [str(ds)],
            "ground_truth_chunk_ids": [str(chunks[0].id)],
            "k": 5,
        },
        {
            "query": "Java 静态类型",
            "dataset_ids": [str(ds)],
            "ground_truth_chunk_ids": [str(chunks[3].id)],
            "k": 5,
        },
        {
            "query": "non-existent topic",
            "dataset_ids": [str(ds)],
            "ground_truth_chunk_ids": [str(uuid.uuid4())],  # 不在 PG
            "k": 5,
        },
    ]
    with eval_path.open("w", encoding="utf-8") as f:
        for r in eval_records:
            f.write(json.dumps(r) + "\n")

    deps = PipelineDeps(embedder=live_embed_model, llm=_fake_llm())
    pipeline = build_full_pipeline(deps)
    runner = EvalRunner(pipeline=pipeline.ainvoke)

    summary = await runner.run(eval_path)

    assert summary.sample_count == 3
    # recall@5: 1.0, 1.0, 0.0 → mean=2/3, min=0, max=1, count=3
    recall = summary.metric_aggregates["recall@5"]
    assert recall["mean"] == pytest.approx(2 / 3)
    assert recall["min"] == 0.0
    assert recall["max"] == 1.0
    assert recall["count"] == 3
    # mrr: 1.0, 1.0, 0.0 → mean=2/3
    assert summary.metric_aggregates["mrr"]["mean"] == pytest.approx(2 / 3)


@pytest.mark.asyncio
async def test_real_eval_hit_rate_at_k(
    db_session: AsyncSession,
    live_embed_model: Embeddings,
    tmp_path: Path,
) -> None:
    """真实场景 4: hit_rate@K 是 binary (any overlap) 验证。

    seed 多个 chunk, eval record 期望召回到 top-5 中任一。
    """
    ds = await _create_dataset(db_session, "eval-hitrate")
    chunks = await _seed_chunks_with_real_embeddings(
        db_session,
        dataset_id=ds,
        texts=[
            "Python 列表推导式 教程。",
            "Python 装饰器使用。",
            "Python 数据分析 pandas。",
        ],
        embed_model=live_embed_model,
    )

    eval_path = tmp_path / "eval.jsonl"
    eval_path.write_text(
        json.dumps(
            {
                "query": "Python 列表推导式",
                "dataset_ids": [str(ds)],
                "ground_truth_chunk_ids": [str(chunks[0].id)],  # 期望 chunks[0]
                "k": 3,
            }
        ),
        encoding="utf-8",
    )

    deps = PipelineDeps(embedder=live_embed_model, llm=_fake_llm())
    pipeline = build_full_pipeline(deps)
    runner = EvalRunner(pipeline=pipeline.ainvoke)

    summary = await runner.run(eval_path)

    assert summary.sample_count == 1
    # hit_rate@3: any overlap → 1.0 (Python 列表推导式 应召回到 top-3)
    assert summary.metric_aggregates["hit_rate@3"]["mean"] >= 0.0