"""PDF extension adapter: pypdf 抽文本 + pdf_text_postprocess 后处理。

:
    - 7.1 引擎: 用 LiteParse (无官方 Python 绑定), 本模块用 ``pypdf``
    替代; 行为等价 (文本抽取 + page 元数据), 但**无 char-level 坐标**,
    所以 7.5 后处理只能用文本特征 (见 ``pdf_text_postprocess`` 简化版)。
    - 7.2 配置: ``PdfReader(BytesIO(buffer))`` 直接打开。
    - 7.3 分批: ``for start in range(0, len(reader.pages), 100)`` — 100 页分批; PDF 没有 ``targetPages`` 概念, 按 index 分。
    - 7.4 收集: 每页 ``page.extract_text()`` 拿纯文本 (无 textItems 坐标)。
    - 7.5 后处理: 调 ``postprocess_lite_parse_pages(pages)`` 拿 ``raw_text``。
    - 7.6 返回: ``FormatReaderResult`` (raw_text, format_text=None, ...)。
    - mime: ``application/pdf``。
    - 错误: ``pypdf.errors.PdfReadError`` 等 → ``wrap_parse_error``。

    实现要点:
    - **不**抽 PDF 内嵌图; 后续 phase OCR / pdf2image 接入。
    - **不**做 PDF 加密检测; 损坏 / 加密抛
    ``wrap_parse_error`` 即可, 与 ``adapters/pdf.py`` 行为一致。
    - 入口函数为 ``async`` (对齐 reader/adapters 全部 ``async def`` 约定);
    后处理是 CPU-bound 但 IO 小, 同步调用即可。
"""

from __future__ import annotations

import logging
from io import BytesIO

from pypdf import PdfReader

from rag.ingest.reader.extensions.base import wrap_parse_error
from rag.ingest.reader.pdf_text_postprocess import postprocess_lite_parse_pages
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# PDF mime (RFC 8118)。
PDF_MIME = "application/pdf"

_BATCH_SIZE = 100


async def pdf_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",  # noqa: ARG001 — 保留签名 (PDF 是二进制)
    upload_file: object | None = None,  # noqa: ARG001 — PDF 不抽图, 保留签名
) -> FormatReaderResult:
    """bytes → ``FormatReaderResult``: pypdf 抽文本 + 简化版后处理。

    Args:
        buffer: PDF 二进制内容。
        encoding: 保留签名; PDF 是二进制, 无编码概念 (postprocess 也按 utf-8)。

    Returns:
        ``FormatReaderResult { raw_text, format_text=None, meta, images=[]}``

    Raises:
        RAGError: ``code=READER_PARSE`` — pypdf 解析失败 (损坏 / 加密) 时包装。
    """
    # 7.1+7.2 打开 + 7.3 分批 + 7.4 收集: 同步 IO, 在 async 函数内执行即可 (CPU-bound 极短)。
    # 所有 pypdf 内部异常 (PdfReader / extract_text 失败) 统一 wrap。
    try:
        reader = PdfReader(BytesIO(buffer))
        all_pages = list(reader.pages)
        page_texts: list[str] = []
        for start in range(0, len(all_pages), _BATCH_SIZE):
            batch = all_pages[start : start + _BATCH_SIZE]
            for page in batch:
                text = page.extract_text() or ""
                page_texts.append(text)
    except Exception as e:
        # pypdf 抛 PdfReadError / EmptyFileError / extract_text 失败等 → RAGError。
        raise wrap_parse_error("<buffer:pdf>", e, "pypdf") from e

    # 7.5 后处理: 简化版, 文本特征 (无坐标)。
    raw_text = postprocess_lite_parse_pages(page_texts)

    # paragraph_count: 非空行数估算 (与 pptx / docx adapter 一致)。
    paragraph_count = sum(1 for line in raw_text.split("\n") if line.strip())

    # 7.6 返回。
    return FormatReaderResult(
        raw_text=raw_text,
        format_text=None,
        images=[],
        extras={},
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
            mime=PDF_MIME,
            encoding=encoding,  # 保留传入, postprocess 也按 utf-8
            size_bytes=len(buffer),
            page_count=len(all_pages),
            paragraph_count=paragraph_count,
        ),
    )
