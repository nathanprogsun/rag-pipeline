"""extensions/pptx 薄封装单元测试。

覆盖:
  - minimal: 1 张 slide 1 段文本 → raw_text 包含该文本 + meta 字段完整
  - error_wrap: 损坏 buffer → RAGError(code=reader.parse), message 含 'python-zipfile'
  - against_real_fixture: 走真实 sample.pptx
  - not_pptx_extension: parse_office 拒绝非 pptx extension → 错误包装
  - no_images: 不抽 pptx 内嵌图, images=[]
"""

from __future__ import annotations

import io
import zipfile

import pytest

from data import SAMPLE_PPTX
from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.extensions.pptx import PPTX_MIME, pptx_adapter

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
SLIDE_XML_TPL = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    ' xmlns:a="' + A_NS + '">'
    "<p:cSld><p:spTree>{body}</p:spTree></p:cSld>"
    "</p:sld>"
)


def _minimal_pptx(text: str) -> bytes:
    """最小 pptx: 1 张 slide 1 段文本。"""
    body = (
        '<a:p xmlns:a="' + A_NS + '"><a:r><a:rPr/><a:t>' + text + "</a:t></a:r></a:p>"
    )
    xml = SLIDE_XML_TPL.format(body=body)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ppt/slides/slide1.xml", xml)
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
    return buf.getvalue()


# ── minimal ──


async def test_pptx_extension_adapter_minimal() -> None:
    """最小 pptx → raw_text 含文本 + meta 全字段 + images=[]。"""
    text = "extension adapter minimal"
    buf = _minimal_pptx(text)
    result = await pptx_adapter(buf)

    assert text in result.raw_text
    assert result.meta.mime == PPTX_MIME
    assert result.meta.encoding == "utf-8"
    assert result.meta.size_bytes == len(buf)
    assert (
        result.meta.page_count is None
    )  # parse_office 不返回 page_count, 与原 adapter 一致
    assert result.meta.datasource == "api"
    # 薄封装不抽图片 / extras
    assert result.format_text is None
    assert result.images == []
    assert result.extras == {}


# ── 真实 fixture ──


async def test_pptx_extension_adapter_against_real_fixture() -> None:
    """真实 sample.pptx: 验证薄封装走通完整链路 (parse_office + FormatReaderResult)。"""
    buf = SAMPLE_PPTX.read_bytes()
    result = await pptx_adapter(buf)

    assert result.meta.size_bytes == len(buf)
    assert result.meta.mime == PPTX_MIME
    assert isinstance(result.raw_text, str)
    assert len(result.raw_text) > 0
    assert result.images == []


# ── 错误包装 ──


async def test_pptx_extension_adapter_error_wrap_corrupt_buffer() -> None:
    """损坏 buffer → RAGError(READER_PARSE); message 含 'python-zipfile'。"""
    with pytest.raises(RAGError) as exc_info:
        await pptx_adapter(b"this is not a pptx at all")
    assert exc_info.value.code == ReaderErrorCode.PARSE
    # wrap_parse_error 拼接 '<buffer:pptx>: python-zipfile failed: ...'
    assert "python-zipfile" in exc_info.value.message


async def test_pptx_extension_adapter_error_wrap_no_slides() -> None:
    """zip 不含 slide*.xml → parse_office 抛 RAGError → wrap 后仍是 RAGError(READER_PARSE)。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ppt/notesSlides/notesSlide1.xml", "<x/>")
    with pytest.raises(RAGError) as exc_info:
        await pptx_adapter(buf.getvalue())
    assert exc_info.value.code == ReaderErrorCode.PARSE


async def test_pptx_extension_adapter_error_chain() -> None:
    """错误链路保留 (``__cause__`` 是底层 ``RAGError``, 来自 parse_office)。"""
    try:
        await pptx_adapter(b"definitely not a zip")
    except RAGError as e:
        # wrap_parse_error 是 raise ... from e, 链路保留
        assert e.__cause__ is not None
    else:  # pragma: no cover
        pytest.fail("expected RAGError")


# ── encoding 参数传递 ──


async def test_pptx_extension_adapter_encoding_passed_through() -> None:
    """encoding 参数传到 DocMeta.encoding。"""
    result = await pptx_adapter(_minimal_pptx("hi"), encoding="gbk")
    assert result.meta.encoding == "gbk"


# ── 多 slide ──


async def test_pptx_extension_adapter_multi_slide() -> None:
    """3 张 slide → raw_text 含全部文本。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, t in enumerate(["alpha", "bravo", "charlie"], 1):
            xml = SLIDE_XML_TPL.format(
                body=(
                    '<a:p xmlns:a="' + A_NS + '">'
                    "<a:r><a:rPr/><a:t>" + t + "</a:t></a:r>"
                    "</a:p>"
                )
            )
            zf.writestr(f"ppt/slides/slide{i}.xml", xml)
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
    result = await pptx_adapter(buf.getvalue())
    assert "alpha" in result.raw_text
    assert "bravo" in result.raw_text
    assert "charlie" in result.raw_text
    # slide 间 \n; paragraph_count 估算: split('\n') 后非空行
    assert "\n" in result.raw_text
    assert (result.meta.paragraph_count or 0) >= 3
