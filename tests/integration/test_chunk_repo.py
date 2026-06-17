"""ChunkRepository 集成测试 — class 形式，真实 PG 上验证各 repo 方法。"""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata as DomainChunkMetadata
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel
from rag.infra.pg.models.document import DocumentModel
from rag.infra.pg.repositories.chunk_repo import ChunkRepository

EMBED_DIM = 1536


def _embedding(tail: float = 1.0) -> list[float]:
    return [0.0] * (EMBED_DIM - 1) + [tail]


async def _create_dataset(
    db_session: AsyncSession, name: str = "repo-test"
) -> uuid.UUID:
    ds = DatasetModel(
        id=uuid.uuid4(), name=name, embed_model="text-embedding-v3", embed_dim=EMBED_DIM
    )
    db_session.add(ds)
    await db_session.flush()
    return ds.id


async def _create_document(
    db_session: AsyncSession,
    dataset_id: uuid.UUID,
    *,
    filename: str = "test-doc.txt",
    total_chunks: int = 1,
) -> uuid.UUID:
    """创建 document 行并 flush, 返回新 document.id。

    测试用的 ``chunks`` 行的 ``document_id`` 必须指向一个已存在的
    ``documents`` 行, 否则违反 ``chunks_document_id_fkey``。
    """
    doc = DocumentModel(
        dataset_id=dataset_id,
        filename=filename,
        status="completed",
        total_chunks=total_chunks,
    )
    db_session.add(doc)
    await db_session.flush()
    return doc.id


async def _set_tsvector(
    db_session: AsyncSession, chunk_id: uuid.UUID, content: str
) -> None:
    await db_session.execute(
        text("UPDATE chunks SET ts_tokens = to_tsvector('simple', :t) WHERE id = :id"),
        {"t": content, "id": chunk_id},
    )


@pytest.mark.asyncio
class TestChunkRepository:
    async def test_search_by_vector_returns_scored_chunks(
        self, db_session: AsyncSession, chunk_repo: ChunkRepository
    ) -> None:
        dataset_id = await _create_dataset(db_session)
        document_id = await _create_document(db_session, dataset_id, filename="v.txt")
        db_session.add(
            ChunkModel(
                dataset_id=dataset_id,
                document_id=document_id,
                text="vector hit",
                embedding=_embedding(1.0),
            )
        )
        await db_session.commit()

        results = await chunk_repo.search_by_vector(
            _embedding(1.0), dataset_id, top_k=1
        )

        assert len(results) == 1
        chunk, score = results[0]
        assert chunk.text == "vector hit"
        assert score > 0.9

    async def test_search_by_fulltext_ranks_matching_chunks(
        self, db_session: AsyncSession, chunk_repo: ChunkRepository
    ) -> None:
        dataset_id = await _create_dataset(db_session)
        document_id = await _create_document(
            db_session, dataset_id, filename="ft.txt", total_chunks=2
        )
        match = ChunkModel(
            dataset_id=dataset_id,
            document_id=document_id,
            text="postgresql vector fulltext",
            chunk_index=0,
            embedding=_embedding(),
        )
        miss = ChunkModel(
            dataset_id=dataset_id,
            document_id=document_id,
            text="unrelated content only",
            chunk_index=1,
            embedding=_embedding(0.5),
        )
        db_session.add_all([match, miss])
        await db_session.flush()
        await _set_tsvector(db_session, match.id, match.text)
        await _set_tsvector(db_session, miss.id, miss.text)
        await db_session.commit()

        results = await chunk_repo.search_by_fulltext("postgresql", dataset_id, top_k=5)

        assert len(results) == 1
        assert results[0][0].text == "postgresql vector fulltext"
        assert results[0][1] > 0

    async def test_bulk_insert_and_count(
        self, db_session: AsyncSession, chunk_repo: ChunkRepository
    ) -> None:
        dataset_id = await _create_dataset(db_session)
        document_id = await _create_document(
            db_session, dataset_id, filename="bulk.txt", total_chunks=3
        )
        chunks = [
            DomainChunk(
                id=uuid.uuid4(),
                dataset_id=dataset_id,
                document_id=document_id,
                text=f"chunk-{i}",
                modality="text",
                metadata=DomainChunkMetadata(
                    dataset_id=dataset_id,
                    datasource="file",
                    chunk_index=i,
                ),
                embedding=_embedding(i / EMBED_DIM),
            )
            for i in range(3)
        ]

        await chunk_repo.bulk_insert(chunks)
        await db_session.commit()

        assert await chunk_repo.count_by_dataset(dataset_id) == 3

    async def test_get_siblings_returns_ordered_slice(
        self, db_session: AsyncSession, chunk_repo: ChunkRepository
    ) -> None:
        dataset_id = await _create_dataset(db_session)
        document_id = await _create_document(
            db_session, dataset_id, filename="sib.txt", total_chunks=4
        )
        parent = "Chapter 1"
        for i in range(4):
            db_session.add(
                ChunkModel(
                    dataset_id=dataset_id,
                    document_id=document_id,
                    text=f"sibling-{i}",
                    parent_title=parent,
                    chunk_index=i,
                    embedding=_embedding(),
                )
            )
        await db_session.commit()

        siblings = await chunk_repo.get_siblings(dataset_id, parent, lo=1, hi=2)

        assert [c.metadata.chunk_index for c in siblings] == [1, 2]
        assert [c.text for c in siblings] == ["sibling-1", "sibling-2"]

    async def test_delete_by_filename_soft_deletes(
        self, db_session: AsyncSession, chunk_repo: ChunkRepository
    ) -> None:
        dataset_id = await _create_dataset(db_session)
        document_id = await _create_document(
            db_session, dataset_id, filename="report.pdf", total_chunks=1
        )
        db_session.add(
            ChunkModel(
                dataset_id=dataset_id,
                document_id=document_id,
                text="soft delete me",
                filename="report.pdf",
                embedding=_embedding(),
            )
        )
        await db_session.commit()

        assert await chunk_repo.count_by_dataset(dataset_id) == 1

        await chunk_repo.delete_by_filename(dataset_id, "report.pdf")
        await db_session.commit()

        assert await chunk_repo.count_by_dataset(dataset_id) == 0

        row = (
            await db_session.execute(
                text(
                    "SELECT deleted_at, text FROM chunks "
                    "WHERE dataset_id = :ds AND filename = :fn"
                ),
                {"ds": dataset_id, "fn": "report.pdf"},
            )
        ).one()
        assert row.deleted_at is not None
        assert row.text == "soft delete me"
