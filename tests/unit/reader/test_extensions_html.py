"""``extensions.html.html_adapter`` 单元测试 (Section 6 契约)。

覆盖:
  - basic: 简单 HTML → markdown
  - with_base64_image: 含 base64 img, 验证空 src 替换
  - oversize: html > 1_000_000 chars, 验证返回原 HTML
  - strip_script: ``<script>`` 标签被剥
"""

from __future__ import annotations

import pytest

from rag.ingest.reader.extensions.html import HTML_MIME, html_adapter

# ── basic ──


@pytest.mark.asyncio
async def test_html_basic() -> None:
    """简单 HTML → markdown, raw_text = markdown, format_text=None, mime=text/html。"""
    buf = b"<h1>Title</h1><p>Hello <b>world</b></p>"
    result = await html_adapter(buf)

    # markdownify ATX heading + 段落 + bold
    assert "# Title" in result.raw_text
    assert "Hello" in result.raw_text
    assert "**world**" in result.raw_text
    # format_text 始终为 None (Section 6.3)
    assert result.format_text is None
    assert result.meta.mime == HTML_MIME
    assert result.meta.mime == "text/html"


# ── with_base64_image ──


@pytest.mark.asyncio
async def test_html_with_base64_image() -> None:
    """含 base64 img → src 被置空 (避免大体积 base64 进入 markdown)。"""
    tiny_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAfbLI3wAAAABJRU5ErkJggg=="
    buf = (
        f'<p>Before</p><img src="data:image/png;base64,{tiny_png_b64}" alt="pic"/><p>After</p>'
    ).encode()
    result = await html_adapter(buf)

    # base64 data URL 不应残留
    assert "data:image" not in result.raw_text
    # 文本应保留
    assert "Before" in result.raw_text
    assert "After" in result.raw_text
    # format_text 仍 None
    assert result.format_text is None
    assert result.meta.mime == "text/html"


# ── oversize ──


@pytest.mark.asyncio
async def test_html_oversize() -> None:
    """html > 1_000_000 chars (decoded) → 走 raw_text 但 html_to_md 走 ``> MAX_HTML_TRANSFORM_CHARS``
    分支, 返回原 HTML; raw_text 应保留原 HTML 字符串。

    注: ``read_raw_text`` 自身不做大小检查; 限制在 ``html_to_md`` 内部 (Section 3.6)。
    """
    big_html = "<p>" + ("a" * 1_000_001) + "</p>"
    result = await html_adapter(big_html.encode("utf-8"))

    # raw_text 应等于输入的 html 字符串 (Section 3.6 原样返回)
    assert result.raw_text == big_html
    assert result.format_text is None
    assert result.meta.mime == "text/html"


# ── strip_script ──


@pytest.mark.asyncio
async def test_html_strip_script() -> None:
    """``<script>`` 标签及其内容被剥 (Section 3.3: 删除 i / script / iframe / style)。"""
    buf = (
        b"<h1>Title</h1>"
        b"<script>alert('xss');</script>"
        b"<p>Visible</p>"
        b"<style>body { color: red; }</style>"
    )
    result = await html_adapter(buf)

    # script / style 标签应被剥
    assert "alert" not in result.raw_text
    assert "color: red" not in result.raw_text
    # 可见内容保留
    assert "# Title" in result.raw_text
    assert "Visible" in result.raw_text
    assert result.format_text is None
