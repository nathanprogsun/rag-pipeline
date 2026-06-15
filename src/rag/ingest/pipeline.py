"""IngestPipeline: 单一 ``ingest(IngestSource) -> IngestResult`` 入口。"""

from __future__ import annotations

import logging
import re

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.chunker import Chunker
from rag.ingest.chunker.types import ChunkContext
from rag.ingest.normalizer import NoOpNormalizer, Normalizer
from rag.ingest.reader import dispatch_bytes, read_url
from rag.ingest.source import (
    BufferSource,
    FileSource,
    IngestSource,
    UrlSource,
)
from rag.ingest.types import Chunk, DocMeta, IngestResult, TextDoc

logger = logging.getLogger(__name__)

# doc-level title 抽取: 优先 Markdown `# title` 或 HTML `<h1>title</h1>` 第一项。
_TITLE_MD_RE = re.compile(r"^#{1,5}\s+(.+)$", re.MULTILINE)
_TITLE_HTML_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def _extract_title(text: str) -> str | None:
    """从纯文本中抽第一行 `#` 标题或 `<h1>`, 失败返回 None。"""
    m = _TITLE_MD_RE.search(text)
    if m:
        title = m.group(1).strip()
        if title:
            return title
    m = _TITLE_HTML_RE.search(text)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title:
            return title
    return None


def _derive_title(text_doc: TextDoc, warnings: list[str]) -> str | None:
    """doc-level title 推导: 文本内 `#` / `<h1>` 第一项, 兜底 filename。"""
    title = _extract_title(text_doc.text)
    if title:
        return title
    if text_doc.meta.filename:
        return text_doc.meta.filename
    warnings.append("title unavailable: no heading and no filename")
    return None


def _build_context(text_doc: TextDoc) -> ChunkContext:
    """组装 Chunker 入参上下文。"""
    return ChunkContext.from_meta(meta=text_doc.meta)


class IngestPipeline:
    def __init__(
        self,
        chunker: Chunker,
        normalizer: Normalizer | None = None,
        *,
        max_url_size: int = 1_000_000_000,
        url_timeout_s: float = 600.0,
    ) -> None:
        self.chunker = chunker
        self.normalizer: Normalizer = normalizer or NoOpNormalizer()
        self._max_url_size = max_url_size
        self._url_timeout_s = url_timeout_s

    async def ingest(
        self,
        source: IngestSource,
        *,
        get_format_text: bool = True,
    ) -> IngestResult:
        """IngestSource -> IngestResult: 全程 async。

        Args:
            source: ``FileSource`` / ``UrlSource`` / ``BufferSource`` 三选一。
            get_format_text: 透传给 chunker, 决定 ``Chunk.text`` 是 ``format_text``
                (csv/xlsx 的 md table 视图) 还是 ``raw_text``。默认 True。

        Returns:
            ``IngestResult(chunks=..., title=..., doc_meta=..., warnings=...)``

        Raises:
            TypeError: ``source`` 不是三种合法子类之一。
        """
        if isinstance(source, FileSource):
            text_doc = await self._read_file(source)
        elif isinstance(source, UrlSource):
            text_doc = await read_url(
                source.url,
                max_size=source.max_size,
                timeout_s=source.timeout_s,
            )
        elif isinstance(source, BufferSource):
            filename = source.source.rsplit("/", 1)[-1]
            if "." not in filename:
                filename = f"{filename}.{source.file_type.lstrip('.')}"
            # BufferSource 视为"已在内存中的文件流", datasource 归 file;
            # 落库时再依据 source 前缀改判为 manual。
            text_doc = await dispatch_bytes(
                buffer=source.buf,
                extension=source.file_type,
                source=source.source,
                datasource="file",
                filename=filename,
            )
        else:
            raise TypeError(f"unsupported IngestSource: {type(source).__name__}")

        return await self._process(text_doc, get_format_text=get_format_text)

    async def _read_file(self, source: FileSource) -> TextDoc:
        """FileSource -> TextDoc: 直接走 ``await dispatch_bytes``。

        不用 ``read_file`` 是因为后者用 ``asyncio.run`` 包, 在已处于 event loop
        时会抛 ``RuntimeError``。
        """
        p = source.path
        if not p.exists():
            raise RAGError(
                code=ReaderErrorCode.NOT_FOUND,
                message=f"{p}: file does not exist",
            )
        if not p.is_file():
            raise RAGError(
                code=ReaderErrorCode.NOT_FOUND,
                message=f"{p}: not a regular file",
            )
        try:
            buffer = p.read_bytes()
        except PermissionError as e:
            raise RAGError(
                code=ReaderErrorCode.PERMISSION,
                message=f"{p}: {e}",
            ) from e
        except OSError as e:
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=f"{p}: {e}",
            ) from e
        return await dispatch_bytes(
            buffer=buffer,
            extension=p.suffix,
            source=f"file://{p.resolve()}",
            datasource="file",
            filename=p.name,
        )

    async def _process(
        self, text_doc: TextDoc, *, get_format_text: bool = True
    ) -> IngestResult:
        warnings: list[str] = []

        # 先在 normalize 之前抽 title, 防 normalizer 改写 / 删除原 H1。
        pre_normalize_title = _derive_title(text_doc, warnings)

        text_doc = await self.normalizer.normalize(text_doc)
        text = text_doc.text

        ctx = _build_context(text_doc)
        chunks: list[Chunk] = self.chunker.split(
            text,
            ctx=ctx,
            format_text=text_doc.format_text,
            get_format_text=get_format_text,
        )

        # normalize 后再抽一次, 兜底 normalizer 自己注入新 H1 的场景;
        # 优先级: 原始 H1 > 改写后 H1 > filename (来自 _derive_title 内部兜底)。
        post_normalize_title = _derive_title(text_doc, warnings)
        title = pre_normalize_title or post_normalize_title
        doc_meta: DocMeta = text_doc.meta

        return IngestResult(
            chunks=chunks,
            title=title,
            doc_meta=doc_meta,
            warnings=warnings,
        )
