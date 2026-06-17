"""PersistStage: 把 ``IngestResult`` 写到 PG (单 document 粒度)。

阶段划分 (短 session + 无 session 交替, 避免 embed 期间占用 PG 连接):
1. ``create_or_get_doc`` — 短 session: upsert document / resume 检测 / 软删旧 chunk
2. ``embed_all_pending`` — 无 session: 分批调用 embedding API (``llm_sem`` 限流)
3. ``insert_chunks`` — 短 session: 单次 bulk_insert + commit (单 doc 体量小, 见 pipeline 100MB 上限)
4. ``finalize_document`` — 短 session: mark completed

断点续传: ``documents.status == "running"`` 时跳过 DB 已有 ``chunk_index``,
仅 embed + insert 缺失块; 非 running 时软删旧 generation 后全量重写。
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from langchain_core.embeddings import Embeddings
from sqlalchemy.ext.asyncio import AsyncSession

from rag.config import settings
from rag.domain.document import Chunk as DomainChunk
from rag.domain.document import ChunkMetadata as DomainChunkMetadata
from rag.domain.document import DocumentDto
from rag.error_codes import IngestErrorCode
from rag.exception import RAGError
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.semaphore import LLMSemaphore, llm_sem
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.infra.pg.repositories.dataset_repo import DatasetRepository
from rag.infra.pg.repositories.document_repo import DocumentRepository
from rag.ingest.types import Chunk as IngestChunk
from rag.ingest.types import ChunkMetadata as IngestChunkMetadata
from rag.ingest.types import IngestResult

logger = logging.getLogger(__name__)

PERSIST_INSERT_FAILED: str = "PERSIST_INSERT_FAILED"
PERSIST_EMBED_FAILED: str = "PERSIST_EMBED_FAILED"

# (enumerate index, ingest chunk, embedding vector)
EmbeddedChunk = tuple[int, IngestChunk, list[float]]


def _hash_chunks(chunks: list[IngestChunk]) -> bytes:
    """对 chunk text 列表做 SHA-256, 用于 document 级 dedup。

    顺序敏感: 同一组 chunks 重排顺序会产生不同 digest。
    """
    h = hashlib.sha256()
    for c in chunks:
        h.update(c.text.encode("utf-8"))
        h.update(b"\x00")
    return h.digest()


async def _create_dataset_once(
    session: AsyncSession,
    dataset_name: str | None,
) -> uuid.UUID:
    """``ingest_many`` 顶部一次性创建 dataset。"""
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


async def _mark_document_failed(document_id: uuid.UUID, error_code: str) -> None:
    """在独立 session 中把 document 标为 failed (主事务回滚时仍能留下失败态)。"""
    try:
        async with AsyncSessionLocal() as fail_session:
            fail_repo = DocumentRepository(fail_session)
            await fail_repo.mark_status(document_id, "failed", error_code=error_code)
            await fail_session.commit()
    except Exception as inner_exc:  # noqa: BLE001
        logger.warning(
            "failed to persist document_id=%s failed status: %s",
            document_id,
            inner_exc,
        )


def _pending_chunks(
    doc: DocumentDto, chunks: list[IngestChunk]
) -> list[tuple[int, IngestChunk]]:
    """根据 resume 状态计算待 embed 的 (index, chunk) 列表。"""
    if doc.is_resume:
        return [
            (i, c) for i, c in enumerate(chunks) if i not in doc.existing_chunk_indexes
        ]
    return list(enumerate(chunks))


async def create_or_get_doc(
    result: IngestResult,
    *,
    dataset_id: uuid.UUID,
) -> DocumentDto | PersistResult:
    """短 session: 校验 dataset、upsert document、处理 resume / 软删, 返回 ``DocumentDto``。

    空 chunks 时在此完成 document upsert + completed 并直接返回 ``PersistResult``。
    """
    filename = result.doc_meta.filename
    chunks = result.chunks

    async with AsyncSessionLocal() as session:
        dataset_repo = DatasetRepository(session)
        ds = await dataset_repo.get_by_id(dataset_id)
        if ds is None:
            raise RAGError(
                code=IngestErrorCode.PERSIST_DATASET_NOT_FOUND,
                message=f"dataset_id {dataset_id} 不存在或已软删除",
            )

        doc_repo = DocumentRepository(session)
        chunk_repo = ChunkRepository(session)

        # 空 chunks: 仍 upsert document, 但不写 chunk
        if not chunks:
            if filename:
                document = await doc_repo.upsert(
                    dataset_id=ds.id,
                    filename=filename,
                    content_hash=_hash_chunks([]),
                    modality="text",
                    total_chunks=0,
                )
                await doc_repo.mark_status(document.id, "completed")
                await session.commit()
            logger.info("persist.no_chunks dataset_id=%s", ds.id)
            return PersistResult(
                dataset_id=ds.id,
                dataset_name=ds.name,
                old_chunk_count=0,
                new_chunk_count=0,
            )

        # resume 须在 upsert 前检测 (upsert 会把 status 置为 running)
        existing_doc = await doc_repo.get_active(ds.id, filename) if filename else None
        is_resume = existing_doc is not None and existing_doc.status == "running"

        if filename:
            document = await doc_repo.upsert(
                dataset_id=ds.id,
                filename=filename,
                content_hash=_hash_chunks(chunks),
                modality="text",
                total_chunks=len(chunks),
            )
            document_id = document.id
        else:
            # 无 filename 时续传不适用
            document_id = uuid.uuid4()
            is_resume = False

        existing_indexes: set[int] = set()
        if is_resume:
            existing_indexes = await chunk_repo.get_existing_indexes(document_id)
            skipped = len(existing_indexes)
            remaining = len(chunks) - skipped
            logger.info(
                "persist.resume document_id=%s skipped=%d remaining=%d",
                document_id,
                skipped,
                remaining,
            )
        else:
            existing_indexes = await chunk_repo.get_existing_indexes(document_id)
            if existing_indexes:
                await chunk_repo.soft_delete_by_document(document_id)

        await session.commit()

        return DocumentDto(
            document_id=document_id,
            dataset_id=ds.id,
            dataset_name=ds.name,
            filename=filename,
            existing_chunk_indexes=existing_indexes,
            is_resume=is_resume,
        )


async def _run_embed_batch(
    embedder: Embeddings,
    texts: list[str],
    *,
    sem: LLMSemaphore,
) -> list[list[float]]:
    """在 embedding 通道信号量保护下调用 ``aembed_documents``。"""
    return await sem.run("embedding", embedder.aembed_documents(texts))


async def embed_all_pending(
    doc: DocumentDto,
    chunks: list[IngestChunk],
    *,
    embedder: Embeddings,
    embed_batch_size: int,
    sem: LLMSemaphore | None = None,
) -> list[EmbeddedChunk]:
    """无 session: 对待处理 chunk 分批 embedding, 返回 (index, chunk, vector) 列表。"""
    pending = _pending_chunks(doc, chunks)
    if not pending:
        return []

    lane_sem = sem if sem is not None else llm_sem
    embedded: list[EmbeddedChunk] = []
    batch_count = (len(pending) + embed_batch_size - 1) // embed_batch_size
    logger.info(
        "persist.embed.start document_id=%s filename=%s pending=%d batches=%d",
        doc.document_id,
        doc.filename,
        len(pending),
        batch_count,
    )

    for batch_start in range(0, len(pending), embed_batch_size):
        batch = pending[batch_start : batch_start + embed_batch_size]
        batch_texts = [c.text for _, c in batch]
        logger.info(
            "persist.embed.batch document_id=%s offset=%d size=%d",
            doc.document_id,
            batch_start,
            len(batch),
        )
        try:
            vectors = await _run_embed_batch(embedder, batch_texts, sem=lane_sem)
        except Exception as e:
            await _mark_document_failed(doc.document_id, PERSIST_EMBED_FAILED)
            raise RAGError(
                code=IngestErrorCode.PERSIST_EMBED_FAILED,
                message=(
                    f"embedding 失败 (batch {batch_start}"
                    f"-{batch_start + len(batch)}): {e!r}"
                ),
            ) from e

        for (idx, chunk), vector in zip(batch, vectors, strict=True):
            embedded.append((idx, chunk, vector))

    logger.info(
        "persist.embed.done document_id=%s filename=%s vectors=%d",
        doc.document_id,
        doc.filename,
        len(embedded),
    )
    return embedded


async def insert_chunks(doc: DocumentDto, embedded: list[EmbeddedChunk]) -> int:
    """短 session: 单次 bulk_insert 全部已 embed 的 chunk 并 commit。"""
    if not embedded:
        return 0

    domain_batch: list[DomainChunk] = [
        _build_domain_chunk(
            chunk,
            dataset_id=doc.dataset_id,
            document_id=doc.document_id,
            embedding=vector,
            filename=doc.filename,
        )
        for _, chunk, vector in embedded
    ]

    try:
        async with AsyncSessionLocal() as session:
            chunk_repo = ChunkRepository(session)
            await chunk_repo.bulk_insert(domain_batch)
            await session.commit()
    except Exception:
        await _mark_document_failed(doc.document_id, PERSIST_INSERT_FAILED)
        raise

    logger.info(
        "persist.chunks_inserted document_id=%s count=%d",
        doc.document_id,
        len(domain_batch),
    )
    return len(domain_batch)


async def finalize_document(doc: DocumentDto) -> None:
    """短 session: 全部 chunk 落库成功后标记 document completed。"""
    if doc.filename is None:
        return
    async with AsyncSessionLocal() as session:
        doc_repo = DocumentRepository(session)
        await doc_repo.mark_status(doc.document_id, "completed")
        await session.commit()
    logger.info(
        "persist.finalize document_id=%s filename=%s status=completed",
        doc.document_id,
        doc.filename,
    )


async def persist(
    result: IngestResult,
    *,
    dataset_id: uuid.UUID,
    embedder: Embeddings | None = None,
    embed_batch_size: int = 256,
    sem: LLMSemaphore | None = None,
) -> PersistResult:
    """把 ``IngestResult.chunks`` 写入 PG (单 document)。

    内部自行管理短 session; 调用方勿再包 ``AsyncSessionLocal``。
    """
    if embed_batch_size <= 0:
        raise RAGError(
            code=IngestErrorCode.PERSIST_INVALID_ARGS,
            message=f"embed_batch_size 必须 > 0, 实际 {embed_batch_size}",
        )

    prep = await create_or_get_doc(result, dataset_id=dataset_id)
    if isinstance(prep, PersistResult):
        return prep

    doc = prep
    filename = result.doc_meta.filename or ""
    logger.info(
        "persist.start filename=%s document_id=%s total_chunks=%d resume=%s",
        filename,
        doc.document_id,
        len(result.chunks),
        doc.is_resume,
    )
    pending = _pending_chunks(doc, result.chunks)
    if not pending:
        await finalize_document(doc)
        logger.info(
            "persist.nothing_to_do document_id=%s resume=%s",
            doc.document_id,
            doc.is_resume,
        )
        return PersistResult(
            dataset_id=doc.dataset_id,
            dataset_name=doc.dataset_name,
            old_chunk_count=len(doc.existing_chunk_indexes),
            new_chunk_count=0,
        )

    if embedder is None:
        embedder = get_embed_model()

    embedded = await embed_all_pending(
        doc,
        result.chunks,
        embedder=embedder,
        embed_batch_size=embed_batch_size,
        sem=sem,
    )
    new_chunk_count = await insert_chunks(doc, embedded)
    await finalize_document(doc)

    logger.info(
        "persist.committed dataset_id=%s filename=%s old=%d new=%d resume=%s",
        doc.dataset_id,
        doc.filename,
        len(doc.existing_chunk_indexes),
        new_chunk_count,
        doc.is_resume,
    )
    return PersistResult(
        dataset_id=doc.dataset_id,
        dataset_name=doc.dataset_name,
        old_chunk_count=len(doc.existing_chunk_indexes),
        new_chunk_count=new_chunk_count,
    )


def _build_domain_chunk(
    ingest_chunk: IngestChunk,
    *,
    dataset_id: uuid.UUID,
    document_id: uuid.UUID,
    embedding: list[float],
    filename: str | None,
) -> DomainChunk:
    """ingest.types.Chunk + dataset_id + embedding -> domain.document.Chunk。"""
    meta: IngestChunkMetadata = ingest_chunk.metadata
    return DomainChunk(
        id=ingest_chunk.id,
        dataset_id=dataset_id,
        document_id=document_id,
        text=ingest_chunk.text,
        modality="text",
        image_path=None,
        embedding=embedding,
        metadata=DomainChunkMetadata(
            datasource="file",
            filename=filename,
            parent_title=meta.heading_stack[0] if meta.heading_stack else "",
            chunk_index=meta.chunk_index,
        ),
    )
