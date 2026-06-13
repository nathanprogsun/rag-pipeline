"""csv extension adapter: ``read_raw_text`` + stdlib ``csv.reader`` → markdown table。

:

    4.1 ``read_raw_text`` 解码 buffer 拿 raw_text
    4.2 ``csv.reader(io.StringIO(raw_text))`` (stdlib 默认 excel dialect,
    Papa.parse 的宽松 CSV 行为近似)
    4.3 ``formatText``:
    - 第 0 行 = header
    - 分隔行 ``| --- | --- | ... |`` (列数同 header)
    - 数据行 ``| cell | cell | ... |`` (列数同 header, 不足补空)
    - 单元格内 ``\\n`` → ``\\\\n`` (转义, 避免破坏 markdown table 行结构)
    - 空行跳过
    - 只 header 没 data 也输出 (header + sep)
    4.4 返回 ``FormatReaderResult(raw_text, format_text, meta,
    images=[], extras={})``, mime ``text/csv``
    4.5 错误: ``csv.Error`` / ``UnicodeDecodeError`` → ``wrap_parse_error`` /
    ``wrap_encoding_error``

    设计:
    - 不依赖 pandas, 用 stdlib ``csv.reader`` (Section 4.2 注释明确禁止)。
    - ``format_text`` 始终填充 (只要有 header 就输出 header + sep, 即便没 data),
    与 Section 4.3 "只有 header 没有 data 也输出" 对齐。
    - ``extras`` 保留 ``row_count`` 方便后续 chunker/审计使用。
"""

from __future__ import annotations

import csv as _csv_stdlib
import logging
from io import StringIO
from typing import Final

from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.raw_text import UploadFileHandler, read_raw_text
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# csv mime (RFC 4180 + IANA 注册名)。
CSV_MIME: Final[str] = "text/csv"

# 单元格内换行在 markdown table 内的转义形式 (Section 4.3)。
_CELL_NEWLINE_REPLACEMENT: Final[str] = "\\n"


def _escape_cell(value: str) -> str:
    """Section 4.3: 单元格内 ``\\n`` → ``\\\\n`` (转义, 不破坏 md table 行结构)。"""
    return value.replace("\n", _CELL_NEWLINE_REPLACEMENT)


def _format_row(cells: list[str], width: int) -> str:
    """把一行 cells 拼成 markdown table 行, 不足 ``width`` 用空串补齐。"""
    if len(cells) < width:
        cells = cells + [""] * (width - len(cells))
    elif len(cells) > width:
        cells = cells[:width]
    escaped = [_escape_cell(c) for c in cells]
    return "| " + " | ".join(escaped) + " |"


def _to_format_text(raw_text: str) -> str | None:
    """Section 4.3: raw_text → markdown table 字符串, 无 header 返回 None。"""
    if not raw_text:
        return None

    reader = _csv_stdlib.reader(StringIO(raw_text))
    rows: list[list[str]] = []
    for row in reader:
        # Section 4.3: 空行跳过 (csv.reader 在全空行时也会返回 ``[""]`` 或 ``[]``)
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
    # 分隔行 (Section 4.3): ``| --- | --- | ... |``, 列数同 header
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
    """bytes → ``FormatReaderResult`` (csv 专属, 含 markdown table format_text)。

    Args:
        buffer: csv 二进制内容。
        encoding: 文本编码 (默认 utf-8)。
        upload_file: 透传给 ``read_raw_text`` 的异步上传回调 (csv 场景一般用不到,
        除非 csv 内嵌 markdown base64 图, 极少见)。

    Returns:
        ``FormatReaderResult { raw_text, format_text, meta,
        images=[], extras={row_count} }``:
        - ``raw_text``: 解码后的 csv 原文
        - ``format_text``: markdown table 视图 (header + sep + rows);
          仅有 header 也输出 (header + sep); 无 header 返回 None
        - ``meta.mime = "text/csv"``

    Raises:
        RAGError: ``code=READER_ENCODING`` (decode 失败) /
        ``code=READER_PARSE`` (csv 解析失败)。
    """
    try:
        raw_text = await read_raw_text(
            buffer, encoding=encoding, upload_file=upload_file
        )
    except UnicodeDecodeError as e:
        # ``read_raw_text`` 内部已兜底, 这里主要是防御性捕获 (e.g. codecs 内部异常)。
        raise wrap_encoding_error("<buffer:csv>", e, "csv") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:csv>", e, "csv") from e

    # Section 4.3: format_text 转换。csv.Error 在转换过程中 (非 decode) 抛。
    try:
        format_text = _to_format_text(raw_text)
    except _csv_stdlib.Error as e:
        raise wrap_parse_error("<buffer:csv>", e, "csv") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:csv>", e, "csv") from e

    # 估算行数: 按 \n split 后非空行 (与 dispatch / 审计对齐)
    row_count = sum(1 for line in raw_text.split("\n") if line.strip())

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=format_text,
        meta=DocMeta(
            datasource="api",  # 占位, dispatch 覆盖
            mime=CSV_MIME,
            encoding=encoding,
            size_bytes=len(buffer),
        ),
        images=[],
        extras={"row_count": row_count},
    )
