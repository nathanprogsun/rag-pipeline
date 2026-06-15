"""pptx 格式适配器: ``parse_office`` 薄封装, 抽取每页 slide 文本。"""

from __future__ import annotations

from rag.ingest.reader.extensions.base import wrap_parse_error
from rag.ingest.reader.parse_office import parse_office
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

# pptx 标准 mime (OOXML 官方命名)。
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


async def pptx_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: object | None = None,  # noqa: ARG001 — pptx 不抽图, 保留签名
) -> FormatReaderResult:
    """将 pptx 字节内容解析为 ``FormatReaderResult``。

    Args:
        buffer: pptx 二进制内容。
        encoding: 文本编码, 主要给 XML decode 兜底。
        upload_file: 保留以对齐 ``FormatAdapter`` 协议, pptx 不抽图故忽略。

    Returns:
        ``FormatReaderResult { raw_text, format_text=None, meta, images=[], extras={} }``。

    Raises:
        RAGError: ``code=READER_PARSE`` —— ``parse_office`` 解析失败时包装。
    """
    try:
        # ``parse_office`` 是 sync, 内部纯 CPU/disk, 无需 to_thread 卸载;
        # 这里用 ``async def`` 薄包装以对齐 FormatAdapter 协议
        raw_text = parse_office(buffer, extension="pptx", encoding=encoding)
    except Exception as e:
        # 用 wrap_parse_error 统一替换 parser 后缀
        raise wrap_parse_error("<buffer:pptx>", e, "python-zipfile") from e

    paragraph_count = sum(1 for line in raw_text.split("\n") if line.strip())

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=None,
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
            mime=PPTX_MIME,
            encoding=encoding,
            size_bytes=len(buffer),
            paragraph_count=paragraph_count,
        ),
        images=[],
        extras={},
    )
