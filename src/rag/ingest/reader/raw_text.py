"""raw text 读取 + 编码兜底 + markdown base64 图抽取。

设计:
 - 不抛编码异常: 任何编码失败都退到 ``buffer.decode('utf-8', errors='replace')``。
 - 图上传失败也走 fallback: 上传函数异常时把 data URL 整段删除。
 - `UploadFileHandler` 是 `Awaitable` 形态, 本模块所有 IO 都走 `async`。
"""

from __future__ import annotations

import base64
import codecs
import logging
import re

from rag.ingest.reader.types import (
    UploadedFileResult as UploadedFileResult,
)
from rag.ingest.reader.types import (
    UploadFileHandler,
)

logger = logging.getLogger(__name__)

# UploadFileHandler / UploadedFileResult 在 types.py 定义, 本模块 re-export 保持
# 向后兼容 (历史 test_extensions_text / test_extensions_html 仍然
# ``from rag.ingest.reader.raw_text import UploadedFileResult``).

# ─────────────────────────────────────────────────────────────────────────────
# 编码白名单 + 兜底
# ─────────────────────────────────────────────────────────────────────────────

# ``bytes.decode()`` 对每个名字都能 work, 但保留 ``utf16le`` / ``ucs2`` / ``base64``
# / ``hex`` 等非文本编码以便和上游对齐.
RAW_ENCODING_LIST: frozenset[str] = frozenset(
    {
        "ascii",
        "utf8",
        "utf-8",
        "utf16le",
        "utf-16le",
        "ucs2",
        "ucs-2",
        "base64",
        "base64url",
        "latin1",
        "binary",
        "hex",
    }
)


def _has_non_ascii_byte(buffer: bytes) -> bool:
    """buffer 含 > 0x7F 字节时返回 True, 用于 ascii 编码时的降级判定。"""
    for b in buffer:
        if b > 0x7F:
            return True
    return False


def _normalize_encoding_name(encoding: str) -> str:
    enc = (encoding or "").strip().lower().replace("_", "-")
    if enc == "utf8":
        return "utf-8"
    return enc


# 默认 utf-8 解码失败时按序 trial decode (中文 windows 日志常见 gbk/gb18030).
_DETECT_FALLBACK_ENCODINGS: tuple[str, ...] = (
    "gb18030",
    "gbk",
    "big5",
    "cp936",
    "cp1252",
    "latin-1",
)


def detect_text_encoding(buffer: bytes) -> str:
    """无第三方依赖的文本编码探测: BOM → strict utf-8 → 常见 locale 编码 trial。

    Args:
        buffer: 输入字节内容。

    Returns:
        推断出的编码名。
    """
    if not buffer:
        return "utf-8"

    if buffer.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if buffer.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if buffer.startswith(b"\xff\xfe"):
        return "utf-16-le"

    try:
        buffer.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    for enc in _DETECT_FALLBACK_ENCODINGS:
        try:
            buffer.decode(enc)
            return enc
        except (UnicodeDecodeError, LookupError):
            continue

    return "utf-8"


def resolve_text_encoding(buffer: bytes, encoding: str = "utf-8") -> str:
    """解析最终解码编码: 仅当请求为默认 utf-8 时自动探测, 显式指定则尊重调用方。

    Args:
        buffer: 输入字节内容。
        encoding: 调用方指定的编码。

    Returns:
        最终使用的编码名。
    """
    normalized = _normalize_encoding_name(encoding)
    if normalized in ("utf-8", ""):
        detected = detect_text_encoding(buffer)
        if detected != "utf-8":
            logger.info("raw_text detected encoding=%s", detected)
            return detected
    return normalized


def _decode_buffer(buffer: bytes, encoding: str) -> str:
    """按 ``encoding`` 解码, 任何异常都退到 ``utf-8`` + ``errors='replace'``。

    优先级:
    1. 白名单内: ``buffer.decode(encoding)``。ascii 遇非 ASCII 字节降级 utf-8。
    2. 其它编码: 通过 ``codecs.decode`` 走 stdlib codec (gbk / big5 / ...)。
    3. 全部失败: ``buffer.decode('utf-8', errors='replace')``。
    """
    normalized = _normalize_encoding_name(encoding)

    try:
        if normalized in RAW_ENCODING_LIST:
            # ascii 仅 0x00~0x7F, 含中文等非 ASCII 字节时降级 utf-8 避免乱码
            if normalized == "ascii" and _has_non_ascii_byte(buffer):
                return buffer.decode("utf-8")
            return buffer.decode(normalized)

        if normalized:
            # ``codecs.decode`` 返回 str (与 ``Codec.decode`` 返回 tuple 不同),
            # 是 Python 推荐的非内置编码入口 (gbk / big5 / shift_jis / gb18030 ...).
            return codecs.decode(buffer, normalized)

        # encoding 为空 → utf-8 兜底
        return buffer.decode("utf-8")
    except (UnicodeDecodeError, LookupError, ValueError) as e:
        detected = detect_text_encoding(buffer)
        if detected != normalized:
            logger.warning(
                "raw_text decode failed (encoding=%s), retry with %s: %s",
                encoding,
                detected,
                e,
            )
            try:
                return _decode_buffer(buffer, detected)
            except (UnicodeDecodeError, LookupError, ValueError):
                pass
        logger.warning("raw_text decode failed (encoding=%s): %s", encoding, e)
        return buffer.decode("utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────────────────
# Markdown base64 图片抽取
# ─────────────────────────────────────────────────────────────────────────────

# 形如 ``![alt](data:image/png;base64,iVBORw0KGgo...)`` 的整段 markdown
# base64 图. 匹配范围: 整段 ``![...](data:...)`` 而不仅是 data URL,
# 这样替换 / 删除时不会留下 ``![alt]()`` 空壳.
# - alt 段允许空 (常见于 OCR 自动生成图)
# - mime 子段 ``[^;\"\\s]+`` (不跨 ;/空格/引号)
# - base64 段标准字符集 ``[A-Za-z0-9+/=]+``
_MD_BASE64_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(data:image/[^;\"\\s]+;base64,([A-Za-z0-9+/=]+)\)"
)


class _Base64ImageMatch:
    """内部辅助: 一次匹配产出一个 base64 图位置 + 完整 markdown 片段。"""

    __slots__ = ("full_match", "mime", "base64", "start", "end")

    def __init__(
        self, full_match: str, mime: str, base64_str: str, start: int, end: int
    ) -> None:
        self.full_match = full_match
        self.mime = mime
        self.base64 = base64_str
        self.start = start
        self.end = end


def _scan_base64_images(content: str) -> list[_Base64ImageMatch]:
    """扫描 content 中所有 ``![...](data:image/...;base64,...)`` 整段图。"""
    results: list[_Base64ImageMatch] = []
    for m in _MD_BASE64_IMAGE_RE.finditer(content):
        full = m.group(0)
        # 从 ``data:image/XXX;base64,`` 头里取 mime 子段
        header = full.split(";", 1)[0]  # ``data:image/png``
        mime = header[len("data:") :] if header.startswith("data:") else "image/png"
        results.append(
            _Base64ImageMatch(
                full_match=full,
                mime=mime,
                base64_str=m.group(1),
                start=m.start(),
                end=m.end(),
            )
        )
    return results


async def _upload_one(
    match: _Base64ImageMatch,
    upload_file: UploadFileHandler,
    idx: int,
) -> str:
    """调上传回调, 返回对象存储 key。失败时返回 ``""`` (整段 data URL 被删)。"""
    name = f"md_base64_{idx}.{match.mime.rsplit('/', 1)[-1]}"
    try:
        image_bytes = base64.b64decode(match.base64, validate=False)
    except (ValueError, TypeError) as e:
        logger.warning("base64 decode failed (mime=%s): %s", match.mime, e)
        return ""
    try:
        result = await upload_file(name, match.mime, image_bytes)
    except Exception as e:  # noqa: BLE001 — 上传回调异常吞掉, 视作删除
        logger.warning("uploadFile raised (mime=%s): %s", match.mime, e)
        return ""
    key = result.get("key", "") if isinstance(result, dict) else ""
    return key


async def _parse_markdown_base64_images(
    content: str,
    upload_file: UploadFileHandler | None,
) -> str:
    """替换 content 中的 base64 data URL → 上传后的 key, 无上传器时整段删除。

    替换语义:
    - controller 返回 ``{key: "..."}`` → 用 ``![...](key)`` 替换整段
    - controller 返回 ``{key: ""}`` 或抛错 → 删除整段 (留空字符串)
    """
    matches = _scan_base64_images(content)
    if not matches:
        return content

    parts: list[str] = []
    cursor = 0
    for idx, m in enumerate(matches):
        parts.append(content[cursor : m.start])
        if upload_file is None:
            # 无上传器: 删除整段 (data URL 体积大, 不应继续流转)
            replacement = ""
        else:
            key = await _upload_one(m, upload_file, idx)
            replacement = f"![]({key})" if key else ""
        parts.append(replacement)
        cursor = m.end
    parts.append(content[cursor:])
    return "".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 公共入口
# ─────────────────────────────────────────────────────────────────────────────


async def read_raw_text(
    buffer: bytes,
    encoding: str = "utf-8",
    upload_file: UploadFileHandler | None = None,
) -> str:
    """bytes → 解码文本 (+ 可选 base64 图抽取)。

    Args:
        buffer: 文件二进制内容。
        encoding: 文本编码 (大小写不敏感)。空字符串 / 未知编码 / 解码失败
            都退到 ``utf-8`` + ``errors='replace'``。
        upload_file: 可选, 异步上传回调。markdown 中的
            ``data:image/...;base64,...`` 会被解码 + 上传 + 替换为 key;
            上传失败 / 未传 upload_file → 删除整段 data URL。

    Returns:
        解码后的文本; 任何阶段异常均不抛出。
    """
    resolved = resolve_text_encoding(buffer, encoding)
    decoded = _decode_buffer(buffer, resolved)
    return await _parse_markdown_base64_images(decoded, upload_file)
