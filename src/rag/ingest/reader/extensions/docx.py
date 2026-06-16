"""docx 格式适配器: mammoth 抽 HTML 后再由 ``html_to_md`` 转 markdown, 含内嵌图上传。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from io import BytesIO

import mammoth
import mammoth.images as mimages

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.infra.pg.runnable_sync import run_coroutine_sync
from rag.ingest.reader.html2md import html_to_md
from rag.ingest.reader.types import (
    FormatReaderResult,
    UploadedFileResult,
    UploadFileHandler,
    mime_to_extension,
)
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# docx 文件 mime (OOXML 官方命名)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_MISSING_UPLOAD_HANDLER_MSG = (
    "Missing imageKeyOptions.prefix for parsed document image upload"
)

_PARSE_ERROR_MSG = "Can not read doc file, please convert to PDF"


def _resolve_mime_extension(mime: str) -> str:
    return mime_to_extension(mime)


async def docx_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: UploadFileHandler | None = None,
) -> FormatReaderResult:
    """将 docx 字节内容解析为 markdown 格式的 ``FormatReaderResult``。

    Args:
        buffer: docx 二进制内容。
        encoding: 文本编码 (docx 主体是 XML, 此参数保留以对齐 adapter 签名)。
        upload_file: 可选异步上传回调 ``(name, mime, bytes) -> {key}``。
            传 ``None`` 且 docx 含内嵌图时抛 ``RAGError(READER_PARSE)``。

    Returns:
        ``FormatReaderResult``:
        - ``raw_text``: markdown 文本。
        - ``format_text=None``。
        - ``images``: 上传成功的 key 列表。
        - ``meta.mime`` 为 docx 标准 mime。

    Raises:
        RAGError: ``code=READER_PARSE`` —— mammoth 解析失败, 或 docx 含内嵌图
            且 ``upload_file`` 为 ``None``。
    """
    images: list[str] = []

    def _build_options() -> dict[str, object]:
        opts: dict[str, object] = {
            "ignore_empty_paragraphs": False,
        }
        # 始终挂图片回调: mammoth 遇到内嵌图时一定走到
        # _convert_image 里的"无 upload_file → 抛错"分支;
        # 不挂则 mammoth 走默认 base64 inline 行为, 无法拦截抛错
        opts["convert_image"] = mimages.img_element(_convert_image)
        return opts

    def _convert_image(image: object) -> dict[str, str]:
        """mammoth ``convert_image`` 回调, 处理内嵌图上传。

        流程:
            1. 无 ``upload_file`` 时抛错 (docx 必须抽图上传, 不允许 base64 残留)。
            2. 有 ``upload_file`` 时抽 bytes → 调异步上传 → 返回 ``{"src": key}``。
            3. 上传失败时记录 warning 并返回 ``{"src": ""}``, 不中断整篇。

        mammoth image 对象 API:
            - ``content_type: str``
            - ``open()`` 上下文管理器, 读取 bytes
        """
        # 防御: 部分 mammoth 版本可能在条件外调用, 此处再次校验
        if upload_file is None:
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=_MISSING_UPLOAD_HANDLER_MSG,
            )
        try:
            content_type: str = getattr(image, "content_type", "image/png")
            open_fn = getattr(image, "open", None)
            if open_fn is None:
                logger.warning("docx image has no .open(): %r", image)
                return {"src": ""}
            with open_fn() as fh:
                image_bytes = fh.read()
            if not image_bytes:
                return {"src": ""}
            name = f"{uuid.uuid4()}.{_resolve_mime_extension(content_type)}"
            result_dict = _run_async_upload(
                upload_file, name, content_type, image_bytes
            )
            key = result_dict.get("key", "") if isinstance(result_dict, dict) else ""
            if not key:
                logger.warning(
                    "docx image upload returned empty key (mime=%s)", content_type
                )
                return {"src": ""}
            images.append(key)
            return {"src": key}
        except RAGError:
            # 抛错透传, 让 mammoth 把整篇失败
            raise
        except Exception as e:  # noqa: BLE001 — 单张图失败不毁整篇
            logger.warning("docx image upload failed: %s", e)
            return {"src": ""}

    def _run_mammoth() -> object:
        """在 worker 线程内同步执行 mammoth, 避免阻塞 event loop。"""
        try:
            return mammoth.convert_to_html(BytesIO(buffer), **_build_options())
        except RAGError:
            # 来自 _convert_image 的抛错透传
            raise
        except Exception as e:
            # 解析失败统一包成 READER_PARSE
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=_PARSE_ERROR_MSG,
            ) from e

    mammoth_result: object = await asyncio.to_thread(_run_mammoth)
    # mammoth 返回 ``mammoth.results.Result`` (有 .value 属性)
    html = mammoth_result.value  # type: ignore[attr-defined]

    # 此时 html 已含上传后的 key, 传 upload_file=None 避免 html2md 二次处理 base64
    markdown = await html_to_md(html, upload_file=None)

    paragraph_count = sum(1 for line in markdown.split("\n") if line.strip())

    return FormatReaderResult(
        raw_text=markdown,
        format_text=None,
        meta=DocMeta(
            mime=DOCX_MIME,
            encoding=encoding,
            size_bytes=len(buffer),
            paragraph_count=paragraph_count,
        ),
        images=list(images),
        extras={},
    )


def _run_async_upload(
    upload_file: UploadFileHandler,
    name: str,
    mime: str,
    image_bytes: bytes,
) -> UploadedFileResult:
    """同步执行一次 async upload_file 调用 (运行在 ``asyncio.to_thread`` worker 线程内)。

    mammoth 的 ``convert_image`` 是同步回调, 而 ``UploadFileHandler`` 是 async;
    worker 线程没有 event loop, 通过 ``run_coroutine_sync`` 安全驱动 async 调用,
    保持与项目其他 sync/async 桥接点一致的接口契约。

    Args:
        upload_file: 异步上传回调。
        name: 生成的文件名。
        mime: 文件 mime。
        image_bytes: 图片字节内容。

    Returns:
        上传结果字典。
    """

    async def _upload() -> UploadedFileResult:
        return await upload_file(name, mime, image_bytes)

    return run_coroutine_sync(_upload)
