"""``raw_text.read_raw_text`` 单元测试。"""

from __future__ import annotations

import pytest

from rag.ingest.reader.raw_text import (
    RAW_ENCODING_LIST,
    detect_text_encoding,
    read_raw_text,
    resolve_text_encoding,
)

# ─────────────────────────────────────────────────────────────────────────────
# 2.1 白名单 + 2.2 ascii 降级
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raw_text_ascii() -> None:
    """纯 ASCII buffer + encoding='ascii' → 直接解码, 不降级。"""
    buf = b"hello world"
    text = await read_raw_text(buf, encoding="ascii")
    assert text == "hello world"


@pytest.mark.asyncio
async def test_raw_text_ascii_with_chinese_falls_back_to_utf8() -> None:
    """含 > 0x7F 字节 + encoding='ascii' → 降级 utf-8 (避免中文乱码)。"""
    buf = "中文 abc".encode()
    text = await read_raw_text(buf, encoding="ascii")
    assert text == "中文 abc"


@pytest.mark.asyncio
async def test_raw_encoding_list_contains_expected_aliases() -> None:
    """白名单含 Node 原生编码别名。"""
    for enc in ("ascii", "utf8", "utf-8", "utf16le", "base64", "hex", "latin1"):
        assert enc in RAW_ENCODING_LIST


# ─────────────────────────────────────────────────────────────────────────────
# 2.3 其它编码 (gbk) + 2.4 兜底
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_raw_text_utf8_chinese() -> None:
    """utf-8 + 中文字符 → 正确解码。"""
    buf = "你好,世界".encode()
    text = await read_raw_text(buf, encoding="utf-8")
    assert text == "你好,世界"


@pytest.mark.asyncio
async def test_raw_text_gbk_chinese() -> None:
    """gbk + 中文字符 → 通过 ``codecs.lookup`` 兜底。"""
    buf = "你好,世界".encode("gbk")
    text = await read_raw_text(buf, encoding="gbk")
    assert text == "你好,世界"


def test_detect_text_encoding_gbk_without_bom() -> None:
    """GBK 字节流在默认 utf-8 路径下应探测为 gbk/gb18030。"""
    buf = "ETF策略初始化完成".encode("gbk")
    detected = detect_text_encoding(buf)
    assert detected in ("gbk", "gb18030", "cp936")


@pytest.mark.asyncio
async def test_read_raw_text_auto_detects_gbk() -> None:
    """encoding 默认 utf-8 时自动探测 GBK 日志, 不出现替换字符乱码。"""
    plain = "2026-01-05 14:01:00 - INFO - 调仓完成"
    buf = plain.encode("gbk")
    text = await read_raw_text(buf, encoding="utf-8")
    assert "调仓完成" in text
    assert "�" not in text


def test_resolve_text_encoding_respects_explicit_gbk() -> None:
    """显式 gbk 时不改探测结果 (由调用方指定)。"""
    buf = "你好".encode("gbk")
    assert resolve_text_encoding(buf, "gbk") == "gbk"


@pytest.mark.asyncio
async def test_raw_text_empty_encoding_falls_back_to_utf8() -> None:
    """encoding='' → utf-8 兜底。"""
    buf = b"plain text"
    text = await read_raw_text(buf, encoding="")
    assert text == "plain text"


@pytest.mark.asyncio
async def test_raw_text_invalid_encoding_falls_back_without_raising() -> None:
    """encoding='garbage' → 未知编码, 不抛, 退到 utf-8 + errors='replace'。"""
    buf = b"survivable text"
    # 不抛异常即通过; 文本应包含 ``survivable text`` (replace 后字符可能变化,
    # 但 ASCII 段必定保留)。
    text = await read_raw_text(buf, encoding="garbage")
    assert "survivable text" in text


# ─────────────────────────────────────────────────────────────────────────────
# 2.5 base64 图剥离
# ─────────────────────────────────────────────────────────────────────────────


# 1x1 PNG (透明), base64 编码: ``iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAA...
# 完整 ~70 字符。下面用更短的格式以保持测试可读。
_TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="


@pytest.mark.asyncio
async def test_raw_text_base64_image_stripped() -> None:
    """markdown 内 base64 data URL → 整段被删除 (避免大体积 base64 残留)。"""
    md = f"# Title\n\n![png](data:image/png;base64,{_TINY_PNG_B64})\n\nafter image"
    text = await read_raw_text(md.encode("utf-8"), encoding="utf-8")
    assert "data:image" not in text
    assert "after image" in text
    assert text.startswith("# Title")


@pytest.mark.asyncio
async def test_raw_text_plain_text_no_base64_passthrough() -> None:
    """纯文本无 data URL → 直接输出。"""
    buf = b"no images here"
    text = await read_raw_text(buf, encoding="utf-8")
    assert text == "no images here"
