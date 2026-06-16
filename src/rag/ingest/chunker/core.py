"""Chunker 主入口: 文本规范化 -> 规则切分 -> 收尾合并 -> 组装 `Chunk`。

旧 API `split_str` 仍保留以兼容旧调用方。
"""

from __future__ import annotations

import logging
import re

# TODO: 若缺失, 将 tqdm 加入 pyproject.toml 依赖。
from rag.ingest.chunker.types import ChunkContext
from rag.ingest.chunker.utils import valid_len
from rag.ingest.types import Chunk, ChunkMetadata

from .code_block import protect_code_block
from .finalize import enforce_max_size, merge_chunks_to_target, merge_small_chunks
from .recursive import common_split
from .rules import CUSTOM_SPLIT_SIGN, build_steps
from .settings import ChunkSettings
from .table import str_is_md_table
from .utils import restore_code_block_marker, simple_text

logger = logging.getLogger(__name__)

# 中文字符之间的 ASCII 标点两侧插入空格, 让标点规则的后缀空格能命中。
_ASCII_PUNCT_BETWEEN_CN_RE = re.compile(r"([一-鿿])([!?,;.])([一-鿿])")

# 顶层预切分隔符: 双换行或中英标点。
# 双换行必须排在前面, 否则会被单换行规则先吃掉。
_PRESPLIT_RE = re.compile(r"(\n\n|。|！|？|；|，|[!?,;.])(?=\S)")

# per-chunk 检测正则
# 代码 fence (是否完整跨 chunk 不影响) 或 HTML <pre><code>。
_CODE_FENCE_RE = re.compile(r"```|~~~")
_HTML_PRE_CODE_RE = re.compile(r"<pre\b[\s\S]*?<code\b", re.IGNORECASE)
_TABLE_RE = re.compile(r"(?:^\|.+\|$\n?)+", re.MULTILINE)
_HTML_TABLE_RE = re.compile(r"<table\b[\s\S]*?</table>", re.IGNORECASE)
# 标题正则已移至 `_heading_re.py`
# Markdown 与 HTML 形式的图片引用。
_IMAGE_REF_RE = re.compile(
    r"!\[[^\]]*\]\([^)]+\)|<img\b[^>]*src=[\"']([^\"']+)[\"']", re.IGNORECASE
)


def _normalize_ascii_punct(text: str) -> str:
    """在中文之间的 ASCII 标点两侧补空格, 便于切分规则匹配。

    Args:
        text: 原始文本。

    Returns:
        标点两侧已加空格的文本。
    """
    return _ASCII_PUNCT_BETWEEN_CN_RE.sub(r"\1\2 \3", text)


def _pre_split(text: str) -> list[str]:
    """顶层预切: 按双换行或中英标点切分, 切点保留为独立 segment。

    Args:
        text: 待切分文本。

    Returns:
        非空 segment 列表; 空文本返回空列表。
    """
    if not text.strip():
        return []
    parts = _PRESPLIT_RE.split(text)
    return [p for p in parts if p and p.strip()]


def _heading_stack_for_chunk(chunk_text: str) -> list[str]:
    """从 chunk 文本自身重建 heading 栈, 不依赖 doc-level 结构。

    Args:
        chunk_text: 单个 chunk 的文本。

    Returns:
        `['# Title', ...]` 形式的 heading 序列。
    """
    if not chunk_text or not chunk_text.strip():
        return []

    from rag.ingest.chunker._heading_re import collect_headings  # noqa: PLC0415

    hits = collect_headings(chunk_text)
    stack: list[tuple[int, str]] = []
    for _offset, level, title in hits:
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    return [f"{'#' * lv} {t}" for lv, t in stack]


def _has_code_in(text: str) -> bool:
    """判断文本是否含代码 fence 或 HTML `<pre><code>` 块。

    Args:
        text: 待检测文本。

    Returns:
        包含代码即返回 True。
    """
    if _CODE_FENCE_RE.search(text):
        return True
    if _HTML_PRE_CODE_RE.search(text):
        return True
    return False


def _has_table_in(text: str) -> bool:
    """判断文本是否含 Markdown pipe 表或 HTML `<table>`。

    Args:
        text: 待检测文本。

    Returns:
        包含表格即返回 True。
    """
    if str_is_md_table(text):
        return True
    if _TABLE_RE.search(text):
        return True
    if _HTML_TABLE_RE.search(text):
        return True
    return False


def _image_refs_in(text: str) -> list[str]:
    """抽取文本中的图片引用 URL, 去重并保留首次出现顺序。

    Args:
        text: 待检测文本。

    Returns:
        URL 列表。
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _IMAGE_REF_RE.finditer(text):
        # Markdown 形式: 整段 m.group(0); HTML 形式: src 在 group(1)。
        url = m.group(1) if m.group(1) else m.group(0)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


class Chunker:
    """按配置执行规范化、切分、合并并产出 `Chunk` 列表。"""

    def __init__(self, settings: ChunkSettings) -> None:
        """
        Args:
            settings: 切分配置 (chunk 大小、重叠比例、paragraph 深度等)。
        """
        self.s = settings

    def split(
        self,
        text: str,
        *,
        ctx: ChunkContext | None = None,
        format_text: str | None = None,
        get_format_text: bool = True,
    ) -> list[Chunk]:
        """主入口: 文本 + 上下文 -> `Chunk` 列表。

        `format_text` (如有) 与 `text` 用同一规则并行切分, 按 `chunk_index` 对齐
        回填到 `Chunk.raw_text` / `Chunk.format_text`; `get_format_text` 控制
        `Chunk.text` 优先取 `format_text` 还是 `raw_text`。

        Args:
            text: 已过 Normalizer 的原始文本。
            ctx: 可选 `ChunkContext`, 用于注入 doc-level 字段到 metadata。
            format_text: 可选, csv/xlsx adapter 提供的 markdown table 视图。
            get_format_text: `True` (默认) 时 `Chunk.text` 取 `format_text` 优先,
                `False` 时始终取 `raw_text`。

        Returns:
            切分得到的 `Chunk` 列表, 含位置、doc-level 与 per-chunk 现场重算
            的 heading/code/table/image 标记。
        """
        if not text or not text.strip():
            return []

        logger.info(
            "chunking.start text_len=%d has_format=%s get_format=%s",
            len(text),
            bool(format_text),
            get_format_text,
        )
        raw = self._split_legacy(text)
        if not raw:
            return []

        # format_text 与 raw_text 用同一规则并行切, 按 chunk_index 对齐;
        # 数量不一致时取较大者, 缺失侧填空串。
        fmt: list[str] = []
        if format_text:
            fmt = self._split_legacy(format_text)

        context = ctx or ChunkContext.empty()
        n = max(len(raw), len(fmt))
        out: list[Chunk] = []
        for i in range(n):
            raw_seg = raw[i] if i < len(raw) else ""
            fmt_seg = fmt[i] if i < len(fmt) else None
            restored = restore_code_block_marker(raw_seg)
            text_out = (
                (fmt_seg if (fmt_seg is not None and fmt_seg) else restored)
                if get_format_text
                else restored
            )
            out.append(
                Chunk(
                    text=text_out,
                    raw_text=restored,
                    format_text=fmt_seg,
                    metadata=ChunkMetadata(
                        # 位置信息
                        chunk_index=i,
                        total_chunks=n,
                        valid_len=valid_len(restored),
                        # doc-level 字段
                        source=context.source,
                        file_type=context.file_type,
                        page_count=context.page_count,
                        encoding=context.encoding,
                        # per-chunk 现场重算
                        heading_stack=_heading_stack_for_chunk(restored),
                        has_code=_has_code_in(restored),
                        has_table=_has_table_in(restored),
                        image_refs=_image_refs_in(restored),
                    ),
                )
            )
        logger.info("chunking.done chunks=%d", len(out))
        return out

    def split_str(self, text: str) -> list[str]:
        """兼容旧 API: 返回纯字符串 chunk 列表, 内部委托 `_split_legacy`。

        Args:
            text: 待切分文本。

        Returns:
            chunk 字符串列表。
        """
        return self._split_legacy(text)

    def _split_legacy(self, text: str) -> list[str]:
        """原 `split` 实现, 返回 `list[str]`, 供 `split` / `split_str` 复用。

        流程: 规范化 -> 代码块保护 -> 自定义 separator 优先 -> 按
        `CUSTOM_SPLIT_SIGN` 切段 -> `_finalize` 合并收尾。
        """
        if not text or not text.strip():
            return []

        text = simple_text(text)
        text = protect_code_block(text)

        if self.s.custom_separator:
            custom_chunks = self._split_custom(text)
            if custom_chunks is not None:
                return self._finalize(custom_chunks)

        segments = text.split(CUSTOM_SPLIT_SIGN)
        all_chunks: list[str] = []
        for seg in segments:
            if not seg.strip():
                continue
            all_chunks.extend(self._split_segment(seg))

        return self._finalize(all_chunks)

    def _split_custom(self, text: str) -> list[str] | None:
        """按 `custom_separator` 切分; 未配置时返回 None。"""
        if not self.s.custom_separator:
            return None
        pattern = re.compile(self.s.custom_separator)
        parts = [p.strip() for p in pattern.split(text) if p.strip()]
        if not parts:
            return []
        return parts

    def _split_segment(self, seg: str) -> list[str]:
        """对单段执行顶层预切 + 递归切分, 短段跳过预切避免被切碎。"""
        seg = _normalize_ascii_punct(seg)
        # 短段不预切标点, 避免整段被切成单句。
        if valid_len(seg) <= self.s.chunk_size:
            return self._split_long(seg)
        parts = _pre_split(seg)
        if len(parts) <= 1:
            return self._split_long(seg)

        rules = build_steps(
            chunk_size=self.s.chunk_size,
            max_size=self.s.max_chunk_size,
            paragraph_chunk_deep=self.s.paragraph_chunk_deep,
        )
        overlap_len = int(self.s.chunk_size * self.s.overlap_ratio)

        result: list[str] = []
        for p in parts:
            if valid_len(p) > self.s.chunk_size:
                result.extend(
                    common_split(
                        text=p,
                        step=0,
                        last_text="",
                        parent_title="",
                        rules=rules,
                        chunk_size=self.s.chunk_size,
                        max_size=self.s.max_chunk_size,
                        overlap_len=overlap_len,
                        paragraph_chunk_min_size=self.s.paragraph_chunk_min_size,
                    )
                )
            else:
                result.append(p)
        return result

    def _split_long(self, seg: str) -> list[str]:
        """对超长段直接进入递归切分。"""
        rules = build_steps(
            chunk_size=self.s.chunk_size,
            max_size=self.s.max_chunk_size,
            paragraph_chunk_deep=self.s.paragraph_chunk_deep,
        )
        overlap_len = int(self.s.chunk_size * self.s.overlap_ratio)

        return common_split(
            text=seg,
            step=0,
            last_text="",
            parent_title="",
            rules=rules,
            chunk_size=self.s.chunk_size,
            max_size=self.s.max_chunk_size,
            overlap_len=overlap_len,
            paragraph_chunk_min_size=self.s.paragraph_chunk_min_size,
        )

    def _finalize(self, chunks: list[str]) -> list[str]:
        """收尾: 还原代码块 marker, 合并小块, 强制不超 `max_size`。"""
        chunks = [restore_code_block_marker(c) for c in chunks]
        chunks = [c for c in chunks if c.strip()]
        small = [c for c in chunks if len(c) < self.s.min_chunk_size]
        if 0 < len(small) <= 3:
            return [c.strip() for c in chunks if c.strip()]
        chunks = merge_small_chunks(chunks, self.s.min_chunk_size)
        chunks = merge_chunks_to_target(
            chunks,
            self.s.chunk_size,
            self.s.max_chunk_size,
        )
        chunks = enforce_max_size(chunks, self.s.max_chunk_size, self.s.overlap_ratio)
        return [c.strip() for c in chunks if c.strip()]
