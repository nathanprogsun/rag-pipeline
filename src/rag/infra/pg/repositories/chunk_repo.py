import uuid

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from rag.infra.pg.models.chunk import ChunkModel


class ChunkRepository:
    """Chunk 数据访问 (Repository 模式). Session 由调用方注入。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def search_by_vector(
        self,
        query_vec: list[float],
        dataset_id: uuid.UUID,
        top_k: int = 10,
    ) -> list[tuple[ChunkModel, float]]:
        await self.session.execute(
            text(f"SET LOCAL hnsw.ef_search = {max(top_k * 2, 40)}")
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
        return [(row.ChunkModel, float(row.score)) for row in result.all()]

    async def search_by_fulltext(
        self,
        ts_query: str,
        dataset_id: uuid.UUID,
        top_k: int = 10,
    ) -> list[tuple[ChunkModel, float]]:
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
        return [(row.ChunkModel, float(row.score)) for row in result.all()]

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

    async def bulk_insert(self, models: list[ChunkModel]) -> None:
        self.session.add_all(models)

    async def get_siblings(
        self,
        dataset_id: uuid.UUID,
        parent_title: str,
        lo: int,
        hi: int,
    ) -> list[ChunkModel]:
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
        return list(result.scalars().all())

    async def count_by_dataset(self, dataset_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(ChunkModel)
            .where(ChunkModel.dataset_id == dataset_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0
