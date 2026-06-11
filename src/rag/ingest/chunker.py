"""文本分块: 17 级递归切分, 对齐 spec §15 / FastGPT textSplitter。"""

import re
from dataclasses import dataclass, field

# 软分隔符优先级: (分隔符, 是否启用), 从粗到细逐级尝试
DEFAULT_SOFT_SEPS: list[tuple[str, bool]] = [
    ("\n\n", True),
    ("\n", True),
    ("。", True),
    (". ", True),
    ("！", True),
    ("! ", True),
    ("？", True),
    ("? ", True),
    ("；", True),
    ("; ", True),
    ("，", True),
    (", ", True),
]

_HEADING_RE = re.compile(r"^(#{1,5})\s+(.+)$", re.MULTILINE)
_HEADING_SPLIT_RE = re.compile(r"^(#{1,5}\s+.+)$", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```|~~~[\s\S]*?~~~)")
_HTML_TABLE_RE = re.compile(r"<table>[\s\S]*?</table>", re.IGNORECASE)
_MD_TABLE_ROW_RE = re.compile(r"^\|.+\|$", re.MULTILINE)
_HEADING_LINE_RE = re.compile(r"^#{1,5}\s", re.MULTILINE)


@dataclass
class ChunkSettings:
    """分块算法参数; 由 ingest pipeline 组装, 不直接读 .env。"""

    chunk_size: int = 1000  # 目标 chunk 有效长度 (去空白后)
    max_chunk_size: int = 8000  # 单块硬上限, 超出则滑动窗口截断
    overlap_ratio: float = 0.15  # 相邻 chunk 重叠比例
    paragraph_chunk_deep: int = 5  # 段落递归深度上限 (预留)
    paragraph_chunk_min_size: int = 100  # 末段过短时合并回上一块
    min_chunk_size: int = 64  # 过小块合并阈值
    custom_separator: str | None = None  # 用户自定义正则硬切 (Step 0)
    soft_seps: list[tuple[str, bool]] = field(
        default_factory=lambda: list(DEFAULT_SOFT_SEPS)
    )


def _valid_len(text: str) -> int:
    """有效长度: 去掉空白与换行后的字符数。"""
    return len(re.sub(r"[\s\n]", "", text))


def _heading_level(tag: str) -> int:
    """从 'level:title' 标签解析标题层级。"""
    return int(tag.split(":", 1)[0])


def _push_heading_path(path: list[str], level: int, title: str) -> list[str]:
    """维护标题祖先链: 弹出同级及更深节点, 再压入当前标题。"""
    new_path = [tag for tag in path if _heading_level(tag) < level]
    new_path.append(f"{level}:{title}")
    return new_path


def _path_to_heading_prefix(path: list[str]) -> str:
    """把标题路径还原为 Markdown 前缀, 供子 chunk 继承上下文。"""
    lines: list[str] = []
    for tag in path:
        level = _heading_level(tag)
        title = tag.split(":", 1)[1]
        lines.append("#" * level + " " + title)
    return "\n".join(lines)


class Chunker:
    """17 级递归文本分块器。"""

    def __init__(self, settings: ChunkSettings) -> None:
        self.s = settings

    def split(self, text: str) -> list[str]:
        """入口: 按 plain / markdown 路径分块并做最终整理。"""
        if not text or not text.strip():
            return []

        if self.s.custom_separator:
            custom_chunks = self._split_custom(text)
            if custom_chunks is not None:
                return custom_chunks

        has_markdown = bool(_HEADING_RE.search(text))
        if not has_markdown:
            chunks = self._split_plain(text)
        else:
            chunks = self._split_markdown(text)

        return self._finalize(chunks)

    def _split_custom(self, text: str) -> list[str] | None:
        """Step 0: 用户自定义正则硬切 (plain / markdown 共用)。"""
        if self.s.custom_separator is None:
            return None
        pattern = re.compile(self.s.custom_separator)
        parts = [part.strip() for part in pattern.split(text) if part.strip()]
        if not parts:
            return []
        chunks: list[str] = []
        remaining: list[str] = []
        for part in parts:
            if _valid_len(part) > self.s.max_chunk_size:
                remaining.append(part)
            else:
                chunks.append(part)
        if remaining:
            for part in remaining:
                chunks.extend(self._enforce_max_size(self._soft_split(part)))
        return self._enforce_max_size(chunks)

    def _split_plain(self, text: str) -> list[str]:
        """纯文本 / 图片描述回退: 跳过标题步骤, 直接从软分隔符切分。"""
        return self._soft_split(text)

    def _split_markdown(self, text: str) -> list[str]:
        """Markdown 路径: 标题 → 代码块 → 表格 → 剩余正文软切。"""
        chunks: list[str] = []

        text, heading_chunks = self._step_headings(text)
        chunks.extend(heading_chunks)

        text, code_chunks = self._step_code_blocks(text)
        chunks.extend(code_chunks)

        text, table_chunks = self._step_html_tables(text)
        chunks.extend(table_chunks)

        text, md_table_chunks = self._step_md_tables(text)
        chunks.extend(md_table_chunks)

        if text.strip():
            chunks.extend(self._soft_split(text))

        return chunks

    def _step_headings(self, text: str) -> tuple[str, list[str]]:
        """按 Markdown 标题切 section, 子块继承 heading_path 前缀。"""
        sections = _HEADING_SPLIT_RE.split(text)
        if len(sections) <= 1:
            return text, []

        chunks: list[str] = []
        heading_path: list[str] = []
        current_body = ""

        for section in sections:
            if _HEADING_LINE_RE.match(section):
                if current_body.strip():
                    prefix = _path_to_heading_prefix(heading_path)
                    chunks.extend(self._split_section(prefix, current_body))
                match = _HEADING_RE.match(section)
                if match is not None:
                    level = len(match.group(1))
                    title = match.group(2).strip()
                    heading_path = _push_heading_path(heading_path, level, title)
                current_body = ""
            else:
                current_body += section

        if current_body.strip():
            prefix = _path_to_heading_prefix(heading_path)
            chunks.extend(self._split_section(prefix, current_body))

        return "", chunks

    def _split_section(self, heading: str, body: str) -> list[str]:
        """单个标题 section: 未超限则整块保留, 否则按标点再切并合并。"""
        combined = f"{heading}\n\n{body}" if heading else body
        if _valid_len(combined) <= self.s.chunk_size:
            return [combined]

        sub_pieces = self._split_by_seps(
            body, ["\n\n", "\n", "。", "？", "！", "；", "，"]
        )
        section_chunks: list[str] = []
        for piece in sub_pieces:
            if heading:
                piece = f"{heading}\n\n{piece}"
            section_chunks.append(piece)
        return self._merge_chunks(section_chunks)

    def _step_code_blocks(self, text: str) -> tuple[str, list[str]]:
        """提取 fenced code block 为独立 chunk, 避免内部被切碎。"""
        parts = _CODE_BLOCK_RE.split(text)
        remaining: list[str] = []
        code_chunks: list[str] = []
        for index, part in enumerate(parts):
            if index % 2 == 1:
                if _valid_len(part) <= self.s.max_chunk_size:
                    code_chunks.append(part)
                else:
                    if part.startswith("```"):
                        fence = part[:3]
                    elif part.startswith("~~~"):
                        fence = part[:3]
                    else:
                        fence = "```"
                    lines = part.split("\n")
                    inner = "\n".join(lines[1:-1]) if len(lines) > 2 else part
                    truncated = inner[: self.s.max_chunk_size - 10]
                    code_chunks.append(f"{fence}\n{truncated}\n{fence}")
            else:
                remaining.append(part)
        return "\n".join(remaining), code_chunks

    def _step_html_tables(self, text: str) -> tuple[str, list[str]]:
        """HTML <table> 整表保留或按 max 截断。"""
        parts = _HTML_TABLE_RE.split(text)
        remaining: list[str] = []
        table_chunks: list[str] = []
        for index, part in enumerate(parts):
            if index % 2 == 1:
                if _valid_len(part) <= self.s.chunk_size:
                    table_chunks.append(part)
                else:
                    table_chunks.append(part[: self.s.max_chunk_size])
            else:
                remaining.append(part)
        return "\n".join(remaining), table_chunks

    def _step_md_tables(self, text: str) -> tuple[str, list[str]]:
        """Markdown 管道表格: 按行分组, 每块重复表头。"""
        lines = text.split("\n")
        header_row = ""
        sep_row = ""
        data_rows: list[str] = []
        in_table = False
        non_table_lines: list[str] = []
        table_chunks: list[str] = []

        for line in lines:
            if _MD_TABLE_ROW_RE.match(line):
                if header_row and sep_row:
                    data_rows.append(line)
                elif "---" in line and header_row:
                    sep_row = line
                elif "|" in line:
                    header_row = line
                in_table = True
            else:
                if in_table and header_row and data_rows:
                    table_chunks.extend(
                        self._chunk_table(header_row, sep_row, data_rows)
                    )
                    header_row, sep_row, data_rows = "", "", []
                in_table = False
                non_table_lines.append(line)

        if header_row and data_rows:
            table_chunks.extend(self._chunk_table(header_row, sep_row, data_rows))

        return "\n".join(non_table_lines), table_chunks

    def _chunk_table(self, header: str, sep: str, rows: list[str]) -> list[str]:
        """表格分块: 超 chunk_size * 1.2 时新开一块并复制表头。"""
        chunks: list[str] = []
        buf = [header, sep]
        buf_len = len(header) + len(sep)
        for row in rows:
            if buf_len + len(row) > int(self.s.chunk_size * 1.2) and len(buf) > 2:
                chunks.append("\n".join(buf))
                buf = [header, sep, row]
                buf_len = len(header) + len(sep) + len(row)
            else:
                buf.append(row)
                buf_len += len(row)
        if len(buf) > 2:
            chunks.append("\n".join(buf))
        return chunks

    def _split_by_seps(self, text: str, seps: list[str]) -> list[str]:
        """按给定分隔符列表顺序递归切分。"""
        pieces = [text]
        for sep in seps:
            new_pieces: list[str] = []
            for piece in pieces:
                new_pieces.extend(piece.split(sep))
            pieces = new_pieces
        return [piece.strip() for piece in pieces if piece.strip()]

    def _soft_split(self, text: str) -> list[str]:
        """软切: 先按空行分段, 超长段再走 soft_seps。"""
        if "\n\n" in text:
            paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
            if len(paragraphs) > 1:
                result: list[str] = []
                for paragraph in paragraphs:
                    if _valid_len(paragraph) > self.s.chunk_size:
                        result.extend(self._soft_split_seps(paragraph))
                    else:
                        result.append(paragraph)
                return self._enforce_max_size(result)
        return self._enforce_max_size(self._soft_split_seps(text))

    def _soft_split_seps(self, text: str) -> list[str]:
        """按 soft_seps 优先级逐级切分, 最后带 overlap 合并。"""
        pieces = [text]
        for sep, _enabled in self.s.soft_seps:
            new_pieces: list[str] = []
            for piece in pieces:
                if sep == "\n\n":
                    if "\n\n" in piece:
                        new_pieces.extend(
                            part.strip() for part in piece.split("\n\n") if part.strip()
                        )
                    else:
                        new_pieces.append(piece)
                elif _valid_len(piece) <= self.s.chunk_size:
                    new_pieces.append(piece)
                else:
                    parts = piece.split(sep)
                    new_pieces.extend(parts)
            pieces = new_pieces
        return self._merge_with_overlap(pieces)

    def _merge_chunks(self, pieces: list[str]) -> list[str]:
        """合并相邻片段至 chunk_size, 无 overlap。"""
        result: list[str] = []
        buf = ""
        for piece in pieces:
            merged = piece if not buf else f"{buf}\n{piece}"
            if _valid_len(merged) <= self.s.chunk_size:
                buf = merged
            else:
                if buf:
                    result.append(buf)
                buf = piece
        if buf:
            result.append(buf)
        return self._merge_small_chunks(result)

    def _merge_with_overlap(self, pieces: list[str]) -> list[str]:
        """合并片段并在块边界保留 overlap_ratio 重叠。"""
        result: list[str] = []
        buf = ""
        for piece in pieces:
            merged = piece if not buf else f"{buf}\n{piece}"
            if _valid_len(merged) <= self.s.chunk_size:
                buf = merged
            else:
                if buf:
                    result.append(buf)
                if buf and self.s.overlap_ratio > 0:
                    overlap_len = int(len(buf) * self.s.overlap_ratio)
                    buf = f"{buf[-overlap_len:]}\n{piece}" if overlap_len > 0 else piece
                else:
                    buf = piece
        if buf:
            result.append(buf)

        if (
            len(result) >= 2
            and _valid_len(result[-1]) < self.s.paragraph_chunk_min_size
        ):
            last = result.pop()
            result[-1] = f"{result[-1]}\n{last}"

        return self._merge_small_chunks(result)

    def _merge_small_chunks(self, chunks: list[str]) -> list[str]:
        """把小于 min_chunk_size 的块并入相邻块 (首尾特殊处理)。"""
        if not chunks:
            return chunks
        if len(chunks) >= 2 and all(
            _valid_len(chunk) < self.s.min_chunk_size for chunk in chunks
        ):
            return chunks
        result = list(chunks)
        index = 0
        while index < len(result):
            if _valid_len(result[index]) < self.s.min_chunk_size and index + 1 < len(
                result
            ):
                result[index + 1] = f"{result[index]}\n{result[index + 1]}"
                result.pop(index)
                continue
            if _valid_len(result[index]) < self.s.min_chunk_size and index > 0:
                result[index - 1] = f"{result[index - 1]}\n{result[index]}"
                result.pop(index)
                continue
            index += 1
        return result

    def _enforce_max_size(self, chunks: list[str]) -> list[str]:
        """确保每块不超过 max_chunk_size, 超出则滑动窗口。"""
        result: list[str] = []
        for chunk in chunks:
            if len(chunk) <= self.s.max_chunk_size:
                result.append(chunk)
            else:
                result.extend(self._sliding_window(chunk))
        return result

    def _sliding_window(self, text: str) -> list[str]:
        """Step 16: 字符级滑动窗口兜底切分。"""
        if len(text) <= self.s.max_chunk_size:
            return [text]
        step = max(
            1,
            int(self.s.max_chunk_size * (1 - self.s.overlap_ratio)),
        )
        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self.s.max_chunk_size, len(text))
            chunks.append(text[start:end])
            if end >= len(text):
                break
            start += step
        return chunks

    def _finalize(self, chunks: list[str]) -> list[str]:
        """收尾: 去空块、合并小块、再次 enforce max。"""
        cleaned = [chunk for chunk in chunks if chunk.strip()]
        return self._enforce_max_size(self._merge_small_chunks(cleaned))
