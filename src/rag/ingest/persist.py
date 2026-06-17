"""PersistStage: 把 `IngestResult` 写到 PG。

串联: 解析 dataset (id 或新建) -> 批量 embedding (按窗口拆分) ->
按 document_id 软删旧 chunk -> 分批 commit 新 chunk。

断点续传语义: 同一 (dataset_id, filename) 再次进入 persist 时,
- 若 `documents.status == "running"` (上次中断), 跳过已存在的 chunk_index,
  仅补缺失的 chunk; 标记 status="completed" 在所有 batch 成功后。
- 若 `documents.status == "pending"` / `"completed"` (全新 / 主动重 ingest),
  软删旧 generation 的 chunk, 全量重写。

每 batch 一提交: 避免大 ingest 中途崩溃时丢失已完成的 embedding 工作。
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
from rag.error_codes import IngestErrorCode
from rag.exception import RAGError
from rag.infra.llm.embed import get_embed_model
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


def _hash_chunks(chunks: list[IngestChunk]) -> bytes:
    """对 chunk text 列表做 SHA-256, 用于 document 级 dedup。

    顺序敏感: 同一组 chunks 重排顺序会产生不同 digest。
    这是有意的 — chunk 顺序代表实际生成顺序, 重排视为内容变更。
    (如果需要顺序无关去重, 在调用方 sort 之后再 hash。)

    Returns:
        32-byte SHA-256 digest.
    """
    h = hashlib.sha256()
    for c in chunks:
        h.update(c.text.encode("utf-8"))
        h.update(b"\x00")  # 用 NUL 分隔避免相邻 chunk 边界混淆
    return h.digest()


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


async def _mark_document_failed(document_id: uuid.UUID, error_code: str) -> None:
    """在独立 session 中把 document 标为 failed。

    落库失败时主事务会回滚, 因此用独立 session 持久化 failed 状态。
    自身抛错时仅记录, 不向上冒泡 — 调用方已经把真实异常抛出。
    """
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


async def persist(
    session: AsyncSession,
    result: IngestResult,
    *,
    dataset_id: uuid.UUID,
    embedder: Embeddings | None = None,
    embed_batch_size: int = 256,
) -> PersistResult:
    """把 `IngestResult.chunks` 写入 PG。``dataset_id`` 必传。

    Args:
        session: 外部注入的 SQLAlchemy 异步会话。
        result: `IngestPipeline._process()` 输出。
        dataset_id: 落库目标 dataset 的 UUID (由 pipeline 顶部解析)。
        embedder: 可选 LangChain `Embeddings`; 缺省时按 `settings.openai_embedding_*` 自动构造。
        embed_batch_size: 每次 `aembed_documents` 调用的 chunk 数。
            每个 batch 落库后立即 commit, 避免大批量 ingest 中途崩溃丢失已完成工作。

    Returns:
        `PersistResult`, 含 dataset_id 与新旧 chunk 数。

    Raises:
        RAGError: dataset_id 不存在; embedding 失败; 插入失败。
    """
    if embed_batch_size <= 0:
        raise RAGError(
            code=IngestErrorCode.PERSIST_INVALID_ARGS,
            message=f"embed_batch_size 必须 > 0, 实际 {embed_batch_size}",
        )

    dataset_repo = DatasetRepository(session)
    ds = await dataset_repo.get_by_id(dataset_id)
    if ds is None:
        raise RAGError(
            code=IngestErrorCode.PERSIST_DATASET_NOT_FOUND,
            message=f"dataset_id {dataset_id} 不存在或已软删除",
        )

    filename = result.doc_meta.filename
    chunks = result.chunks
    chunk_repo = ChunkRepository(session)
    doc_repo = DocumentRepository(session)

    # 1. 空 chunks 短路: 仍然 upsert document (status=running -> completed), 但不写 chunk。
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
            dataset_id=ds.id, dataset_name=ds.name, old_chunk_count=0, new_chunk_count=0
        )

    # 2. resume 检测: 在 upsert 之前先看现有 document 的 status。
    #    upsert 会无条件 status="running", 必须在它之前判断。
    existing_doc: object | None = None
    if filename:
        existing_doc = await doc_repo.get_active(ds.id, filename)
    is_resume = (
        existing_doc is not None and getattr(existing_doc, "status", None) == "running"
    )

    # 3. upsert document (status -> "running", generation += 1)
    document_id: uuid.UUID
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
        # 无 filename 的数据源 (e.g. URL fetch) 退化为 UUID 占位 document,
        # 续传语义不适用。
        document_id = uuid.uuid4()
        is_resume = False

    # 4. 准备 remaining_chunks: 续传跳过已存在, 否则按需软删旧 generation 的 chunks。
    existing_indexes: set[int] = set()
    if is_resume:
        existing_indexes = await chunk_repo.get_existing_indexes(document_id)
        remaining_chunks = [
            (i, c) for i, c in enumerate(chunks) if i not in existing_indexes
        ]
        logger.info(
            "persist.resume document_id=%s skipped=%d remaining=%d",
            document_id,
            len(existing_indexes),
            len(remaining_chunks),
        )
    else:
        existing_indexes = await chunk_repo.get_existing_indexes(document_id)
        if existing_indexes:
            await chunk_repo.soft_delete_by_document(document_id)
            await session.commit()
        remaining_chunks = list(enumerate(chunks))

    # 5. embedding + insert 按 batch 循环, 每 batch 一提交。
    if embedder is None:
        embedder = get_embed_model()

    new_chunk_count = 0
    for batch_start in range(0, len(remaining_chunks), embed_batch_size):
        batch = remaining_chunks[batch_start : batch_start + embed_batch_size]
        batch_texts = [c.text for _, c in batch]

        # 5a. 尝试 embedding; 失败时把 document 标 failed (独立 session)。
        try:
            embeddings: list[list[float]] = await embedder.aembed_documents(batch_texts)
        except Exception as e:
            await _mark_document_failed(document_id, PERSIST_EMBED_FAILED)
            raise RAGError(
                code=IngestErrorCode.PERSIST_EMBED_FAILED,
                message=(
                    f"embedding 失败 (batch {batch_start}"
                    f"-{batch_start + len(batch)}): {e!r}"
                ),
            ) from e

        # 5b. 构造 domain chunk 并落库; 失败同理标 failed。
        domain_batch: list[DomainChunk] = [
            _build_domain_chunk(
                c,
                dataset_id=ds.id,
                document_id=document_id,
                embedding=emb,
                filename=filename,
            )
            for (_, c), emb in zip(batch, embeddings, strict=True)
        ]
        try:
            await chunk_repo.bulk_insert(domain_batch)
        except Exception:
            await _mark_document_failed(document_id, PERSIST_INSERT_FAILED)
            raise

        # 5c. 每 batch 独立 commit, 中途崩溃时已完成 batch 已落库。
        await session.commit()
        new_chunk_count += len(batch)
        logger.info(
            "persist.batch_committed document_id=%s batch=%d-%d",
            document_id,
            batch_start,
            batch_start + len(batch),
        )

    # 6. 全部 batch 完成后, document 标 completed (同一事务持久化)。
    if filename:
        await doc_repo.mark_status(document_id, "completed")
        await session.commit()

    logger.info(
        "persist.committed dataset_id=%s filename=%s old=%d new=%d resume=%s",
        ds.id,
        filename,
        len(existing_indexes),
        new_chunk_count,
        is_resume,
    )
    return PersistResult(
        dataset_id=ds.id,
        dataset_name=ds.name,
        old_chunk_count=len(existing_indexes),
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
    # image_path 暂不入库 (本期 image 持久化不做, 见 TODO);
    # modality 固定 "text", image_caption 路径在后续 PR 单独处理。
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
