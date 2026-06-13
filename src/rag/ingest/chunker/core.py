"""Chunker 入口: split(text, ctx) -> list[Chunk] (新主签名) + split_str() 兼容旧 API。

实现策略: 12 级 Rule 递归 + 实用入口 (新设计, 从 17 级收敛)

入口流程:
  1. 文本规范化 (simple_text) + 代码块保护 (protect_code_block)
  2. 自定义 separator 优先切
  3. 顶层 CUSTOM_SPLIT_SIGN 切
  4. 每个段: 预切 \\n\\n / 中英标点, 再调用 common_split 处理超长段
  5. finalize: 还原代码块 marker + merge_small + enforce_max
  6. 包 Chunk: chunk_index / total_chunks / valid_len + 注入 ctx (doc-level) +
     per-chunk 重算 heading_stack/has_code/has_table/image_refs
"""

from __future__ import annotations

import logging
import re

# TODO: if missing, add tqdm to pyproject.toml dependencies.
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

# 中文字符之间 ASCII 标点两侧插入空格, 让标点规则的 `[!?;,] ` 后缀命中
_ASCII_PUNCT_BETWEEN_CN_RE = re.compile(r"([一-鿿])([!?,;.])([一-鿿])")

# 顶层预切分隔符: \\n\\n / 中英标点 (在中文上下文中)
# 注意 \\n\\n 必须在 \\n 之前 (避免被单换行切掉双换行)
_PRESPLIT_RE = re.compile(r"(\n\n|。|！|？|；|，|[!?,;.])(?=\S)")

# per-chunk 检测正则
# 代码 fence (无论是否完整跨 chunk) + <pre><code> 块
_CODE_FENCE_RE = re.compile(r"```|~~~")
_HTML_PRE_CODE_RE = re.compile(r"<pre\b[\s\S]*?<code\b", re.IGNORECASE)
_TABLE_RE = re.compile(r"(?:^\|.+\|$\n?)+", re.MULTILINE)
_HTML_TABLE_RE = re.compile(r"<table\b[\s\S]*?</table>", re.IGNORECASE)
# Markdown 标题 (1-5 级) + 顺序; 用于 per-chunk heading stack 重算
_MD_HEADING_RE = re.compile(r"^(#{1,5})\s+(.+)$", re.MULTILINE)
# HTML 标题 (h1-h6)
_HTML_HEADING_RE = re.compile(r"<h([1-6])>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
# 通用图片引用: ![alt](url) 或 HTML <img src=...>
_IMAGE_REF_RE = re.compile(
    r"!\[[^\]]*\]\([^)]+\)|<img\b[^>]*src=[\"']([^\"']+)[\"']", re.IGNORECASE
)


def _normalize_ascii_punct(text: str) -> str:
    """中文字符间的 ASCII 标点两侧加空格, 便于 punct_merged 规则匹配。"""
    return _ASCII_PUNCT_BETWEEN_CN_RE.sub(r"\1\2 \3", text)


def _pre_split(text: str) -> list[str]:
    """顶层预切: \\n\\n / 中英文标点, 保留切点作为独立 segment。"""
    if not text.strip():
        return []
    parts = _PRESPLIT_RE.split(text)
    return [p for p in parts if p and p.strip()]


def _heading_stack_for_chunk(chunk_text: str) -> list[str]:
    """per-chunk heading 栈重建: 仅解析 chunk 文本自身内出现的 headings。

    思路: heading_stack 反映"该 chunk 当前所在的 heading 上下文"。
    - 解析 chunk 内出现的所有 heading (按行/位置顺序)
    - 维护 stack: 新 heading.level > stack[-1].level → push, 否则 pop until < level
    - 返回 stack 的 ['# Title', ...] 序列

    此实现不依赖 doc-level structure, 避免 ChunkContext 字段变更。
    """
    if not chunk_text or not chunk_text.strip():
        return []

    # 按 (offset, level, title) 顺序收集所有 heading
    hits: list[tuple[int, int, str]] = []
    for m in _MD_HEADING_RE.finditer(chunk_text):
        level = len(m.group(1))
        title = m.group(2).strip()
        if title:
            hits.append((m.start(), level, title))
    for m in _HTML_HEADING_RE.finditer(chunk_text):
        level = int(m.group(1))
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        if title:
            hits.append((m.start(), level, title))

    hits.sort(key=lambda x: x[0])

    # 维护 stack (类似 MarkdownStructureExtractor._nest 的逻辑)
    stack: list[tuple[int, str]] = []
    for _offset, level, title in hits:
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
    return [f"{'#' * lv} {t}" for lv, t in stack]


def _has_code_in(text: str) -> bool:
    """per-chunk 代码块检测。

    策略:
      1. 任何 ``` 或 ~~~ fence 出现 (即便跨 chunk 也算) → True
      2. HTML <pre><code> 块 → True
    """
    if _CODE_FENCE_RE.search(text):
        return True
    if _HTML_PRE_CODE_RE.search(text):
        return True
    return False


def _has_table_in(text: str) -> bool:
    """per-chunk 表格检测: Markdown pipe 表或 HTML <table>。"""
    if str_is_md_table(text):
        return True
    if _TABLE_RE.search(text):
        return True
    if _HTML_TABLE_RE.search(text):
        return True
    return False


def _image_refs_in(text: str) -> list[str]:
    """per-chunk 图片引用抽取: 去重, 保持首次出现顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for m in _IMAGE_REF_RE.finditer(text):
        # Markdown 形式: 整段 m.group(0); HTML 形式: src 在 group(1)
        url = m.group(1) if m.group(1) else m.group(0)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


class Chunker:
    def __init__(self, settings: ChunkSettings) -> None:
        self.s = settings

    # ── NEW 主签名: 返回 list[Chunk] (含元数据) ─────────────────
    def split(
        self,
        text: str,
        *,
        ctx: ChunkContext | None = None,
        format_text: str | None = None,
        get_format_text: bool = True,
    ) -> list[Chunk]:
        """主入口: text + 上下文 -> list[Chunk]。

        Args:
            text: 原始文本 (已过 Normalizer)
            ctx:  可选, 携带 DocMeta 信息, 注入到每块 metadata
            format_text: 可选, csv/xlsx adapter 提供的 markdown table 视图。
                若提供, 会与 raw_text 并行切 (用同一 chunker 规则), 然后
                按 chunk_index 对齐回填到 ``Chunk.raw_text`` / ``Chunk.format_text``。
            get_format_text: 决定 ``Chunk.text`` 对外文本来源:
                - True (默认): ``chunk.format_text or chunk.raw_text``
                - False:      ``chunk.raw_text`` 永远

        ``heading_path`` (doc-level DFS) 已删除; ``heading_stack`` / ``has_code``
        / ``has_table`` / ``image_refs`` 在 split 循环里 per-chunk 重算。

        ``format_text`` + ``get_format_text`` 透传, 同一 chunker 同时对
        raw_text / format_text 跑, 按 index 对齐。
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

        # format_text 与 raw_text 用同一规则并行切, 按 chunk_index 对齐。
        # 两者 chunk 数通常一致; 若不一致, 取较大者, 缺失侧填空串。
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
                        # 位置
                        chunk_index=i,
                        total_chunks=n,
                        valid_len=valid_len(restored),
                        # 来自 DocMeta (doc-level)
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

    # ── 旧 API 兼容: 返回 list[str] ─────────────────────────────
    def split_str(self, text: str) -> list[str]:
        """旧 API 等价, 内部委托 _split_legacy。供旧测试 / 旧调用方使用。"""
        return self._split_legacy(text)

    # ── 私有: 原 split 实现 (返回 list[str]) ─────────────────────
    def _split_legacy(self, text: str) -> list[str]:
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
        if not self.s.custom_separator:
            return None
        pattern = re.compile(self.s.custom_separator)
        parts = [p.strip() for p in pattern.split(text) if p.strip()]
        if not parts:
            return []
        return parts

    def _split_segment(self, seg: str) -> list[str]:
        seg = _normalize_ascii_punct(seg)
        # 短段跳过标点 pre_split, 直接走递归规则 (避免全文被逗号切碎)。
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
