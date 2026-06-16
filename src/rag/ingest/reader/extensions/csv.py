"""csv 格式适配器: 解码 buffer 后用 stdlib ``csv.reader`` 转为 markdown 表格。"""

from __future__ import annotations

import csv as _csv_stdlib
import logging
from io import StringIO
from typing import Final

from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.raw_text import read_raw_text
from rag.ingest.reader.types import FormatReaderResult, UploadFileHandler
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# csv 的标准 mime。
CSV_MIME: Final[str] = "text/csv"

# markdown 表格内单元格换行的转义形式, 避免破坏行结构。
_CELL_NEWLINE_REPLACEMENT: Final[str] = "\\n"


def _escape_cell(value: str) -> str:
    """转义单元格内换行符, 保持 markdown 表格行结构。"""
    return value.replace("\n", _CELL_NEWLINE_REPLACEMENT)


def _format_row(cells: list[str], width: int) -> str:
    """把一行 cells 拼成 markdown 表格行, 不足 ``width`` 用空串补齐。"""
    if len(cells) < width:
        cells = cells + [""] * (width - len(cells))
    elif len(cells) > width:
        cells = cells[:width]
    escaped = [_escape_cell(c) for c in cells]
    return "| " + " | ".join(escaped) + " |"


def _to_format_text(raw_text: str) -> str | None:
    """将 csv 原文转为 markdown 表格字符串, 无表头时返回 ``None``。"""
    if not raw_text:
        return None

    reader = _csv_stdlib.reader(StringIO(raw_text))
    rows: list[list[str]] = []
    for row in reader:
        # 空行跳过; csv.reader 在全空行时也会返回 ``[""]`` 或 ``[]``
        if not row:
            continue
        if len(row) == 1 and row[0] == "":
            continue
        rows.append(row)

    if not rows:
        return None

    header = rows[0]
    data_rows = rows[1:]
    width = len(header)

    lines: list[str] = [_format_row(header, width)]
    # 分隔行, 列数与表头一致
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for r in data_rows:
        lines.append(_format_row(r, width))

    return "\n".join(lines)


async def csv_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",
    upload_file: UploadFileHandler | None = None,
) -> FormatReaderResult:
    """将 csv 字节内容解析为 ``FormatReaderResult``。

    Args:
        buffer: csv 二进制内容。
        encoding: 文本编码, 默认 ``utf-8``。
        upload_file: 透传给 ``read_raw_text`` 的异步上传回调, csv 场景一般用不到。

    Returns:
        ``FormatReaderResult``:
        - ``raw_text``: 解码后的 csv 原文。
        - ``format_text``: markdown 表格视图; 仅有表头也输出 (表头 + 分隔行),
          无表头返回 ``None``。
        - ``meta.mime = "text/csv"``。

    Raises:
        RAGError: ``code=READER_ENCODING`` 解码失败; ``code=READER_PARSE`` 解析失败。
    """
    try:
        raw_text = await read_raw_text(buffer, encoding=encoding)
    except UnicodeDecodeError as e:
        # ``read_raw_text`` 内部已有兜底, 此处是防御性捕获
        raise wrap_encoding_error("<buffer:csv>", e, "csv") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:csv>", e, "csv") from e

    try:
        format_text = _to_format_text(raw_text)
    except _csv_stdlib.Error as e:
        raise wrap_parse_error("<buffer:csv>", e, "csv") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:csv>", e, "csv") from e

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=format_text,
        meta=DocMeta(mime=CSV_MIME),
        images=[],
    )
