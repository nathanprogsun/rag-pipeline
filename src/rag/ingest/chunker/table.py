r"""Markdown 表格检测 + 切分。

严格 4 条件校验:
  1. >= 2 行
  2. header 行以 | 开头且以 | 结尾
  3. sep 行匹配 ^(\|[\s:]*-+[\s:]*)+\|$
  4. data 行 (如有) 也以 | 开头且以 | 结尾

兜底: 单行超 chunk_size → 调用 common_split 按单元格递归切。
"""

from __future__ import annotations

import re

from .recursive import common_split
from .rules import build_steps

_SEP_RE = re.compile(r"^(\|[\s:]*-+[\s:]*)+\|$")
_PIPE_RE = re.compile(r"^\s*\|.*\|\s*$")


def str_is_md_table(text: str) -> bool:
    lines = text.split("\n")
    if len(lines) < 2:
        return False
    header = lines[0].strip()
    if not header.startswith("|") or not header.endswith("|"):
        return False
    sep = lines[1].strip()
    if not _SEP_RE.match(sep):
        return False
    for line in lines[2:]:
        s = line.strip()
        if s and not _PIPE_RE.match(line):
            return False
    return True


def _split_oversized_row(
    row: str,
    chunk_size: int,
    max_size: int,
    overlap_len: int,
    paragraph_chunk_min_size: int,
) -> list[str]:
    """单行超 chunk_size 时, 按 cells 切分并对每个 cell 递归走 common_split。

    保留 Markdown 行结构: 把 cell 内容转成 '|' 分隔的扁平行, 切完后拼回。
    """
    cell_match = re.match(r"^\s*\|(.*)\|\s*$", row)
    if not cell_match:
        return common_split(
            text=row,
            step=0,
            last_text="",
            parent_title="",
            rules=build_steps(
                chunk_size=chunk_size,
                max_size=max_size,
                paragraph_chunk_deep=5,
            ),
            chunk_size=chunk_size,
            max_size=max_size,
            overlap_len=overlap_len,
            paragraph_chunk_min_size=paragraph_chunk_min_size,
        )
    cells = [c.strip() for c in cell_match.group(1).split("|")]
    rebuilt: list[str] = []
    for cell in cells:
        if not cell:
            rebuilt.append("")
            continue
        sub_chunks = common_split(
            text=cell,
            step=0,
            last_text="",
            parent_title="",
            rules=build_steps(
                chunk_size=chunk_size,
                max_size=max_size,
                paragraph_chunk_deep=5,
            ),
            chunk_size=chunk_size,
            max_size=max_size,
            overlap_len=overlap_len,
            paragraph_chunk_min_size=paragraph_chunk_min_size,
        )
        rebuilt.extend(sub_chunks)
    return ["| " + " | ".join(rebuilt) + " |"]


def markdown_table_split(
    text: str,
    chunk_size: int = 1000,
    *,
    max_size: int | None = None,
    overlap_ratio: float = 0.15,
    paragraph_chunk_min_size: int = 100,
) -> list[str]:
    """按 chunk_size 切分, 每块重复 header + sep。

    单行超 chunk_size → 走 _split_oversized_row 按 cell 递归兜底, 避免硬并。
    """
    if not str_is_md_table(text):
        return [text]

    max_size = max_size if max_size is not None else chunk_size * 8
    overlap_len = int(chunk_size * overlap_ratio)
    lines = text.split("\n")
    header = lines[0]
    sep = lines[1]
    data = lines[2:]

    header_size = len(header.split("|")) - 2
    rebuilt_sep = "| " + " | ".join(["---"] * max(1, header_size)) + " |"

    chunks: list[str] = []
    buf_lines: list[str] = [header, rebuilt_sep]
    buf_len = sum(len(x) for x in buf_lines)

    for row in data:
        row_len = len(row)
        # 单行超 chunk_size → 走兜底递归
        if row_len > chunk_size:
            # 先 flush 当前缓冲
            if len(buf_lines) > 2:
                chunks.append("\n".join(buf_lines))
                buf_lines = [header, rebuilt_sep]
                buf_len = sum(len(x) for x in buf_lines)
            # 兜底切该行, 每段单独成块 (重复 header + sep)
            row_chunks = _split_oversized_row(
                row,
                chunk_size=chunk_size,
                max_size=max_size,
                overlap_len=overlap_len,
                paragraph_chunk_min_size=paragraph_chunk_min_size,
            )
            for sub in row_chunks:
                chunks.append("\n".join([header, rebuilt_sep, sub]))
            continue

        if buf_len + row_len > int(chunk_size * 1.2) and len(buf_lines) > 2:
            chunks.append("\n".join(buf_lines))
            buf_lines = [header, rebuilt_sep, row]
            buf_len = sum(len(x) for x in buf_lines)
        else:
            buf_lines.append(row)
            buf_len += row_len

    if len(buf_lines) > 2:
        chunks.append("\n".join(buf_lines))

    _ = sep  # sep 保留兼容
    return chunks
