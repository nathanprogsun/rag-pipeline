"""PG 集成测试 — 在 `settings.database_url` 指向的真实 PG 上验证 infra 层。

依赖 `conftest.db_session`：读配置连库 → 建表 → 本文件中的用例。
建议用专用测试库，见 `conftest` 模块说明。
"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel
from rag.infra.pg.repositories.chunk_repo import ChunkRepository


@pytest.mark.asyncio
async def test_schema_creates_datasets_table(db_session: AsyncSession) -> None:
    """Step 1: ORM create_all 后，datasets 表真实存在于 PG 中。"""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='datasets' AND column_name='id'"
        )
    )
    assert result.scalar() == "id"


@pytest.mark.asyncio
async def test_extension_vector_enabled(db_session: AsyncSession) -> None:
    """Step 2: pgvector 扩展在真实库中已启用。"""
    result = await db_session.execute(
        text("SELECT extname FROM pg_extension WHERE extname='vector'")
    )
    assert result.scalar() == "vector"


@pytest.mark.asyncio
async def test_chunk_repository_roundtrip(db_session: AsyncSession) -> None:
    """Step 3: Repository CRUD — Create(dataset+chunk) → Read(向量检索)。"""
    ds = DatasetModel(id=uuid.uuid4(), name="t", embed_model="m", embed_dim=1536)
    db_session.add(ds)
    await db_session.flush()

    c = ChunkModel(
        dataset_id=ds.id,
        text="test content",
        embedding=[0.0] * 1535 + [1.0],
    )
    db_session.add(c)
    await db_session.commit()

    repo = ChunkRepository(db_session)
    query_vec = [0.0] * 1535 + [1.0]
    results = await repo.search_by_vector(query_vec, ds.id, top_k=1)
    assert len(results) == 1
    assert results[0][0].text == "test content"


@pytest.mark.asyncio
async def test_soft_delete_excludes_rows(db_session: AsyncSession) -> None:
    """Step 4: 软删除 — repo 设 deleted_at，ORM 查询不可见，行仍在库中。"""
    ds = DatasetModel(id=uuid.uuid4(), name="t", embed_model="m", embed_dim=1536)
    db_session.add(ds)
    await db_session.flush()

    c = ChunkModel(
        dataset_id=ds.id,
        text="to delete",
        filename="doc.pdf",
        embedding=[0.0] * 1535 + [1.0],
    )
    db_session.add(c)
    await db_session.commit()

    repo = ChunkRepository(db_session)
    query_vec = [0.0] * 1535 + [1.0]
    assert len(await repo.search_by_vector(query_vec, ds.id, top_k=5)) == 1
    assert await repo.count_by_dataset(ds.id) == 1

    await repo.delete_by_filename(ds.id, "doc.pdf")
    await db_session.commit()

    # Read 路径：全局软删过滤生效，repository 查不到
    assert len(await repo.search_by_vector(query_vec, ds.id, top_k=5)) == 0
    assert await repo.count_by_dataset(ds.id) == 0

    # 行未物理删除，deleted_at 已写入（绕过 ORM 过滤器直查 PG）
    row = (
        await db_session.execute(
            text(
                "SELECT deleted_at, text FROM chunks "
                "WHERE dataset_id = :ds AND filename = :fn"
            ),
            {"ds": ds.id, "fn": "doc.pdf"},
        )
    ).one()
    assert row.deleted_at is not None
    assert row.text == "to delete"
