"""Markdown / HTML 标题抽取工具。"""

from __future__ import annotations

import re

# Markdown 标题: `# Title` ~ `##### Title`
MD_HEADING_RE: re.Pattern[str] = re.compile(r"^(#{1,5})\s+(.+)$", re.MULTILINE)
# HTML h1-h6 标题
HTML_HEADING_RE: re.Pattern[str] = re.compile(
    r"<h([1-6])>(.*?)</h\1>", re.IGNORECASE | re.DOTALL
)
# 仅 h1 (用于文档级 title 抽取)
HTML_H1_ONLY_RE: re.Pattern[str] = re.compile(
    r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL
)


def extract_first_title(text: str) -> str | None:
    """从文本中抽第一个 Markdown `#` 标题或 HTML `<h1>`, 失败返回 None。

    与 ``ingest/pipeline.py`` 的原 `_extract_title` 行为一致。
    """
    m = MD_HEADING_RE.search(text)
    if m:
        title = m.group(2).strip()
        if title:
            return title
    m = HTML_H1_ONLY_RE.search(text)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title:
            return title
    return None


def collect_headings(text: str) -> list[tuple[int, int, str]]:
    """收集文本中全部 Markdown 与 HTML 标题, 返回 ``(offset, level, title)`` 列表。

    偏移升序, 与 ``chunker/core.py`` 的 ``_heading_stack_for_chunk`` 行为一致。
    """
    hits: list[tuple[int, int, str]] = []
    for m in MD_HEADING_RE.finditer(text):
        level = len(m.group(1))
        title = m.group(2).strip()
        if title:
            hits.append((m.start(), level, title))
    for m in HTML_HEADING_RE.finditer(text):
        level = int(m.group(1))
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        if title:
            hits.append((m.start(), level, title))
    hits.sort(key=lambda x: x[0])
    return hits
