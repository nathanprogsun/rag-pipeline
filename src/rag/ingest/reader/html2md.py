"""HTML → markdown 转换器。

要点:
 - 引擎: `markdownify` (`Turndown` 在 Python 端的最近似实现)
 - 删除标签: `i` / `script` / `iframe` / `style`
 - 媒体 (`video` / `source` / `audio`) → `[src](src)`, 优先自身 src, 否则第一个 `<source>` 的 src
 - base64 图片: src 置空, 避免大体积 base64 进入 markdown
 - 超大 HTML: `len(html) > MAX_HTML_TRANSFORM_CHARS` → 原样返回
 - 异常处理: 转换抛错 → log warning + 返回 ``""``

`markdownify` 的 `strong_em_symbol` 同时控制 strong 和 em, 不能分别给 `_` / `**`。
本模块先用 `_` 让 `markdownify` 输出 `__x__` 形式, 再在后处理阶段把
`__x__` 还原为 `**x**`。
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Final, cast

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as _md

logger = logging.getLogger(__name__)

# 超大 HTML 直接原样返回, 不走 markdownify
MAX_HTML_TRANSFORM_CHARS: Final[int] = 1_000_000

# 匹配 `<img ... src=("|')data:<mime>;base64,<data>\1 ...>`
_BASE64_SRC_RE: Final[re.Pattern[str]] = re.compile(
    r"""\bsrc\s*=\s*(["'])data:([^;]+);base64,([A-Za-z0-9+/=]+)\1""",
    re.IGNORECASE,
)


async def html_to_md(html: str) -> str:
    """HTML 字符串 → markdown 字符串 (async)。

    Args:
        html: 输入 HTML (可能含 base64 图片、media 标签、script 等)。

    Returns:
        转换后的 markdown; 失败返回 `""`; 超大 HTML 原样返回。
    """
    if not html:
        return ""

    # 超大直接返回原 HTML
    if len(html) > MAX_HTML_TRANSFORM_CHARS:
        return html

    try:
        # base64 图片预处理: src 置空, 避免大体积 base64 进入 markdown
        processed = _strip_base64_images(html)

        # 删除 i / script / iframe / style; media → <a href="src">src</a>
        processed = _preprocess_media_and_strip(processed)

        # markdownify 转换
        md = _run_markdownify(processed)

        # 后处理
        return simple_markdown_text(md)
    except Exception as e:
        logger.warning("html_to_md failed (returning ''): %s", e)
        return ""


# ---------------------------------------------------------------------------
# base64 图片处理
# ---------------------------------------------------------------------------


def _strip_base64_images(html: str) -> str:
    """把 ``<img src="data:...;base64,...">`` 替换为空 src。"""
    return _BASE64_SRC_RE.sub(r"src=\1\1", html)


# ---------------------------------------------------------------------------
# 标签删除 + 媒体重写
# ---------------------------------------------------------------------------

_STRIP_TAGS: Final[tuple[str, ...]] = ("i", "script", "iframe", "style")
_MEDIA_TAGS: Final[tuple[str, ...]] = ("video", "source", "audio")


def _resolve_media_src(node: Tag) -> str | None:
    """video/audio 先看自身 src, 否则取第一个 `<source>` 的 src。"""
    raw_src = node.get("src")
    src = cast("str | None", raw_src)
    if src:
        return src
    child = node.find("source")
    if child is not None and isinstance(child, Tag):
        raw_child_src = child.get("src")
        child_src = cast("str | None", raw_child_src)
        if child_src:
            return child_src
    return None


def _preprocess_media_and_strip(html: str) -> str:
    """删标签 + media → `<a href="src">src</a>`。"""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all(_STRIP_TAGS):
        tag.decompose()

    for node in list(soup.find_all(_MEDIA_TAGS)):
        src = _resolve_media_src(node)
        if not src:
            node.decompose()
            continue
        anchor = soup.new_tag("a", href=src)
        anchor.string = src
        node.replace_with(anchor)

    return str(soup)


# ---------------------------------------------------------------------------
# markdownify 包装
# ---------------------------------------------------------------------------


def _run_markdownify(html: str) -> str:
    """调 `markdownify`, 配置对齐 `Turndown` 选项。

    `markdownify` 用 `strong_em_symbol` 同时控制 strong 和 em, 无法分别给
    '_' 和 '**'; 选用 '_' 然后在 `simple_markdown_text` 阶段把 `__x__`
    还原为 `**x**`, em `_x_` 保持不变。
    """
    return _md(
        html,
        heading_style="ATX",
        bullets="-",
        strong_em_symbol="_",
        autolinks=False,
        default_title=False,
        escape_underscores=False,
        escape_asterisks=False,
        strip=list(_STRIP_TAGS),
    )


# ---------------------------------------------------------------------------
# 后处理 simple_markdown_text
# ---------------------------------------------------------------------------

_ESCAPED_MD_CHARS: Final[re.Pattern[str]] = re.compile(r"""\\([#`!*()+\-_\[\]{}\\.])""")

# markdownify 偶尔把链接里的换行原样保留, 需要归一
_LINK_NEWLINE_RE: Final[re.Pattern[str]] = re.compile(
    r"\[([^\]]+)\]\(([^)]+)\)",
)

# markdownify 在 strong_em_symbol='_' 时把 <strong> 输出为 `__x__`,
# 还原为 strong_delimiter='**' 形式
_DOUBLE_UNDERSCORE_PAIR: Final[re.Pattern[str]] = re.compile(
    r"(?<!\w)__([^_\n][^_\n]*?[^_\n]|\S)__(?!\w)"
)


def simple_markdown_text(raw: str) -> str:
    """轻量后处理。

    步骤:
        1. 实体反转义 (e.g. `&amp;` → `&`)
        2. 链接 `[text](url)` 内 text 的换行归一为空格
        3. 把 `markdownify` 的 `__strong__` 还原为 `**strong**`
        4. 反转义误转义的 markdown 元字符 (e.g. `\\#` → `#`)
        5. 折叠多余空白
        6. 收尾 trim
    """
    # 1. 实体反转义
    text = unescape(raw)

    # 2. 链接内换行归一: `[text\nmore](url)` → `[text more](url)`
    def _clean_link(m: re.Match[str]) -> str:
        link_text = m.group(1).replace("\n", " ").strip()
        url = m.group(2).strip()
        if not url:
            return ""
        return f"[{link_text}]({url})"

    text = _LINK_NEWLINE_RE.sub(_clean_link, text)

    # 3. __strong__ → **strong** (markdownify 用 '_' 当 strong,Turndown 用 '**')
    text = _DOUBLE_UNDERSCORE_PAIR.sub(r"**\1**", text)

    # 4. 反转义 markdown 元字符
    if _ESCAPED_MD_CHARS.search(text):
        text = _ESCAPED_MD_CHARS.sub(r"\1", text)

    # 5. 折叠连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 6. trim
    return text.strip()