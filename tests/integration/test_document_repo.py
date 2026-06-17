"""Integration tests for DocumentRepository."""

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from rag.infra.pg.repositories.dataset_repo import DatasetRepository
from rag.infra.pg.repositories.document_repo import DocumentRepository


@pytest_asyncio.fixture
async def dataset_id(db_session: AsyncSession) -> uuid.UUID:
    """创建一个测试 dataset (不 commit)。"""
    repo = DatasetRepository(db_session)
    ds = await repo.create(
        name=f"test-ds-{uuid.uuid4()}",
        embed_model="text-embedding-3-small",
        embed_dim=1536,
    )
    return ds.id


@pytest.mark.asyncio
async def test_get_by_name_returns_active_dataset(
    db_session: AsyncSession, dataset_id: uuid.UUID
) -> None:
    """``get_by_name`` 命中未软删 dataset; 软删后默认查不到。"""
    ds_repo = DatasetRepository(db_session)
    ds = await ds_repo.get_by_id(dataset_id)
    assert ds is not None

    found = await ds_repo.get_by_name(ds.name)
    assert found is not None
    assert found.id == dataset_id

    ds.deleted_at = datetime.now(UTC)
    await db_session.flush()

    assert await ds_repo.get_by_name(ds.name) is None
    found_deleted = await ds_repo.get_by_name(ds.name, include_deleted=True)
    assert found_deleted is not None
    assert found_deleted.id == dataset_id


@pytest.mark.asyncio
async def test_upsert_creates_then_increments(
    db_session: AsyncSession, dataset_id: uuid.UUID
) -> None:
    """首次 upsert 生成 generation=1, 再次 upsert 同一 (dataset, filename) 则 generation=2。"""
    repo = DocumentRepository(db_session)
    doc1 = await repo.upsert(dataset_id=dataset_id, filename="a.txt")
    assert doc1.generation == 1
    assert doc1.status == "running"

    doc2 = await repo.upsert(dataset_id=dataset_id, filename="a.txt")
    assert doc2.generation == 2
    assert doc2.id == doc1.id  # same row, generation bumped


@pytest.mark.asyncio
async def test_get_active_returns_latest_generation(
    db_session: AsyncSession, dataset_id: uuid.UUID
) -> None:
    repo = DocumentRepository(db_session)
    await repo.upsert(dataset_id=dataset_id, filename="b.txt", total_chunks=10)
    await repo.upsert(dataset_id=dataset_id, filename="b.txt", total_chunks=20)
    doc = await repo.get_active(dataset_id, "b.txt")
    assert doc is not None
    assert doc.generation == 2
    assert doc.total_chunks == 20


@pytest.mark.asyncio
async def test_mark_status_updates(
    db_session: AsyncSession, dataset_id: uuid.UUID
) -> None:
    repo = DocumentRepository(db_session)
    doc = await repo.upsert(dataset_id=dataset_id, filename="c.txt")
    await repo.mark_status(doc.id, "failed", error_code="OOM")
    refreshed = await repo.get_active(dataset_id, "c.txt")
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.error_code == "OOM"


@pytest.mark.asyncio
async def test_list_by_dataset(db_session: AsyncSession, dataset_id: uuid.UUID) -> None:
    repo = DocumentRepository(db_session)
    await repo.upsert(dataset_id=dataset_id, filename="d1.txt")
    await repo.upsert(dataset_id=dataset_id, filename="d2.txt")
    docs = await repo.list_by_dataset(dataset_id)
    filenames = sorted(d.filename for d in docs)
    assert filenames == ["d1.txt", "d2.txt"]
