"""parse_office 单元测试: OOXML 解压 + XML 抽取。

覆盖:
  - basic: 1 张 slide, 1 段文本 → 抽到
  - multiple_slides: 3 张 slide → 全部抽到, 顺序正确
  - no_slides: 不含 ``ppt/slides/*.xml`` → 抛 RAGError(READER_PARSE)
  - natural_sort: slide1, slide2, slide10 数字排序
  - encoding_fallback: encoding 写失败时降级 utf-8 (XML 内容即可, 不真正失败)
  - tempfile_cleanup: ``/tmp`` 临时文件 unlink

fixture 策略:
  - 不依赖外部 pptx 库造样本; 直接用 ``zipfile.ZipFile`` 写最小 OOXML 内存 zip。
  - ``a:p`` / ``a:t`` 必须带 OOXML 命名空间, 与 ``parse_office`` 的 ``PARA_TAG / TEXT_TAG`` 一致。
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.parse_office import parse_office

# ── OOXML XML 模板 ──

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
SLIDE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"'
    ' xmlns:a="' + A_NS + '">'
    "<p:cSld><p:spTree>{body}</p:spTree></p:cSld>"
    "</p:sld>"
)


def _para(text: str) -> str:
    """构造 ``a:p`` 含 ``a:t``。"""
    # 注意: a:r > a:t 是 OOXML 真实形态; 直接 a:p > a:t 也能被 parser 识别。
    return (
        '<a:p xmlns:a="' + A_NS + '"><a:r><a:rPr/><a:t>' + text + "</a:t></a:r></a:p>"
    )


def _empty_para() -> str:
    """构造无 ``a:t`` 的 ``a:p`` (parse_office 应过滤掉)。"""
    return '<a:p xmlns:a="' + A_NS + '"/>'


def _build_slide_xml(paragraphs: list[str]) -> str:
    body = "".join(paragraphs)
    return SLIDE_XML.format(body=body)


def _build_pptx_zip(slides: dict[str, str]) -> bytes:
    """根据 slide 文件名 → xml 构造 zip bytes。

    slides: ``{"ppt/slides/slide1.xml": xml_str, ...}``
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in slides.items():
            zf.writestr(name, content)
        # Content_Types + 其他 OOXML 必需条目 (空 stub 即可, parse_office 不强校验)。
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
    return buf.getvalue()


def _minimal_pptx(text: str) -> bytes:
    """1 张 slide, 1 段文本。"""
    return _build_pptx_zip({"ppt/slides/slide1.xml": _build_slide_xml([_para(text)])})


# ── basic ──


def test_parse_office_basic() -> None:
    """1 张 slide 1 段文本 → 抽到。"""
    out = parse_office(_minimal_pptx("hello pptx"), extension="pptx")
    assert out == "hello pptx"


# ── multiple slides ──


def test_parse_office_multiple_slides() -> None:
    """3 张 slide: slide1=alpha, slide2=bravo, slide3=charlie → 全部抽到, 顺序正确。"""
    zip_bytes = _build_pptx_zip(
        {
            "ppt/slides/slide1.xml": _build_slide_xml([_para("alpha")]),
            "ppt/slides/slide2.xml": _build_slide_xml([_para("bravo")]),
            "ppt/slides/slide3.xml": _build_slide_xml([_para("charlie")]),
        }
    )
    out = parse_office(zip_bytes, extension="pptx")
    # slide 间 \n → alpha \n bravo \n charlie
    assert out == "alpha\nbravo\ncharlie"


def test_parse_office_multiple_paragraphs_per_slide() -> None:
    """单张 slide 含多段 → 段间 \\n; 含空段 → 跳过。"""
    zip_bytes = _build_pptx_zip(
        {
            "ppt/slides/slide1.xml": _build_slide_xml(
                [_para("first"), _empty_para(), _para("second"), _para("third")]
            )
        }
    )
    out = parse_office(zip_bytes, extension="pptx")
    assert out == "first\nsecond\nthird"


# ── no slides → RAGError ──


def test_parse_office_no_slides_raises() -> None:
    """zip 不含 ``ppt/slides/*.xml`` → RAGError(READER_PARSE)。"""
    # 仅含 notesSlide 不算 slide
    zip_bytes = _build_pptx_zip(
        {"ppt/notesSlides/notesSlide1.xml": _build_slide_xml([_para("note")])}
    )
    with pytest.raises(RAGError) as exc_info:
        parse_office(zip_bytes, extension="pptx")
    assert exc_info.value.code == ReaderErrorCode.PARSE


def test_parse_office_empty_zip_raises() -> None:
    """完全空 zip (只有 Content_Types) → RAGError(READER_PARSE)。"""
    zip_bytes = _build_pptx_zip({})
    with pytest.raises(RAGError) as exc_info:
        parse_office(zip_bytes, extension="pptx")
    assert exc_info.value.code == ReaderErrorCode.PARSE


def test_parse_office_not_pptx_extension_raises() -> None:
    """非 pptx extension → RAGError(READER_PARSE) (8.3 default 拒绝)。"""
    with pytest.raises(RAGError) as exc_info:
        parse_office(b"anything", extension="docx")
    assert exc_info.value.code == ReaderErrorCode.PARSE


# ── natural sort ──


def test_parse_office_natural_sort() -> None:
    """slide1, slide2, slide10 → 数字升序 1 < 2 < 10; 写入时故意倒序放入 zip。"""
    zip_bytes = _build_pptx_zip(
        {
            # 故意先放 slide10, 再放 slide1, slide2 验证 zip 内顺序被覆盖
            "ppt/slides/slide10.xml": _build_slide_xml([_para("ten")]),
            "ppt/slides/slide1.xml": _build_slide_xml([_para("one")]),
            "ppt/slides/slide2.xml": _build_slide_xml([_para("two")]),
        }
    )
    out = parse_office(zip_bytes, extension="pptx")
    assert out == "one\ntwo\nten"


def test_parse_office_notes_and_slides_mixed_order() -> None:
    """slide + notesSlide 共存: 都按数字排序; slide2 的 note 出现在 slide2 之前。"""
    zip_bytes = _build_pptx_zip(
        {
            "ppt/notesSlides/notesSlide2.xml": _build_slide_xml([_para("note-2")]),
            "ppt/slides/slide1.xml": _build_slide_xml([_para("slide-1")]),
            "ppt/notesSlides/notesSlide1.xml": _build_slide_xml([_para("note-1")]),
            "ppt/slides/slide2.xml": _build_slide_xml([_para("slide-2")]),
        }
    )
    out = parse_office(zip_bytes, extension="pptx")
    # 数字升序: 1 (slide1, notesSlide1) 在 2 (slide2, notesSlide2) 之前。
    # 同数字时按 zip 名 ascii: notesSlide1 < slide1 → notesSlide1 先; notesSlide2 < slide2 → notesSlide2 先。
    assert out == "note-1\nslide-1\nnote-2\nslide-2"


# ── encoding fallback ──


def test_parse_office_encoding_fallback() -> None:
    """encoding=gbk 在 UTF-8 XML 内容上 decode 失败 → 自动降级 utf-8; 最终仍抽到文本。"""
    text = "中文段落 — encoding fallback"
    zip_bytes = _build_pptx_zip(
        {"ppt/slides/slide1.xml": _build_slide_xml([_para(text)])}
    )
    # XML 本身是 utf-8 编码; 即便传入 gbk, parse_office 内的 _read_member 解码失败会走 fallback。
    out = parse_office(zip_bytes, extension="pptx", encoding="gbk")
    assert text in out


# ── tempfile cleanup ──


def test_parse_office_tempfile_cleanup() -> None:
    """调用前后 ``/tmp`` 中没有残留 ``.pptx`` 临时文件。"""
    # 临时目录枚举前后对比
    before = _list_tmp_pptx()
    _ = parse_office(_minimal_pptx("clean up"), extension="pptx")
    after = _list_tmp_pptx()
    # 新调用不应增加 pptx 临时文件残留 (允许其他并发测试残留, 但本调用前后差应为 0)
    assert before == after


def _list_tmp_pptx() -> set[str]:
    """枚举 ``/tmp`` 下 ``.pptx`` 文件名集合 (绝对路径)。"""
    tmp = "/tmp"
    if not os.path.isdir(tmp):
        return set()
    out: set[str] = set()
    for name in os.listdir(tmp):
        if name.endswith(".pptx"):
            out.add(os.path.join(tmp, name))
    return out


# ── malformed XML ──


def test_parse_office_invalid_xml_raises() -> None:
    """slide 内 XML 不是合法 XML → RAGError(READER_PARSE)。"""
    zip_bytes = _build_pptx_zip({"ppt/slides/slide1.xml": "<not><valid><xml"})
    with pytest.raises(RAGError) as exc_info:
        parse_office(zip_bytes, extension="pptx")
    assert exc_info.value.code == ReaderErrorCode.PARSE


def test_parse_office_invalid_zip_raises() -> None:
    """buffer 不是合法 zip → RAGError(READER_PARSE) (zipfile.BadZipFile 包装)。"""
    with pytest.raises(RAGError) as exc_info:
        parse_office(b"not a zip file at all", extension="pptx")
    assert exc_info.value.code == ReaderErrorCode.PARSE
