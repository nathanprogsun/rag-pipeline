"""IngestPipeline: ``ingest_many(targets) -> IngestOutcome`` 入口。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from rag.infra.pg.database import AsyncSessionLocal
from rag.ingest.chunker import Chunker
from rag.ingest.chunker._heading_re import extract_first_title
from rag.ingest.normalizer import NoOpNormalizer, Normalizer
from rag.ingest.persist import _create_dataset_once
from rag.ingest.persist import persist as persist_chunks
from rag.ingest.reader import dispatch_bytes
from rag.ingest.reader.file import read_to_buffer
from rag.ingest.types import (
    Chunk,
    DocMeta,
    IngestOutcome,
    IngestResult,
    PersistConfig,
    PersistOutcome,
    TextDoc,
)

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES: int = 100 * 1024 * 1024
_SKIP_DIR_NAMES: frozenset[str] = frozenset({"__pycache__"})


def _file_eligible(path: Path, warnings: list[str]) -> bool:
    if path.name.startswith("."):
        return False
    if any(part in _SKIP_DIR_NAMES for part in path.parts):
        return False
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            warnings.append(f"skip oversized file (>{_MAX_FILE_BYTES} bytes): {path}")
            return False
    except OSError as exc:
        warnings.append(f"stat failed for {path}: {exc}")
        return False
    return True


def expand_paths(paths: list[Path]) -> tuple[list[Path], list[str]]:
    """展开路径列表: 文件保留, 目录递归遍历 (遇目录即递归)。"""
    expanded: list[Path] = []
    warnings: list[str] = []
    seen: set[Path] = set()

    def visit(raw: Path) -> None:
        if not raw.exists():
            warnings.append(f"path not found: {raw}")
            return
        if raw.is_file():
            if _file_eligible(raw, warnings) and raw not in seen:
                seen.add(raw)
                expanded.append(raw)
            return
        if raw.is_dir():
            for child in sorted(raw.iterdir()):
                visit(child)
            return
        warnings.append(f"skip non-file/non-dir path: {raw}")

    for raw in paths:
        visit(raw)
    expanded.sort()
    return expanded, warnings


def _expand_files(targets: list[str]) -> tuple[list[Path], list[str]]:
    return expand_paths([Path(t) for t in targets])


class IngestPipeline:
    def __init__(
        self,
        chunker: Chunker,
        normalizer: Normalizer | None = None,
        persist_config: PersistConfig | None = None,
        *,
        max_concurrent: int = 8,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")
        self.chunker = chunker
        self.normalizer: Normalizer = normalizer or NoOpNormalizer()
        self.persist_config = persist_config
        self._max_concurrent = max_concurrent
        self._sem = asyncio.Semaphore(max_concurrent)

    @property
    def persist_enabled(self) -> bool:
        return self.persist_config is not None and self.persist_config.enabled

    async def ingest_many(
        self,
        targets: list[str],
    ) -> IngestOutcome:
        """批量 ingest; 目录递归展开, 大小超过限制的文件跳过。"""
        logger.info("ingest.batch.start targets=%s", targets)

        # 1. 展开路径
        files, warnings = _expand_files(targets)
        if not files:
            logger.warning(
                "ingest.batch.empty targets=%s warnings=%d", targets, len(warnings)
            )
            return IngestOutcome(items=[], warnings=warnings, errors=[])

        logger.info("ingest.batch.expanded file_count=%d", len(files))

        # 1.5 解析 dataset (并发前一次性创建, 消除 cfg.adopt race)
        cfg = self.persist_config
        resolved_dataset_id: uuid.UUID | None = None
        if self.persist_enabled and cfg is not None:
            if cfg.create_dataset:
                logger.info("ingest.batch.dataset_create name=%s", cfg.dataset_name)
                async with AsyncSessionLocal() as session:
                    resolved_dataset_id = await _create_dataset_once(
                        session, cfg.dataset_name
                    )
            elif cfg.dataset_id is not None:
                resolved_dataset_id = cfg.dataset_id
                logger.info("ingest.batch.dataset_use id=%s", resolved_dataset_id)

        # 2. 并行 ingest; gather 会创建 N 个协程, 实际并发由 _sem 限制为 max_concurrent
        logger.info(
            "ingest.batch.processing file_count=%d max_concurrent=%d persist=%s",
            len(files),
            self._max_concurrent,
            resolved_dataset_id is not None,
        )
        results = await asyncio.gather(
            *[
                self._process_gated(file, dataset_id=resolved_dataset_id)
                for file in files
            ],
            return_exceptions=True,
        )
        items: list[IngestResult] = []
        errors: list[tuple[str, BaseException]] = []
        for file, outcome in zip(files, results, strict=True):
            if isinstance(outcome, BaseException) and not isinstance(
                outcome, Exception
            ):
                # CancelledError 等 BaseException-but-not-Exception: 透传, 不入 errors
                raise outcome
            if isinstance(outcome, Exception):
                errors.append((str(file), outcome))
                warnings.append(f"ingest failed for {file}: {outcome}")
                logger.warning("ingest failed path=%s err=%s", file, outcome)
            else:
                items.append(outcome)

        logger.info(
            "ingest.batch.done ok=%d errors=%d warnings=%d",
            len(items),
            len(errors),
            len(warnings),
        )
        return IngestOutcome(items=items, warnings=warnings, errors=errors)

    async def _maybe_persist(
        self, result: IngestResult, *, dataset_id: uuid.UUID
    ) -> IngestResult:
        filename = result.doc_meta.filename or ""
        logger.info(
            "ingest.persist.start filename=%s chunks=%d dataset_id=%s",
            filename,
            len(result.chunks),
            dataset_id,
        )
        pr = await persist_chunks(
            result,
            dataset_id=dataset_id,
        )
        logger.info(
            "ingest.persist.done filename=%s old=%d new=%d",
            filename,
            pr.old_chunk_count,
            pr.new_chunk_count,
        )
        outcome = PersistOutcome(
            dataset_id=pr.dataset_id,
            dataset_name=pr.dataset_name,
            old_chunk_count=pr.old_chunk_count,
            new_chunk_count=pr.new_chunk_count,
        )
        return result.model_copy(update={"persist": outcome})

    async def _read_file(self, path: Path) -> TextDoc:
        """Path -> TextDoc: 复用 ``read_to_buffer`` 后 async dispatch。"""
        buffer = read_to_buffer(path)
        return await dispatch_bytes(
            buffer=buffer,
            extension=path.suffix,
            filename=path.name,
        )

    async def _process(
        self,
        file: Path,
        *,
        dataset_id: uuid.UUID | None = None,
    ) -> IngestResult:
        logger.info("ingest.file.start path=%s", file)

        # 1. 读取文件
        text_doc = await self._read_file(file)
        logger.info(
            "ingest.file.read_done path=%s text_len=%d ext=%s",
            file.name,
            len(text_doc.text),
            file.suffix,
        )

        # 2. 标准化
        text_doc = await self.normalizer.normalize(text_doc)
        logger.info(
            "ingest.file.normalize_done path=%s text_len=%d",
            file.name,
            len(text_doc.text),
        )
        text = text_doc.text

        # 3. 分块
        chunks: list[Chunk] = self.chunker.split(
            text,
            format_text=text_doc.format_text,
            get_format_text=True,
        )
        logger.info("ingest.file.chunk_done path=%s chunks=%d", file.name, len(chunks))

        doc_meta: DocMeta = text_doc.meta
        title = extract_first_title(text_doc.text) or text_doc.meta.filename
        result = IngestResult(
            chunks=chunks,
            title=title,
            doc_meta=doc_meta,
        )

        # 4. 存储
        if dataset_id is not None:
            updated_result = await self._maybe_persist(result, dataset_id=dataset_id)
            logger.info(
                "ingest.file.done path=%s chunks=%d persisted=True",
                file.name,
                len(chunks),
            )
            return updated_result
        logger.info(
            "ingest.file.done path=%s chunks=%d persisted=False", file.name, len(chunks)
        )
        return result

    async def _process_gated(
        self, file: Path, *, dataset_id: uuid.UUID | None
    ) -> IngestResult:
        async with self._sem:
            return await self._process(file, dataset_id=dataset_id)
