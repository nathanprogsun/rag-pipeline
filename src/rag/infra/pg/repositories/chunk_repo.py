import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from rag.domain.document import Chunk as DomainChunk
from rag.infra.pg.mappers import chunk_model_list_to_domain, domain_chunk_to_model
from rag.infra.pg.models.chunk import ChunkModel


class ChunkRepository:
    """Chunk 数据访问 (Repository 模式). Session 由调用方注入。

    公共 API 一律返回 / 接受 ``DomainChunk``, ChunkModel 仅在 SQLAlchemy
    查询内部使用, 通过 mapper 转换。这样 retriever / service 层不再直接
    依赖 ORM 类, 字段映射集中到 ``infra.pg.mappers``。
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search_by_vector(
        self,
        query_vec: list[float],
        dataset_id: uuid.UUID,
        top_k: int = 10,
    ) -> list[tuple[DomainChunk, float]]:
        await self.session.execute(
            text("SET LOCAL hnsw.ef_search = :ef").bindparams(ef=max(top_k * 2, 40))
        )
        stmt = (
            select(
                ChunkModel,
                (1 - ChunkModel.embedding.cosine_distance(query_vec)).label("score"),
            )
            .where(ChunkModel.dataset_id == dataset_id)
            .order_by(ChunkModel.embedding.cosine_distance(query_vec))
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        models = [row.ChunkModel for row in rows]
        scores = [float(row.score) for row in rows]
        return list(zip(chunk_model_list_to_domain(models), scores, strict=True))

    async def search_by_fulltext(
        self,
        ts_query: str,
        dataset_id: uuid.UUID,
        top_k: int = 10,
    ) -> list[tuple[DomainChunk, float]]:
        stmt = (
            select(
                ChunkModel,
                func.ts_rank(
                    ChunkModel.ts_tokens, func.to_tsquery("simple", ts_query)
                ).label("score"),
            )
            .where(
                ChunkModel.dataset_id == dataset_id,
                ChunkModel.ts_tokens.op("@@")(func.to_tsquery("simple", ts_query)),
            )
            .order_by(
                func.ts_rank(
                    ChunkModel.ts_tokens, func.to_tsquery("simple", ts_query)
                ).desc()
            )
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        models = [row.ChunkModel for row in rows]
        scores = [float(row.score) for row in rows]
        return list(zip(chunk_model_list_to_domain(models), scores, strict=True))

    async def delete_by_filename(self, dataset_id: uuid.UUID, filename: str) -> None:
        await self.session.execute(
            update(ChunkModel)
            .where(
                and_(
                    ChunkModel.dataset_id == dataset_id,
                    ChunkModel.filename == filename,
                    ChunkModel.deleted_at.is_(None),
                )
            )
            .values(deleted_at=func.now())
        )

    async def bulk_insert(self, chunks: list[DomainChunk]) -> None:
        """业务层 DomainChunk 列表写入。 mapper 负责 DomainChunk -> ChunkModel。

        本方法 ``flush()`` 但不 ``commit()``: 触发 auto-increment / unique
        约束错误在方法内抛出, 调用方仍按契约负责 ``commit()`` / 包裹在
        :meth:`transaction` 中。
        """
        self.session.add_all(domain_chunk_to_model(c) for c in chunks)
        await self.session.flush()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Repository 级事务上下文: 进入时暂不 begin, 退出时 commit。

        正常结束 -> ``commit()``; 异常 -> ``rollback()`` 后重新抛。 用于
        多 repo 方法需要原子写入的场景, 调用方不再手动 commit / rollback::

            async with chunk_repo.transaction():
                await chunk_repo.bulk_insert(chunks)
                await other_repo.touch(dataset_id)

        注意: session 仍由外层调用方注入并负责生命周期 (close)。 嵌套时
        SQLAlchemy 2.x 的 AsyncSession 通过 SAVEPOINT 处理, 但本 helper
        设计为单层使用。
        """
        try:
            yield
        except Exception:
            await self.session.rollback()
            raise
        else:
            await self.session.commit()

    async def get_siblings(
        self,
        dataset_id: uuid.UUID,
        parent_title: str,
        lo: int,  # lower index, 下限
        hi: int,  # higher index, 上限
    ) -> list[DomainChunk]:
        stmt = (
            select(ChunkModel)
            .where(
                and_(
                    ChunkModel.dataset_id == dataset_id,
                    ChunkModel.parent_title == parent_title,
                    ChunkModel.chunk_index >= lo,
                    ChunkModel.chunk_index <= hi,
                )
            )
            .order_by(ChunkModel.chunk_index)
        )
        result = await self.session.execute(stmt)
        return chunk_model_list_to_domain(list(result.scalars().all()))

    async def count_by_dataset(self, dataset_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(ChunkModel)
            .where(ChunkModel.dataset_id == dataset_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0
