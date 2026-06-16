"""PersistStage: 把 `IngestResult` 写到 PG。

串联: 解析 dataset (id 或新建) -> 批量 embedding -> 同 (dataset, filename) 旧 chunk
软删 -> 批量 insert 新 chunk。整段在单 PG 事务内执行, 任何异常触发 rollback。

幂等策略: 同 (dataset_id, filename) 先软删再 insert。重复 ingest 同一文件
得到更新后的 chunk 集合, 不会产生重复。
"""

from __future__ import annotations

import logging
import uuid

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession

from rag.config import settings
from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata as DomainChunkMetadata
from rag.error_codes import IngestErrorCode
from rag.exception import RAGError
from rag.infra.llm.embed import get_embed_model
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.repositories.dataset_repo import DatasetRepository
from rag.ingest.types import Chunk as IngestChunk
from rag.ingest.types import ChunkMetadata as IngestChunkMetadata
from rag.ingest.types import IngestResult

logger = logging.getLogger(__name__)


async def _create_dataset_once(
    session: AsyncSession,
    dataset_name: str | None,
) -> uuid.UUID:
    """``ingest_many`` 顶部一次性创建 dataset。

    与 `dataset_repo.create` 不同: 后续 ``dataset_repo.create`` 会被 unique 约束保护 (Phase 2)。
    """
    if dataset_name is None:
        raise RAGError(
            code=IngestErrorCode.PERSIST_INVALID_ARGS,
            message="create_dataset=True 必须配 dataset_name",
        )
    repo = DatasetRepository(session)
    ds = await repo.create(
        name=dataset_name,
        embed_model=settings.openai_embedding_model,
        embed_dim=settings.openai_embedding_dim,
    )
    await session.commit()
    logger.info("persist.dataset_created id=%s name=%s", ds.id, ds.name)
    return ds.id


class PersistResult:
    """PersistStage 的输出: dataset_id + 替换/新增的 chunk 数。"""

    def __init__(
        self,
        dataset_id: uuid.UUID,
        dataset_name: str,
        *,
        old_chunk_count: int,
        new_chunk_count: int,
    ) -> None:
        self.dataset_id = dataset_id
        self.dataset_name = dataset_name
        self.old_chunk_count = old_chunk_count
        self.new_chunk_count = new_chunk_count

    def __repr__(self) -> str:
        return (
            f"PersistResult(dataset_id={self.dataset_id}, "
            f"name={self.dataset_name!r}, "
            f"old={self.old_chunk_count}, new={self.new_chunk_count})"
        )


async def persist(
    session: AsyncSession,
    result: IngestResult,
    *,
    dataset_id: uuid.UUID,
    embedder: Embeddings | None = None,
) -> PersistResult:
    """把 `IngestResult.chunks` 写入 PG。``dataset_id`` 必传。

    Args:
        session: 外部注入的 SQLAlchemy 异步会话。
        result: `IngestPipeline._process()` 输出。
        dataset_id: 落库目标 dataset 的 UUID (由 pipeline 顶部解析)。
        embedder: 可选 LangChain `Embeddings`; 缺省时按 `settings.openai_embedding_*` 自动构造。

    Returns:
        `PersistResult`, 含 dataset_id 与新旧 chunk 数。

    Raises:
        RAGError: dataset_id 不存在。
    """
    dataset_repo = DatasetRepository(session)
    ds = await dataset_repo.get_by_id(dataset_id)
    if ds is None:
        raise RAGError(
            code=IngestErrorCode.PERSIST_DATASET_NOT_FOUND,
            message=f"dataset_id {dataset_id} 不存在或已软删除",
        )

    # 2. 批量 embedding
    if embedder is None:
        embedder = get_embed_model()
    chunks = result.chunks
    if not chunks:
        logger.info("persist.no_chunks dataset_id=%s", ds.id)
        return PersistResult(
            dataset_id=ds.id, dataset_name=ds.name, old_chunk_count=0, new_chunk_count=0
        )

    # 入库用 chunk.text (默认 format_text 视图, 已在前序选好)
    texts = [c.text for c in chunks]
    try:
        embeddings: list[list[float]] = await embedder.aembed_documents(texts)
    except Exception as e:
        raise RAGError(
            code=IngestErrorCode.PERSIST_EMBED_FAILED,
            message=f"embedding 失败: {e!r}",
        ) from e

    # 3. 构造 domain.document.Chunk (file ingest 落库语义固定为 file)
    filename = result.doc_meta.filename
    domain_chunks: list[DomainChunk] = [
        _build_domain_chunk(
            c,
            dataset_id=ds.id,
            embedding=emb,
            filename=filename,
        )
        for c, emb in zip(chunks, embeddings, strict=True)
    ]

    # 4. 同 (dataset_id, filename) 软删旧 chunk
    chunk_repo = ChunkRepository(session)
    old_count = 0
    if filename:
        old_count = await chunk_repo.soft_delete_by_filename(ds.id, filename)

    # 5. 批量 insert 新 chunk
    await chunk_repo.bulk_insert(domain_chunks)

    await session.commit()
    logger.info(
        "persist.committed dataset_id=%s filename=%s old=%d new=%d",
        ds.id,
        filename,
        old_count,
        len(domain_chunks),
    )
    return PersistResult(
        dataset_id=ds.id,
        dataset_name=ds.name,
        old_chunk_count=old_count,
        new_chunk_count=len(domain_chunks),
    )


def _build_domain_chunk(
    ingest_chunk: IngestChunk,
    *,
    dataset_id: uuid.UUID,
    embedding: list[float],
    filename: str | None,
) -> DomainChunk:
    """ingest.types.Chunk + dataset_id + embedding -> domain.document.Chunk。"""
    meta: IngestChunkMetadata = ingest_chunk.metadata
    # image_path 暂不入库 (本期 image 持久化不做, 见 TODO);
    # modality 固定 "text", image_caption 路径在后续 PR 单独处理。
    return DomainChunk(
        id=ingest_chunk.id,
        dataset_id=dataset_id,
        text=ingest_chunk.text,
        modality="text",
        image_path=None,
        embedding=embedding,
        metadata=DomainChunkMetadata(
            dataset_id=dataset_id,
            datasource="file",
            filename=filename,
            parent_title=meta.heading_stack[0] if meta.heading_stack else "",
            chunk_index=meta.chunk_index,
        ),
    )
