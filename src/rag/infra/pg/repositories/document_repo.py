"""`Document` 数据仓储: documents 表的 upsert / get / list 操作。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from rag.infra.pg.models.document import DocumentModel


class DocumentRepository:
    """`Document` 数据仓储。

    所有方法接收外部注入的 ``AsyncSession``; 事务边界 (commit / rollback)
    由调用方负责 (除非方法内显式调用 session.flush / commit)。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(
        self,
        *,
        dataset_id: uuid.UUID,
        filename: str,
        content_hash: bytes | None = None,
        modality: str = "text",
        page_count: int | None = None,
        total_chunks: int = 0,
    ) -> DocumentModel:
        """upsert: 找 active `(dataset_id, filename)`, 找到则 generation+=1, 否则 INSERT。

        ``flush()`` 但不 ``commit()``, 事务边界由调用方负责。
        """
        stmt = (
            select(DocumentModel)
            .where(
                DocumentModel.dataset_id == dataset_id,
                DocumentModel.filename == filename,
                DocumentModel.deleted_at.is_(None),
            )
            .with_for_update()
            .order_by(DocumentModel.generation.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.generation += 1
            existing.content_hash = content_hash
            existing.modality = modality
            existing.page_count = page_count
            existing.total_chunks = total_chunks
            existing.status = "running"
            existing.error_code = None
            existing.last_processed_at = datetime.now(timezone.utc)
            await self.session.flush()
            return existing
        model = DocumentModel(
            dataset_id=dataset_id,
            filename=filename,
            content_hash=content_hash,
            modality=modality,
            page_count=page_count,
            total_chunks=total_chunks,
            status="running",
            generation=1,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def mark_status(
        self, document_id: uuid.UUID, status: str, *, error_code: str | None = None
    ) -> None:
        """更新 document.status (与 error_code)。flush 但不 commit。"""
        await self.session.execute(
            update(DocumentModel)
            .where(DocumentModel.id == document_id)
            .values(status=status, error_code=error_code)
        )
        await self.session.flush()

    async def get_active(
        self, dataset_id: uuid.UUID, filename: str
    ) -> DocumentModel | None:
        """取当前 active document (按 generation desc 排序第一个)。"""
        stmt = (
            select(DocumentModel)
            .where(
                DocumentModel.dataset_id == dataset_id,
                DocumentModel.filename == filename,
                DocumentModel.deleted_at.is_(None),
            )
            .order_by(DocumentModel.generation.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_dataset(
        self, dataset_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Sequence[DocumentModel]:
        """列出 dataset 下所有 document (按 created_at desc)。"""
        stmt = (
            select(DocumentModel)
            .where(DocumentModel.dataset_id == dataset_id)
            .order_by(DocumentModel.created_at.desc())
        )
        if not include_deleted:
            stmt = stmt.where(DocumentModel.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalars().all()
