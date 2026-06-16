"""``extensions.text.text_adapter`` 单元测试 (txt + md 共享 raw_text 入口)。"""

from __future__ import annotations

import pytest

from rag.ingest.reader.extensions.text import TEXT_MIME, text_adapter
from rag.ingest.reader.types import UploadedFileResult


@pytest.mark.asyncio
async def test_text_adapter_txt_plain() -> None:
    """纯文本 buffer → raw_text = buffer 解码结果。"""
    buf = b"hello world"
    result = await text_adapter(buf)
    assert result.raw_text == "hello world"
    assert result.meta.mime == TEXT_MIME
    assert result.meta.mime == "text/plain"
    assert result.format_text is None
    assert result.images == []


@pytest.mark.asyncio
async def test_text_adapter_md_keeps_markdown_syntax() -> None:
    """markdown buffer 走 text_adapter, 返回的 raw_text 含 ``#`` 标题。"""
    buf = b"# Title\n\n## Section\n\n- item 1\n- item 2"
    result = await text_adapter(buf)
    assert "# Title" in result.raw_text
    assert "## Section" in result.raw_text
    assert "- item 1" in result.raw_text
    assert result.meta.mime == "text/plain"


@pytest.mark.asyncio
async def test_text_adapter_chinese_encoding() -> None:
    """中文字符 + utf-8 → 正确解码。"""
    buf = "你好,世界".encode()
    result = await text_adapter(buf, encoding="utf-8")
    assert result.raw_text == "你好,世界"


@pytest.mark.asyncio
async def test_text_adapter_gbk_encoding() -> None:
    """gbk 编码 buffer → 通过 raw_text 兜底解码。"""
    buf = "你好".encode("gbk")
    result = await text_adapter(buf, encoding="gbk")
    assert result.raw_text == "你好"


@pytest.mark.asyncio
async def test_text_adapter_md_base64_image_upload() -> None:
    """markdown 含 base64 data URL + upload_file mock → 图被替换为 key。"""
    tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="
    md = f"# Doc\n\n![pic](data:image/png;base64,{tiny_png_b64})\n"

    async def uploader(name: str, mime: str, buf: bytes) -> UploadedFileResult:
        return {"key": "s3://bucket/key.png"}

    result = await text_adapter(md.encode("utf-8"), upload_file=uploader)

    assert "data:image" not in result.raw_text
    assert "s3://bucket/key.png" in result.raw_text
    assert "# Doc" in result.raw_text


@pytest.mark.asyncio
async def test_text_adapter_md_base64_no_upload_strips() -> None:
    """markdown 含 base64 + upload_file=None → 整段 data URL 删除。"""
    tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="
    md = f"intro\n\n![pic](data:image/png;base64,{tiny_png_b64})\n\nend"
    result = await text_adapter(md.encode("utf-8"))
    assert "data:image" not in result.raw_text
    assert "intro" in result.raw_text
    assert "end" in result.raw_text


@pytest.mark.asyncio
async def test_text_adapter_invalid_bytes_falls_back_without_raising() -> None:
    """含非法字节 → raw_text 内部兜底, 不抛异常。"""
    buf = b"\xff\xfe\xfd broken"
    result = await text_adapter(buf, encoding="utf-8")
    assert isinstance(result.raw_text, str)
    assert len(result.raw_text) > 0
