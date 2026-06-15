import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import and_, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from rag.domain.document import Chunk as DomainChunk
from rag.infra.pg.mappers import chunk_model_list_to_domain, domain_chunk_to_model
from rag.infra.pg.models.chunk import ChunkModel


class ChunkRepository:
    """`Chunk` 数据仓储。`AsyncSession` 由调用方注入，公共 API 全部以 `DomainChunk` 形式收发。"""

    def __init__(self, session: AsyncSession) -> None:
        """初始化仓储。

        Args:
            session: 外部注入的 SQLAlchemy 异步会话。
        """
        self.session = session

    async def search_by_vector(
        self,
        query_vec: list[float],
        dataset_id: uuid.UUID,
        top_k: int = 10,
    ) -> list[tuple[DomainChunk, float]]:
        """按余弦相似度检索 top_k 文本块。

        Args:
            query_vec: 查询向量。
            dataset_id: 数据集 ID。
            top_k: 返回结果数量。

        Returns:
            `(DomainChunk, score)` 元组列表，按相似度降序。
        """
        # `SET LOCAL` 不支持参数化；`ef_search` 为整数, 无注入风险
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
        """按全文检索相关度检索 top_k 文本块。

        Args:
            ts_query: 已经过分词的 `&` 连接查询串。
            dataset_id: 数据集 ID。
            top_k: 返回结果数量。

        Returns:
            `(DomainChunk, score)` 元组列表，按 `ts_rank` 降序。
        """
        # 使用 `websearch_to_tsquery` 解析带空格的输入, 避免 `to_tsquery` 语法错误
        ts_query_expr = func.websearch_to_tsquery("simple", ts_query)
        stmt = (
            select(
                ChunkModel,
                func.ts_rank(ChunkModel.ts_tokens, ts_query_expr).label("score"),
            )
            .where(
                ChunkModel.dataset_id == dataset_id,
                ChunkModel.ts_tokens.op("@@")(ts_query_expr),
            )
            .order_by(func.ts_rank(ChunkModel.ts_tokens, ts_query_expr).desc())
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        rows = result.all()
        models = [row.ChunkModel for row in rows]
        scores = [float(row.score) for row in rows]
        return list(zip(chunk_model_list_to_domain(models), scores, strict=True))

    async def delete_by_filename(self, dataset_id: uuid.UUID, filename: str) -> None:
        """按文件名软删除（写入 `deleted_at`）。"""
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

    async def soft_delete_by_filename(
        self, dataset_id: uuid.UUID, filename: str
    ) -> int:
        """按文件名软删除, 返回受影响行数。

        与 `delete_by_filename` 行为一致, 区别是显式返回受影响行数, 供
        PersistStage 决定是否需要新插入。
        """
        result = await self.session.execute(
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
        return int(result.rowcount or 0)

    async def bulk_insert(self, chunks: list[DomainChunk]) -> None:
        """批量写入 `DomainChunk` 列表。

        本方法 `flush()` 但不 `commit()`：约束错误立即抛出，由调用方
        决定 `commit()` 或通过 `transaction()` 上下文回滚。
        """
        self.session.add_all(domain_chunk_to_model(c) for c in chunks)
        await self.session.flush()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """仓储级事务上下文：正常结束提交，异常回滚后重新抛。

        用于多步写入需原子的场景，调用方无需手动 `commit` / `rollback`。

        注意：session 生命周期仍由外层负责；本 helper 设计为单层使用。
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
        lo: int,  # `chunk_index` 下限
        hi: int,  # `chunk_index` 上限
    ) -> list[DomainChunk]:
        """按 `parent_title` 与索引区间取相邻文本块。"""
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
        """统计数据集内未删除的文本块数量。"""
        stmt = (
            select(func.count())
            .select_from(ChunkModel)
            .where(ChunkModel.dataset_id == dataset_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0
