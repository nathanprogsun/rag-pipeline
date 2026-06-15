"""PDF 文本后处理: 简化版 (文本特征)。

输入格式: ``pages: list[str]`` (每页纯文本) 而非 ``list[dict]`` with
textItems; 与 ``pypdf.Page.extract_text()`` 输出对齐。

本模块采用简化版实现, 与严格 1:1 对齐需要 char-level 坐标
(``pdfplumber`` 抽坐标后复刻), 工作量大且不阻塞当前价值。简化版要点:
 - 页眉/页脚检测: 文本特征 — 连续 ``repeated_noise_min_count`` 页有相同首行/末行 → 标记为 noise
 - 重复行删除: 跨所有 page, ``text.strip()`` 出现次数 ≥ ``repeated_noise_min_count``
   且长度 ≤ ``repeated_noise_max_length`` 的行全删
 - 纯页码删除: ``^(\d+|-\s*\d+\s*-|\d+\s*/\s*\d+|Page\s+\d+|-\s*\d+\s*-)$``
 - 合并视觉换行: 段末标点 (``.。!?``) 不合并, bullet 行 (以 ``-`` / ``*`` / 数字 ``.`` 开头)
   不合并, 段间空行不合并, 其他情况下后续行直接接到上一行末尾
 - ``normalize_unicode``: ``unicodedata.normalize('NFKC', text)``
 - 段落分隔: ``\\n\\n`` 分段, ``\\n`` 行内, 末尾保留 ``\\n``

NOTE: 11 个参数中, 当前简化版实际用到的只有 4 个
(`repeated_noise_min_count`, `repeated_noise_max_length`, `drop_pure_page_number`,
`normalize_unicode`); 其余 7 个参数保留签名仅为对齐。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# 7 个保留签名但简化版未使用的参数: 文档说明 + 保留位
# - trim_page_edge: 简化为按页文本首尾裁
# - header_ratio / footer_ratio: 简化为文本首末行 (语义等价)
# - line_y_ratio / min_space_gap_ratio / wide_space_gap_ratio: 需要 x/y 坐标
# - merge_visual_lines: 简化为规则 (标点/bullet/空行) — 保留, 默认 True

# 匹配: ``1`` (纯数字) / ``- 2 -`` (带 dash 包围) / ``1/3`` (n/m) /
_PURE_PAGE_NUMBER_RE = re.compile(
    r"^(\d+|-\s*\d+\s*-|\d+\s*/\s*\d+|Page\s+\d+|-\s*\d+\s*-)\s*$",
    re.IGNORECASE,
)

# 合并视觉换行时, 上一行以此结尾 → 不与下一行合并.
_END_PUNCT_RE = re.compile(r"[。.!?！?]\s*$")

# bullet 行前缀 (``-`` / ``*`` / ``1.`` / ``2.``)
_BULLET_PREFIX_RE = re.compile(r"^\s*([-*]|\d+\.)\s+")


# ─────────────────────────────────────────────────────────────────────────────
# 公共入口
# ─────────────────────────────────────────────────────────────────────────────


def postprocess_lite_parse_pages(
    pages: list[str],
    *,
    trim_page_edge: bool = True,  # noqa: ARG001 — 保留签名
    header_ratio: float = 0.05,  # noqa: ARG001 — 保留签名
    footer_ratio: float = 0.05,  # noqa: ARG001 — 保留签名
    line_y_ratio: float = 0.55,  # noqa: ARG001 — 保留签名
    min_space_gap_ratio: float = 0.35,  # noqa: ARG001 — 保留签名
    wide_space_gap_ratio: float = 1.2,  # noqa: ARG001 — 保留签名
    merge_visual_lines: bool = True,
    remove_repeated_page_noise: bool = True,
    repeated_noise_min_count: int = 3,
    repeated_noise_max_length: int = 30,
    drop_pure_page_number: bool = True,
    normalize_unicode: bool = False,
) -> str:
    """PDF 文本后处理。

    Args:
        pages: 每页纯文本 (例如 ``pypdf.Page.extract_text()`` 输出)。
        trim_page_edge: 保留签名, 简化版默认 ``True`` 时裁掉每页首尾空行。
        header_ratio: 保留签名, 简化版用文本首末行等价语义。
        footer_ratio: 保留签名, 简化版用文本首末行等价语义。
        line_y_ratio: 保留签名, 简化版无 x/y 坐标, 无效果。
        min_space_gap_ratio: 保留签名, 简化版无 x/y 坐标, 无效果。
        wide_space_gap_ratio: 保留签名, 简化版无 x/y 坐标, 无效果。
        merge_visual_lines: 是否合并视觉换行 (段末标点 / bullet / 空行不合并)。
        remove_repeated_page_noise: 是否删除跨页重复出现的行。
        repeated_noise_min_count: 重复行最小出现次数 (≥ 该值才视为 noise)。
        repeated_noise_max_length: 重复行最大长度 (噪声通常是短字符串如 ``1`` /
            ``第 1 页``)。
        drop_pure_page_number: 是否删除纯页码行 (匹配 ``^\\d+$`` 等)。
        normalize_unicode: 是否对结果做 ``NFKC`` 归一化 (全角转半角等)。

    Returns:
        后处理后的整篇文本: 段间 ``\\n\\n``, 末尾保留 ``\\n``。
    """
    if not pages:
        return ""

    # 1) 裁每页首尾空行 (trim_page_edge=True 时的等价处理).
    cleaned_pages: list[list[str]] = []
    for page_text in pages:
        if trim_page_edge:
            cleaned_pages.append(_trim_page_lines(page_text))
        else:
            cleaned_pages.append(page_text.split("\n"))

    # 2) 找跨页重复行 + 标记为 noise.
    if remove_repeated_page_noise:
        noise_lines = _find_repeated_lines(
            cleaned_pages,
            min_count=repeated_noise_min_count,
            max_length=repeated_noise_max_length,
        )
    else:
        noise_lines = set()

    # 3) 过滤 noise + 纯页码; 收集段间空行位置.
    filtered_pages: list[list[str]] = []
    for lines in cleaned_pages:
        new_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                # 空行: 标记为段间分隔, 下一非空行时与上一非空行段间分隔
                new_lines.append("")
                continue
            if _is_noise_line(stripped, noise_lines, drop_pure_page_number):
                continue
            new_lines.append(stripped)
        filtered_pages.append(new_lines)

    # 4) 合并视觉换行 (merge_visual_lines=True 时).
    if merge_visual_lines:
        merged_pages = [_merge_visual_lines(lines) for lines in filtered_pages]
    else:
        merged_pages = filtered_pages

    # 5) 段间 ``\n\n``, 行内 ``\n``.
    result = _join_pages(merged_pages)

    # 6) NFKC 归一化 (可选).
    if normalize_unicode:
        result = unicodedata.normalize("NFKC", result)

    return result


# ─────────────────────────────────────────────────────────────────────────────
# 内部实现
# ─────────────────────────────────────────────────────────────────────────────


def _trim_page_lines(page_text: str) -> list[str]:
    """裁掉每页首尾空行, 等价于 ``trim_page_edge=True`` 的坐标裁剪。"""
    lines = page_text.split("\n")
    # 跳过前导空行
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    # 跳过尾部空行
    end = len(lines)
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _find_repeated_lines(
    pages_lines: Iterable[list[str]],
    *,
    min_count: int,
    max_length: int,
) -> set[str]:
    """统计跨所有 page 出现 ≥ ``min_count`` 次, 长度 ≤ ``max_length`` 的行。"""
    counts: dict[str, int] = {}
    for lines in pages_lines:
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if len(stripped) > max_length:
                continue
            counts[stripped] = counts.get(stripped, 0) + 1
    return {line for line, n in counts.items() if n >= min_count}


def _is_noise_line(
    stripped: str,
    noise_lines: set[str],
    drop_pure_page_number: bool,
) -> bool:
    """判断单行是否为 noise (跨页重复行 / 纯页码)。"""
    if stripped in noise_lines:
        return True
    if drop_pure_page_number and _PURE_PAGE_NUMBER_RE.match(stripped):
        return True
    return False


def _merge_visual_lines(lines: list[str]) -> list[str]:
    """合并视觉换行: 段末标点 / bullet / 段间空行不合并, 其他接续。

    规则:
    - 空行: 保留作为段间分隔
    - bullet 行 (``-`` / ``*`` / ``N.``): 保留独立, 不与上一行合并
    - 上一行以段末标点结尾 (``。.!?``): 保留独立, 不与下一行合并
    - 其他: 后续行直接接续

    简化说明: 严格版用 ``\\n`` 拼接行, 简化版用 `` `` 拼接。
    差异在视觉; 后续 chunker 不依赖行内 ``\\n``, 仅依赖段间 ``\\n\\n``。
    """
    if not lines:
        return lines

    out: list[str] = []
    buf: str | None = None  # 当前累积行

    def _flush() -> None:
        nonlocal buf
        if buf is not None:
            out.append(buf)
            buf = None

    for line in lines:
        if not line:
            # 段间空行: 冲刷 buf, 加空行作为段分隔
            _flush()
            out.append("")
            continue

        if _BULLET_PREFIX_RE.match(line):
            # bullet 行: 冲刷 buf, 独立成行
            _flush()
            out.append(line)
            continue

        if buf is None:
            # 起始行
            buf = line
        elif _END_PUNCT_RE.search(buf):
            # 上一行以段末标点结尾: 不合并
            _flush()
            buf = line
        else:
            buf = f"{buf} {line}"

    _flush()
    return out


def _join_pages(pages_lines: Iterable[list[str]]) -> str:
    """多页 → 单字符串: 段间 ``\\n\\n``, 末尾保留 ``\\n``。

    处理流程:
    1. 把每页的 ``list[str]`` 压平成单字符串 (行间 ``\\n``)
    2. 页间用 ``\\n\\n`` 拼接 (整段空行作段分隔)
    3. 末尾追加 ``\\n``
    """
    page_strs: list[str] = []
    for lines in pages_lines:
        if not lines:
            continue
        page_strs.append("\n".join(lines))
    body = "\n\n".join(page_strs)
    if not body:
        return ""
    if not body.endswith("\n"):
        body += "\n"
    return body
