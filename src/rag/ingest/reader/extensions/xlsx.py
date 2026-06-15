"""xlsx extension adapter: openpyxl 抽 sheet → CSV + markdown table 双输出。

9.1 解析: ``openpyxl.load_workbook(BytesIO(buffer), data_only=True, read_only=True)``
    - ``data_only=True``: 取计算后的值, 不是公式
    - ``read_only=True``: 大文件省内存 (但只能 read, 不 write, 这里只读 OK)
    - 隐藏 sheet 也要读
9.2 rawText: 每 sheet
    - ``ws.iter_rows(values_only=True)`` → 二维数组
    - 单元格 ``str()`` 化, ``None`` → ``""``
    - 过滤完全空行 (所有 cell 都是空串)
    - 行内 ``","``, 行间 ``"\\n"``, 多 sheet 直接 ``"\\n"`` 拼接 (无 sheet 标题)
9.3 formatText: 每 sheet
    - header = rows[0], body = rows[1:]
    - ``len(rows) < 2`` (只有 header 或空) → 跳过该 sheet 的 formatText 输出
    - 分隔行 ``"|" + "|".join(["---"] * len(header)) + "|"``
      (**前后** 有 ``|``, 与 csv markdown 风格一致)
    - body 行 ``"| " + " | ".join([cell.replace("\\n", "\\\\n")]) + " |"``
    - 多 sheet 用 ``CUSTOM_SPLIT_SIGN`` 拼接
9.5 单元格 ``\\n`` → ``\\\\n`` (markdown table 不被多行破坏)
9.6 返回 ``FormatReaderResult(raw_text, format_text or None, ...)``
    - 若所有 sheet 都被 ``len(rows) < 2`` 过滤掉 → ``format_text = None``

设计:
- 不复用旧 ``adapters/xlsx``: 那里是同步函数; 本模块的 ``xlsx_adapter`` 走
  ``async def`` 以对齐 ``FormatAdapter`` 协议 (虽然实现不 await, 仍保持
  一致的 async 签名, 让 ``dispatch`` 后续替换无破坏)。
- ``CUSTOM_SPLIT_SIGN`` 与 ``adapters/xlsx`` / ``csv.ts`` / ``xlsx.ts`` 对齐,
  与 ``chunker.rules.CUSTOM_SPLIT_SIGN`` 同字面值但语义不同 (chunker 那个
  是 chunk 拼接用的, xlsx 这里是 sheet 间分隔)。
"""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO
from typing import Final

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from rag.ingest.reader.extensions.base import wrap_parse_error
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

# xlsx mime (OOXML 官方命名)。
XLSX_MIME: Final[str] = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

# 多 sheet 的 markdown table 拼接分隔符 (Section 9.3, 对齐 xlsx.ts / csv.ts)。
# 注意: 与 ``chunker.rules.CUSTOM_SPLIT_SIGN`` 字面值相同, 但语义不同 —
# chunker 那个是 chunk 间占位符, xlsx 这里是 sheet 间分隔, 不要混用。
CUSTOM_SPLIT_SIGN: Final[str] = "-----CUSTOM-SPLIT-SIGN-----"


def _sheet_to_rows(ws: object) -> list[list[str]]:
    """把 sheet 转成 ``[[cell, ...], ...]``, None → '', 过滤完全空行。

    Section 9.2:
    - 单元格 ``str()`` 化 (Section 9.5 在 markdown 视图才转 ``\\n``)
    - 完全空行 (所有 cell 都是空串) 跳过
    """
    raw_data: list[tuple[object, ...]] = list(ws.iter_rows(values_only=True))  # type: ignore[attr-defined]
    rows: list[list[str]] = []
    for row in raw_data:
        cells = ["" if v is None else str(v) for v in row]
        # 过滤"完全空行": 全部 cell 都是空串 (Section 9.6 隐含要求)
        if all(c == "" for c in cells):
            continue
        rows.append(cells)
    return rows


def _sheet_to_csv(rows: list[list[str]]) -> str:
    """把 ``[[cell, ...], ...]`` 拼成 CSV 文本 (行内 ``,``, 行间 ``\\n``)。"""
    return "\n".join(",".join(row) for row in rows)


def _sheet_to_md_table(rows: list[list[str]]) -> str | None:
    """把 ``[[cell, ...], ...]`` 拼成 markdown table, 不足 2 行返回 None。

    Section 9.3 + 9.4:
    - ``len(rows) < 2`` → 跳过 (只有 header 或空) → 返回 None
    - 分隔行 ``"|" + "|".join(["---"] * len(header)) + "|"`` (前后有 ``|``)
    - body 行 ``"| " + " | ".join([...]) + " |"``
    - 单元格 ``\\n`` → ``\\\\n`` (Section 9.5)
    """
    if len(rows) < 2:
        return None

    header = rows[0]
    body = rows[1:]
    width = len(header)

    lines: list[str] = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join(["---"] * width) + "|",
    ]
    for row in body:
        # 列数补齐 / 截断到 header 宽度
        if len(row) < width:
            row = list(row) + [""] * (width - len(row))
        elif len(row) > width:
            row = list(row)[:width]
        escaped = [c.replace("\n", "\\n") for c in row]
        lines.append("| " + " | ".join(escaped) + " |")
    return "\n".join(lines)


async def xlsx_adapter(
    buffer: bytes,
    *,
    encoding: str = "utf-8",  # xlsx 不需要 encoding, 保留对齐签名
) -> FormatReaderResult:
    """bytes → ``FormatReaderResult`` (xlsx 专属, raw_text=CSV, format_text=md table)。

    Args:
        buffer: xlsx 二进制内容。
        encoding: 占位参数 (xlsx 是二进制 OOXML, 不需要文本编码),
            保留以对齐 ``FormatAdapter`` 签名。

    Returns:
        ``FormatReaderResult { raw_text, format_text, meta,
        images=[], extras={} }``:
        - ``raw_text``: 多 sheet 的 CSV 拼接 (sheet 间 ``\\n``, 行内 ``,``,
          无 sheet 标题)
        - ``format_text``: 多 sheet 的 markdown table 拼接 (sheet 间
          ``CUSTOM_SPLIT_SIGN``); 若所有 sheet 都被 ``len(rows) < 2`` 过滤
          → ``None``
        - ``meta.mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"``

    Raises:
        RAGError: ``code=READER_PARSE`` — openpyxl 解析失败 (坏 zip / 非法 xlsx)。
    """
    del encoding  # unused; xlsx 是二进制, 不需 decode

    try:
        # 9.1: data_only=True 取值, read_only=True 省内存
        wb = load_workbook(BytesIO(buffer), data_only=True, read_only=True)
    except (InvalidFileException, zipfile.BadZipFile) as e:
        raise wrap_parse_error("<buffer:xlsx>", e, "openpyxl") from e
    except Exception as e:
        # openpyxl 内部也可能抛 OSError / ValueError / KeyError 等, 统一包装
        raise wrap_parse_error("<buffer:xlsx>", e, "openpyxl") from e

    csv_chunks: list[str] = []
    md_chunks: list[str] = []

    try:
        for ws in wb.worksheets:
            rows = _sheet_to_rows(ws)

            # 9.2: raw_text — 所有非空 sheet 都贡献 CSV 块
            if rows:
                csv_chunks.append(_sheet_to_csv(rows))

            # 9.3: format_text — 只有 len(rows) >= 2 的 sheet 才贡献 md table
            md = _sheet_to_md_table(rows)
            if md is not None:
                md_chunks.append(md)
    finally:
        # read_only 模式: 显式 close 释放内存 + 文件句柄 (openpyxl 文档建议)
        wb.close()

    # 9.2: 多 sheet 直接 "\\n" 拼接, 无 sheet 标题
    raw_text = "\n".join(csv_chunks)
    # 9.3: 多 sheet 用 CUSTOM_SPLIT_SIGN 拼接; 若全部被过滤 → None
    format_text: str | None = CUSTOM_SPLIT_SIGN.join(md_chunks) if md_chunks else None

    return FormatReaderResult(
        raw_text=raw_text,
        format_text=format_text,
        meta=DocMeta(
            datasource="file",  # 占位, dispatch 覆盖
            mime=XLSX_MIME,
            encoding="utf-8",  # 二进制容器, encoding 概念不适用; 占位 utf-8
            size_bytes=len(buffer),
        ),
        images=[],
        extras={},
    )
