"""`Dataset` 数据仓储。

提供列表查询 (`list`), 配合 `rag-search list-datasets` 子命令
让用户在执行检索前先发现可用的 dataset。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel


class DatasetListItem(BaseModel):
    """`list-datasets` 子命令的查询结果投影。"""

    id: uuid.UUID
    name: str
    embed_model: str
    chunk_count: int
    created_at: datetime


class DatasetRepository:
    """`Dataset` 数据仓储, 负责 dataset 元数据与 chunk 计数查询。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化仓储。

        Args:
            session: 外部注入的 SQLAlchemy 异步会话。
        """
        self.session = session

    async def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        name_contains: str | None = None,
        include_deleted: bool = False,
    ) -> list[DatasetListItem]:
        """列出 dataset 及 chunk 数量, 按 `created_at` 降序。

        Args:
            limit: 最多返回条数。
            offset: 分页偏移。
            name_contains: `name` 模糊匹配 (LIKE '%...%'), 大小写敏感。
            include_deleted: 是否包含软删除 (`deleted_at IS NOT NULL`) 的 dataset。

        Returns:
            `DatasetListItem` 列表, 每项含 `chunk_count` (子查询聚合)。
        """
        # 子查询: 每个 dataset 的 chunk 数量 (含软删除块, 与 dataset 删除状态解耦)
        chunk_count_sq = (
            select(
                ChunkModel.dataset_id.label("dataset_id"),
                func.count(ChunkModel.id).label("n"),
            )
            .group_by(ChunkModel.dataset_id)
            .subquery()
        )
        stmt = (
            select(
                DatasetModel.id,
                DatasetModel.name,
                DatasetModel.embed_model,
                DatasetModel.created_at,
                func.coalesce(chunk_count_sq.c.n, 0).label("chunk_count"),
            )
            .outerjoin(
                chunk_count_sq, chunk_count_sq.c.dataset_id == DatasetModel.id
            )
            .order_by(DatasetModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if not include_deleted:
            stmt = stmt.where(DatasetModel.deleted_at.is_(None))
        if name_contains:
            stmt = stmt.where(DatasetModel.name.contains(name_contains))

        result = await self.session.execute(stmt)
        rows = result.all()
        return [
            DatasetListItem(
                id=row.id,
                name=row.name,
                embed_model=row.embed_model,
                chunk_count=int(row.chunk_count),
                created_at=row.created_at,
            )
            for row in rows
        ]
