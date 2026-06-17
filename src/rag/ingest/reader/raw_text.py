"""raw text 读取 + 编码兜底。

设计:
 - 不抛编码异常: 任何编码失败都退到 ``buffer.decode('utf-8', errors='replace')``。
 - markdown 内的 base64 图片: 直接整段删除, 避免大体积数据残留到下游。
"""

from __future__ import annotations

import codecs
import logging
import re
from typing import Final

logger = logging.getLogger(__name__)

# markdown 图片: ``![alt](data:<mime>;base64,<data>)``
_MD_BASE64_IMAGE_RE: Final[re.Pattern[str]] = re.compile(
    r"""!\[([^\]]*)\]\(data:([^;]+);base64,([A-Za-z0-9+/=]+)\)""",
    re.IGNORECASE,
)

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


def _strip_md_base64_images(text: str) -> str:
    """把 markdown 内 ``![alt](data:...;base64,...)`` 整段删除, 避免大体积 base64 残留。"""
    return _MD_BASE64_IMAGE_RE.sub("", text)


async def read_raw_text(
    buffer: bytes,
    encoding: str = "utf-8",
) -> str:
    """bytes → 解码文本 (含 markdown base64 图剥离)。

    Args:
        buffer: 文件二进制内容。
        encoding: 文本编码 (大小写不敏感)。空字符串 / 未知编码 / 解码失败
            都退到 ``utf-8`` + ``errors='replace'``。

    Returns:
        解码后的文本; 编码阶段异常均不抛出。
    """
    resolved = resolve_text_encoding(buffer, encoding)
    text = _decode_buffer(buffer, resolved)
    return _strip_md_base64_images(text)
