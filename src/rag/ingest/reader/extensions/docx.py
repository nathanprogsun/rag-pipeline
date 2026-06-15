"""docx extension adapter: mammoth html → html2md markdown (含内嵌图上传)。

实现 5 项契约:

    5.1 用 ``mammoth`` 库: ``mammoth.convert_to_html(file_obj, **options)``
    5.2 选项: ``ignore_empty_paragraphs=False`` (Section 5.2)
    5.3 图片回调 (``mammoth.images.img_element(callback)``):
        - ``name = uuid.uuid4() + ext_from_mime(mime)``
        - ``if not upload_file: throw Error("Missing imageKeyOptions.prefix ...")``
          (Section 5.3: 无 handler 抛错; 区别于 pptx 的"忽略"策略)
        - 调 ``upload_file(name=name, mime=mime, buffer=image_bytes)`` 拿 ``{key}``,
          返回 ``{"src": key}`` 替换图片 src
    5.4 MD 化: ``html_to_md(html, upload_file=None)``
        (Section 5.4: html 已经含上传后的 S3 key URL, 不再二次 base64 处理;
         传 ``upload_file=None`` 给 html2md 让它走"src 置空"分支即可,
         避免重复处理 base64 — Section 5.4 注释)
    5.5 失败: ``catch`` → ``RAGError(code=READER_PARSE,
        message="Can not read doc file, please convert to PDF")``
    返回: ``FormatReaderResult { raw_text=markdown, format_text=None,
        images=[uploaded_keys], extras={} }`` (只 rawText, 无 formatText)

注意:
    - docx 的图片上传策略与 pptx 不同: pptx 不抽图, docx **必须** 抽并上传 (Section 5.3 强约束)
    - mammoth 是同步库; ``UploadFileHandler`` 是 async, 回调内用 ``run_coroutine_sync``
      桥接 (worker 线程无 event loop, 统一用项目内 sync/async helper 走 ``asyncio.run``)
    - 上传失败的图片源会被 ``html_to_md`` 当成无效 (src="" / data URL 仍可能残留),
      失败时用 warning log 而非抛错
"""

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
)
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# docx 文件 mime (OOXML 官方命名, 与 adapters/docx.py 一致)
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_MISSING_UPLOAD_HANDLER_MSG = (
    "Missing imageKeyOptions.prefix for parsed document image upload"
)

_PARSE_ERROR_MSG = "Can not read doc file, please convert to PDF"

# mime → 文件后缀 (Section 5.3: ext_from_mime)
# 与 html2md._MIME_EXT 同步; 这里独立维护一份, 避免跨模块私有依赖
_MIME_TO_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
}


def _resolve_mime_extension(mime: str) -> str:
    """Section 5.3: ext_from_mime — mime → 后缀 (无匹配默认 'bin')。"""
    return _MIME_TO_EXT.get(mime.lower(), "bin")


async def docx_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: UploadFileHandler | None = None,
) -> FormatReaderResult:
    """bytes → FormatReaderResult: ``mammoth`` html → ``html_to_md`` markdown。

    Args:
        buffer: docx 二进制内容。
        encoding: 文本编码 (docx 主体是 XML, 此参数保留以对齐 adapter 签名)。
        upload_file: 可选异步上传回调 ``(name, mime, bytes) -> {key}``。
            - 传 ``None`` 且 docx 含内嵌图 → 抛 ``RAGError(READER_PARSE)`` (Section 5.3)
            - 传 callback 时, docx 内的图片会被回调上传, key 进入 ``result.images``

    Returns:
        ``FormatReaderResult``:
            - ``raw_text``: markdown 文本
            - ``format_text=None``
            - ``images``: 上传成功的 key 列表
            - ``meta``: ``mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document'``

    Raises:
        RAGError: ``code=READER_PARSE``:
            - mammoth 解析失败 (Section 5.5)
            - docx 含内嵌图 + ``upload_file is None`` (Section 5.3)
    """
    # mammoth 需要 file-like (有 seek/name), BytesIO 包装 buffer
    # Section 5.1 + 5.2: 调 mammoth.convert_to_html, options 至少含 ignore_empty_paragraphs=False
    # mammoth 同步 + upload_file async, 通过 ``asyncio.to_thread`` 跑在 worker 线程,
    # 线程内可安全驱动 async 调用; image 回调内用 ``run_coroutine_sync``
    # 桥接 (内部仍走 asyncio.run, 但接口契约与项目其他 sync/async 桥接点一致)。
    images: list[str] = []

    def _build_options() -> dict[str, object]:
        opts: dict[str, object] = {
            "ignore_empty_paragraphs": False,
        }
        # Section 5.3: 总是挂图片回调, 这样 mammoth 遇到内嵌图时一定走到
        # _convert_image 里的"无 upload_file → 抛错"分支;
        # 不挂则 mammoth 走默认 base64 inline 行为, 我们没法拦截抛错
        opts["convert_image"] = mimages.img_element(_convert_image)
        return opts

    def _convert_image(image: object) -> dict[str, str]:
        """mammoth convertImage 回调 (Section 5.3 关键路径)。

        流程:
            1. 无 upload_file (且本函数被调用意味着 mammoth 遇到了内嵌图) → 抛错
               (Section 5.3: "Missing imageKeyOptions.prefix for parsed document image upload")
            2. 有 upload_file: 抽 image bytes → 调异步上传 → 返回 ``{"src": key}``
            3. 上传失败: log warning + 返回 ``{"src": ""}`` (mammoth 不会因为空 src 崩)

        mammoth 的 image 对象 API:
            - ``content_type: str`` (e.g. ``"image/png"``)
            - ``open()`` 上下文管理器 → 读 bytes
        """
        # 防御: 即便 build_options 已守卫, mammoth 在某些版本里也可能在条件外调用;
        # 这里再校验一次, 保证 Section 5.3 抛错一定生效
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
            # Section 5.3: name = uuid.uuid4() + ext_from_mime(mime)
            name = f"{uuid.uuid4()}.{_resolve_mime_extension(content_type)}"
            # upload_file 是 async; mammoth 同步回调; worker 线程无 event loop,
            # 通过 _run_async_upload (run_coroutine_sync) 安全驱动 async 调用
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
            # Section 5.3 抛错 (upload_file is None) 透传, 让 mammoth 把整篇失败
            raise
        except Exception as e:  # noqa: BLE001 — 单张图失败不毁整篇 docx
            logger.warning("docx image upload failed: %s", e)
            return {"src": ""}

    def _run_mammoth() -> object:
        """同步包: mammoth.convert_to_html (在 worker 线程内跑, 避免阻塞 event loop)。

        Section 5.5: 解析失败 → RAGError; Section 5.3 抛错透传。
        """
        try:
            return mammoth.convert_to_html(BytesIO(buffer), **_build_options())
        except RAGError:
            # Section 5.3 抛错 (来自 _convert_image) 透传
            raise
        except Exception as e:
            # Section 5.5: 解析失败统一包成 READER_PARSE +
            # "Can not read doc file, please convert to PDF"
            raise RAGError(
                code=ReaderErrorCode.PARSE,
                message=_PARSE_ERROR_MSG,
            ) from e

    mammoth_result: object = await asyncio.to_thread(_run_mammoth)
    # mammoth 返回 ``mammoth.results.Result`` (有 .value 属性), 但 to_thread 把它
    # 标注为 object; 强转让 mypy 通过
    html = mammoth_result.value  # type: ignore[attr-defined]

    # Section 5.4: html 已经含上传后的 key, 传 upload_file=None 给 html_to_md,
    # 避免 html2md 二次处理 base64 (此 html 不会有 base64, 但 None 是最安全的契约)
    markdown = await html_to_md(html, upload_file=None)

    # paragraph_count 估算: 按 markdown 中的段落分隔 (与 pptx 对齐)
    paragraph_count = sum(1 for line in markdown.split("\n") if line.strip())

    return FormatReaderResult(
        raw_text=markdown,
        format_text=None,
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
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
    """同步执行一次 async upload_file 调用 (在 asyncio.to_thread worker 线程内)。

    mammoth 的 convert_image 是同步回调, 但 ``UploadFileHandler`` 是 async;
    worker 线程没有 event loop, 因此可以安全驱动 async 调用。
    内部通过 ``run_coroutine_sync`` helper 执行, 而非裸 ``asyncio.run``,
    保持与项目其他 sync/async 桥接点 (VectorRetriever.invoke / FulltextRetriever.invoke)
    一致的接口契约 (coroutine factory, running loop 检测)。
    """

    async def _upload() -> UploadedFileResult:
        return await upload_file(name, mime, image_bytes)

    return run_coroutine_sync(_upload)
