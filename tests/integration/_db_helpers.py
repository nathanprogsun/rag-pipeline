"""集成测试共享 DB 工具: dataset / chunk 构造 + tsvector 填充 + LLM fake。

8 个 ``test_*_live.py`` 各自复制了一份:
- ``_create_dataset`` (10 行)
- ``_seed_chunks`` / ``_seed_chunks_with_real_embeddings`` (15-25 行)
- ``UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) WHERE id = :id`` (4 行, 11 处)
- ``_fake_llm`` (6 行, 2 处)

抽到本模块后, 各 test 文件改 ``from tests.integration._db_helpers import ...``。

使用要点:
- 实际 PG 集成, 需 ``db_session`` fixture 或自己接 engine
- embedding 调用真实模型 (live_embed_model fixture), 无 mock
- ``_set_ts_tokens`` 单 chunk 写入 tsvector, 调用方负责循环
- ``_fake_llm`` 返回 ``AIMessage``-like 对象, 满足 chat_model.ainvoke 契约
"""

from __future__ import annotations

import uuid

from langchain_core.embeddings import Embeddings
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.config import settings
from rag.infra.pg.chinese_tokenizer import ChineseTokenizer
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel

# 与 settings.openai_embedding_dim 对齐; 8 个 test_*_live.py 各定义一份, 沉到此处
EMBED_DIM: int = settings.openai_embedding_dim


async def create_dataset(
    db_session: AsyncSession,
    name: str,
    *,
    embed_model: str | None = None,
    embed_dim: int = EMBED_DIM,
) -> uuid.UUID:
    """在真实 PG 中创建 dataset 行。

    Args:
        db_session: 外部传入的 SQLAlchemy 异步会话。
        name: dataset 展示名。
        embed_model: 覆盖默认的 ``settings.openai_embedding_model``; 用于特殊测试
            (如 ``test_subgraph_live`` 用 ``"fake"`` 跳过真实 embed)。
        embed_dim: 覆盖默认 ``EMBED_DIM``; 同上。

    Returns:
        新建 dataset 的 UUID。
    """
    ds = DatasetModel(
        id=uuid.uuid4(),
        name=name,
        embed_model=embed_model or settings.openai_embedding_model,
        embed_dim=embed_dim,
    )
    db_session.add(ds)
    await db_session.flush()
    return ds.id


async def seed_chunks(
    db_session: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    texts: list[str],
    embed_model: Embeddings,
    fill_tsvector: bool = True,
) -> list[ChunkModel]:
    """真实 embedding + 可选 tsvector 填充, 批量入库 chunk。

    Args:
        db_session: 外部传入的会话。
        dataset_id: 目标 dataset UUID。
        texts: chunk 文本列表。
        embed_model: 用于 ``aembed_documents`` 的 LangChain Embeddings。
        fill_tsvector: True 时额外用 ChineseTokenizer 填充 ``ts_tokens`` 列
            (供 fulltext 检索); 测试纯向量语义时关掉可省 IO。

    Returns:
        新建的 ``ChunkModel`` 列表 (含分配 id 与 embedding)。
    """
    embeddings: list[list[float]] = await embed_model.aembed_documents(texts)
    chunks: list[ChunkModel] = []
    for content, emb in zip(texts, embeddings, strict=True):
        chunk = ChunkModel(dataset_id=dataset_id, text=content, embedding=emb)
        db_session.add(chunk)
        chunks.append(chunk)
    await db_session.flush()
    if fill_tsvector:
        for chunk in chunks:
            await set_ts_tokens(db_session, chunk)
    await db_session.commit()
    return chunks


async def set_ts_tokens(db_session: AsyncSession, chunk: ChunkModel) -> None:
    """单 chunk 写 tsvector (Chinese-tokenizer 切词)。

    11 处 ``UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) WHERE id = :id``
    的语义统一, 抽此函数。
    """
    await db_session.execute(
        text("UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) WHERE id = :id"),
        {"t": ChineseTokenizer().build_tsvector(chunk.text), "id": chunk.id},
    )


class _FakeAIMessage:
    """``_fake_llm`` 返回的最小可调用对象, 模拟 LangChain ``AIMessage.content``。"""


def fake_llm(default_response: str = "") -> object:
    """构造一个 ``ainvoke`` 返固定 ``content`` 的 stub chat model。

    2 个 test (test_eval_live / test_pipeline_full_live) 用了几乎一样的模式。
    满足 ``StructureNormalizer._invoke_llm`` 与 ``make_llm_gen`` 的契约
    (``ainvoke(messages) -> 对象 with .content``)。

    Args:
        default_response: ``ainvoke`` 返回对象的 ``content`` 字段。

    Returns:
        一个有 ``ainvoke`` async 方法的匿名对象。
    """

    class _Stub:
        async def ainvoke(
            self,
            messages: object,
            **kwargs: object,
        ) -> _FakeAIMessage:
            return _FakeAIMessage(content=default_response)

    return _Stub()
