"""IngestPipeline: 单一 ``ingest(IngestSource) -> IngestResult`` 入口。

设计要点:
- 删除 ``_ensure_structure`` 兜底 + ``_TEXT_STRUCTURE_EXTRACTORS`` 表
  (旧 MarkdownStructureExtractor / HtmlStructureExtractor 已删除)。
- 文档级结构不再由 reader / pipeline 抽取, 改由 chunker 内部 per-chunk
  regex (_MD_HEADING_RE / _HTML_HEADING_RE / _TABLE_RE / _CODE_FENCE_RE)
  现场重算 heading_stack / has_code / has_table / image_refs。
- doc-level identifier 推导:
    * title: 优先 text 内第一行非空 `#` / `<h1>` 标题, 兜底 ``meta.filename``
    * page_count / paragraph_count: 从 ``meta`` 透传
    * warnings: 收集非致命降级信号
- FileSource 路径走 ``await dispatch_bytes`` 直接, 不再调 ``read_file`` 包装
  (避免 ``asyncio.run`` 嵌套导致 ``RuntimeError``)。
- ``ingest`` 新增 ``get_format_text: bool = True``: 透传给 chunker, 决定
  ``Chunk.text`` 是 ``format_text`` (csv/xlsx 的 md table) 还是 ``raw_text``。
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.chunker import Chunker
from rag.ingest.chunker.types import ChunkContext
from rag.ingest.normalizer import NoOpNormalizer, Normalizer
from rag.ingest.reader import dispatch_bytes, read_url
from rag.ingest.source import (
    ApiSource,
    BufferSource,
    FileSource,
    IngestSource,
    UrlSource,
)
from rag.ingest.types import Chunk, DocMeta, IngestResult, TextDoc

logger = logging.getLogger(__name__)

# doc-level title 抽取: 优先 Markdown `# title` 或 HTML `<h1>title</h1>` 第一项。
# 静态 structure 已删除, 这里从原始文本里走一次轻量 regex 推导即可, 不需要 Heading 树。
_TITLE_MD_RE = re.compile(r"^#{1,5}\s+(.+)$", re.MULTILINE)
_TITLE_HTML_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def _extract_title(text: str) -> str | None:
    """从纯文本中抽第一行 # 标题或 <h1>, 失败返回 None。"""
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
    """doc-level title 推导: 文本内 # / <h1> 第一项, 兜底 filename。"""
    title = _extract_title(text_doc.text)
    if title:
        return title
    if text_doc.meta.filename:
        return text_doc.meta.filename
    warnings.append("title unavailable: no heading and no filename")
    return None


def _build_context(text_doc: TextDoc) -> ChunkContext:
    """组装 Chunker 入参上下文: 仅 DocMeta (structure / heading_path 已删)。"""
    return ChunkContext.from_meta(meta=text_doc.meta)


def _extract_api_field(data: object, field_priority: tuple[str, ...]) -> str:
    """按 field_priority 顺序抽字段: dict 取首条命中, list 拼接, 标量 str() 化。

    dict 路径只取首个非空字符串字段 (不拼接多字段), list 路径按 ``text`` /
    ``content`` 优先级逐项抽取并用空行 join, 命中失败的项用 ``json.dumps`` 兜底。
    """
    if isinstance(data, dict):
        for key in field_priority:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(data, ensure_ascii=False)
    if isinstance(data, list):
        # list 路径固定走 text / content 优先级 (与旧实现保持一致)
        list_priority = ("text", "content")
        parts: list[str] = []
        for item in data:
            if isinstance(item, dict):
                picked = False
                for key in list_priority:
                    v = item.get(key)
                    if isinstance(v, str) and v:
                        parts.append(v)
                        picked = True
                        break
                if not picked:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n\n".join(parts)
    return str(data)


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

    # ── 单一入口 ─────────────────────────────────────────────
    async def ingest(
        self,
        source: IngestSource,
        *,
        get_format_text: bool = True,
    ) -> IngestResult:
        """IngestSource -> IngestResult: 全程 async。

        Args:
            source: ``FileSource`` / ``UrlSource`` / ``BufferSource`` / ``ApiSource`` 四选一。
            get_format_text: 透传给 chunker, 决定 ``Chunk.text`` 是 ``format_text``
                (csv/xlsx 的 md table 视图) 还是 ``raw_text``。默认 True。

        Returns:
            ``IngestResult(chunks=..., title=..., doc_meta=..., warnings=...)``

        Note:
            CLI 入口负责 ``asyncio.run(pipeline.ingest(...))`` 并统一加 tqdm / logger。
            FileSource 不再调 ``read_file`` (那里 ``asyncio.run`` 会与外层 loop 冲突),
            直接走 ``await dispatch_bytes``。
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
            text_doc = await dispatch_bytes(
                buffer=source.buf,
                extension=source.file_type,
                source=source.source,
                datasource="api",
                filename=filename,
            )
        elif isinstance(source, ApiSource):
            text_doc = await self._fetch_api(source)
        else:
            raise TypeError(f"unsupported IngestSource: {type(source).__name__}")

        return await self._process(text_doc, get_format_text=get_format_text)

    async def _read_file(self, source: FileSource) -> TextDoc:
        """FileSource -> TextDoc: 直接走 ``await dispatch_bytes``。

        不用 ``read_file`` 因为后者用 ``asyncio.run`` 包, 在 ``pipeline.ingest``
        已处于 event loop 时会 ``RuntimeError``。
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

    # ── ApiSource 内部拉取 + 字段抽取 ─────────────────────────
    async def _fetch_api(self, source: ApiSource) -> TextDoc:
        """ApiSource -> TextDoc: 拉 JSON, 走 field_priority 抽字段。

        错误码 (细分便于排查):
          - READER_API_AUTH:   401/403
          - READER_API_TIMEOUT: httpx.TimeoutException
          - READER_API_STATUS:  其他非 2xx
          - READER_PARSE:      JSON 解析失败 / 字段抽取全部为空
        """
        url = source.server_url.rstrip("/") + "/" + source.endpoint.lstrip("/")
        headers = {"Accept": "application/json"}
        if source.auth_token:
            headers["Authorization"] = f"Bearer {source.auth_token}"
        timeout = httpx.Timeout(
            connect=10.0, read=source.timeout_s, write=10.0, pool=10.0
        )

        # 测试可注入 client; 真实场景由本方法内部 AsyncClient 承担。
        # source.http_client 声明为 ``object`` 以避免 source.py 引入 httpx 依赖;
        # 此处强制收窄到 httpx.AsyncClient, 注入契约由本方法保证。
        owns_client = source.http_client is None
        client: httpx.AsyncClient
        if owns_client:
            client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
        else:
            client = source.http_client  # type: ignore[assignment]

        try:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.TimeoutException as e:
                logger.warning("reader.api.timeout url=%s err=%s", url, e)
                raise RAGError(
                    code=ReaderErrorCode.API_TIMEOUT,
                    message=f"{url}: api timeout: {e}",
                ) from e
            except httpx.HTTPError as e:
                logger.warning("reader.api.fail url=%s err=%s", url, e)
                raise RAGError(
                    code=ReaderErrorCode.API_STATUS,
                    message=f"{url}: api request failed: {e}",
                ) from e

            status = resp.status_code
            if status in (401, 403):
                raise RAGError(
                    code=ReaderErrorCode.API_AUTH,
                    message=f"{url}: api auth failed: HTTP {status}",
                )
            if status >= 400:
                raise RAGError(
                    code=ReaderErrorCode.API_STATUS,
                    message=f"{url}: api status: HTTP {status}",
                )

            body = resp.content
            if len(body) > source.max_size:
                raise RAGError(
                    code=ReaderErrorCode.TOO_LARGE,
                    message=f"{url}: api response too large: {len(body)} > {source.max_size}",
                )

            try:
                data = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise RAGError(
                    code=ReaderErrorCode.PARSE,
                    message=f"{url}: api JSON parse failed: {e}",
                ) from e
        finally:
            if owns_client:
                await client.aclose()

        extracted = _extract_api_field(data, source.field_priority)
        if not extracted:
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=f"{url}: api JSON had no non-empty field in {source.field_priority}",
            )

        full_source = url
        return TextDoc(
            text=extracted,
            meta=DocMeta(
                datasource="api",
                source=full_source,
                mime="application/json",
                size_bytes=len(body),
            ),
        )

    # ── 内部统一: 两段串联 ─────────────────────────────────────
    async def _process(
        self, text_doc: TextDoc, *, get_format_text: bool = True
    ) -> IngestResult:
        # normalize 已 async, 直接 ``await`` 透传; chunker.split
        # 仍为 sync (无 I/O), 不阻塞 event loop。整条 ingest 主干全栈 async。
        warnings: list[str] = []

        # 先在 normalize 之前抽 title, 防 normalizer 改写 / 删除原 H1。
        pre_normalize_title = _derive_title(text_doc, warnings)

        # Step 1: Normalizer (可选, 内部失败降级)
        text_doc = await self.normalizer.normalize(text_doc)
        text = text_doc.text

        # Step 2: Chunker (注入 ctx 含 DocMeta)
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
