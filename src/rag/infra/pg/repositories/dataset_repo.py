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

from rag.infra.pg.models.dataset import DatasetModel
from rag.infra.pg.models.document import DocumentModel


class DatasetListItem(BaseModel):
    """`list-datasets` 子命令的查询结果投影。"""

    id: uuid.UUID
    name: str
    embed_model: str
    chunk_count: int
    """该 dataset 下所有未软删文档的 ``total_chunks`` 之和。

    P2 之后由 ``documents.total_chunks`` 维护；不含软删文档对应的 chunks。
    """
    created_at: datetime


class DatasetRepository:
    """`Dataset` 数据仓储, 负责 dataset 元数据与 chunk 计数查询。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化仓储。

        Args:
            session: 外部注入的 SQLAlchemy 异步会话。
        """
        self.session = session

    async def get_by_id(
        self, dataset_id: uuid.UUID, *, include_deleted: bool = False
    ) -> DatasetModel | None:
        """按 UUID 取 dataset; 不存在或已软删返回 None。

        Args:
            dataset_id: dataset UUID。
            include_deleted: True 时返回软删除的 dataset, False 时过滤。

        Returns:
            `DatasetModel` 或 None。
        """
        stmt = select(DatasetModel).where(DatasetModel.id == dataset_id)
        if not include_deleted:
            stmt = stmt.where(DatasetModel.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        name: str,
        embed_model: str,
        embed_dim: int,
        chunk_size: int = 1000,
        rerank_model: str | None = None,
        rrf_k: int = 60,
        vector_weight: float = 0.7,
        fulltext_weight: float = 0.3,
        query_select_alpha: float = 0.3,
        prompt_template: str = "",
        system_prompt: str | None = None,
    ) -> DatasetModel:
        """新建 dataset 行, `flush()` 但不 `commit()`。

        业务方负责 commit / rollback。返回新建的 ORM 实例。

        Args:
            name: dataset 展示名 (需 unique, 但此处不强制; 由调用方决定)。
            embed_model: 用于此 dataset 的 embedding 模型名。
            embed_dim: embedding 维度, 需与 `chunks.embedding` 列维度一致。
            chunk_size: 默认 chunk 大小 (记录下来, 给后续 chunker 提示)。
            rerank_model: 可选 rerank 模型。
            rrf_k: RRF k 常量。
            vector_weight / fulltext_weight: 融合权重。
            query_select_alpha: submodular α。
            prompt_template: 提示模板, 默认空 (上层用 DEFAULT_PROMPT_TEMPLATE)。
            system_prompt: 系统提示, 可空。

        Returns:
            新建的 `DatasetModel` (含自动生成的 id)。
        """
        model = DatasetModel(
            name=name,
            embed_model=embed_model,
            embed_dim=embed_dim,
            chunk_size=chunk_size,
            rerank_model=rerank_model,
            rrf_k=rrf_k,
            vector_weight=vector_weight,
            fulltext_weight=fulltext_weight,
            query_select_alpha=query_select_alpha,
            prompt_template=prompt_template,
            system_prompt=system_prompt,
        )
        self.session.add(model)
        await self.session.flush()
        return model

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

        Note:
            `chunk_count` 由 ``documents.total_chunks`` 维护 (P2+),
            仅统计未软删文档的 chunks 之和; 与旧实现 (直接 `COUNT(chunks.id)`,
            含软删块) 在数值上可能不同。
        """
        # 子查询: 每个 dataset 的 chunk_count = SUM(documents.total_chunks) of active docs
        chunk_count_sq = (
            select(
                DocumentModel.dataset_id.label("dataset_id"),
                func.coalesce(func.sum(DocumentModel.total_chunks), 0).label("n"),
            )
            .where(DocumentModel.deleted_at.is_(None))
            .group_by(DocumentModel.dataset_id)
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
            .outerjoin(chunk_count_sq, chunk_count_sq.c.dataset_id == DatasetModel.id)
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
