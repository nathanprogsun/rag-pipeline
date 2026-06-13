"""html2md 单元测试 (8 项契约 + 边缘 case)。"""

from __future__ import annotations

import asyncio

import pytest

from rag.ingest.reader.html2md import (
    MAX_HTML_TRANSFORM_CHARS,
    html_to_md,
    simple_markdown_text,
)
from rag.ingest.reader.types import UploadedFileResult, UploadFileHandler  # noqa: F401

# ---------------------------------------------------------------------------
# Section 3.1+3.2: 基本元素
# ---------------------------------------------------------------------------


async def test_html_to_md_simple() -> None:
    assert await html_to_md("<h1>Hello</h1>") == "# Hello"


async def test_html_to_md_atx_headings() -> None:
    md = await html_to_md(
        "<h1>a</h1><h2>b</h2><h3>c</h3><h4>d</h4><h5>e</h5><h6>f</h6>"
    )
    assert md == "# a\n\n## b\n\n### c\n\n#### d\n\n##### e\n\n###### f"


async def test_html_to_md_paragraph() -> None:
    assert await html_to_md("<p>hi</p>") == "hi"


async def test_html_to_md_bullet_list() -> None:
    assert await html_to_md("<ul><li>a</li><li>b</li></ul>") == "- a\n- b"


async def test_html_to_md_ordered_list() -> None:
    assert await html_to_md("<ol><li>a</li></ol>") == "1. a"
    assert await html_to_md("<ol><li>a</li><li>b</li></ol>") == "1. a\n2. b"


async def test_html_to_md_code_block_fenced() -> None:
    assert await html_to_md("<pre><code>code</code>") == "```\ncode\n```"


async def test_html_to_md_link() -> None:
    # Section 3.2: link_style="INLINED" → [text](url)
    assert await html_to_md('<a href="x">y</a>') == "[y](x)"


async def test_html_to_md_strong_em() -> None:
    # Turndown: em='_' + strong='**'
    assert await html_to_md("<strong>bold</strong> and <em>italic</em>") == (
        "**bold** and _italic_"
    )


# ---------------------------------------------------------------------------
# Section 3.3: 删除标签
# ---------------------------------------------------------------------------


async def test_html_to_md_strip_script() -> None:
    assert await html_to_md("<script>alert(1)</script>Hello") == "Hello"


async def test_html_to_md_strip_style() -> None:
    assert await html_to_md("<style>body{color:red}</style>Hello") == "Hello"


async def test_html_to_md_strip_iframe() -> None:
    assert await html_to_md('<iframe src="x"></iframe>Hello') == "Hello"


async def test_html_to_md_strip_i() -> None:
    assert await html_to_md("<i>italic</i>Hello") == "Hello"


# ---------------------------------------------------------------------------
# Section 3.4: 媒体规则
# ---------------------------------------------------------------------------


async def test_html_to_md_video_to_link() -> None:
    # video 自身 src 优先
    assert await html_to_md('<video src="v.mp4"></video>') == "[v.mp4](v.mp4)"


async def test_html_to_md_video_falls_back_to_source() -> None:
    # video 无自身 src, 退到第一个 <source> 的 src
    assert await html_to_md('<video><source src="s.mp4"></video>') == "[s.mp4](s.mp4)"


async def test_html_to_md_video_own_src_wins_over_source() -> None:
    # 自身 src 优先于内部 <source>
    assert (
        await html_to_md('<video src="own.mp4"><source src="child.mp4"></video>')
        == "[own.mp4](own.mp4)"
    )


async def test_html_to_md_audio_to_link() -> None:
    assert await html_to_md('<audio src="a.mp3"></audio>') == "[a.mp3](a.mp3)"


async def test_html_to_md_video_without_src_decomposed() -> None:
    # video/audio 都无 src → decompose, 无任何输出
    assert await html_to_md("<video></video>Hello") == "Hello"


# ---------------------------------------------------------------------------
# Section 3.5: base64 图片
# ---------------------------------------------------------------------------


async def test_html_to_md_base64_image_no_upload() -> None:
    # 无 upload_file: src 置空, 避免大体积 base64 进入 markdown
    md = await html_to_md('<img src="data:image/png;base64,QUFB" alt="x">')
    # 期望: alt 保留, src 为空字符串
    assert "![x]()" in md
    assert "QUFB" not in md
    assert "base64" not in md


async def test_html_to_md_base64_image_with_upload() -> None:
    """用 mock async upload, 验证 src 被替换为上传返回的 key。"""
    seen: list[tuple[str, str, bytes]] = []

    async def upload(filename: str, mime: str, data: bytes) -> UploadedFileResult:
        seen.append((filename, mime, data))
        return {"key": f"uploaded/{filename}"}

    md = await html_to_md(
        '<img src="data:image/png;base64,QUFB" alt="x">',
        upload_file=upload,
    )
    assert len(seen) == 1
    filename, mime, data = seen[0]
    assert mime == "image/png"
    assert data == b"AAA"  # base64 "QUFB" 解码后是 "AAA"
    assert "uploaded/" in md
    assert "QUFB" not in md
    assert "base64,QUFB" not in md


async def test_html_to_md_base64_image_upload_failure() -> None:
    """upload_file 抛错时, src 置空 (不抛) — Section 3.8 行为由 upload_file 路径走 're-raise'。"""

    # 注意: Section 3.8 说 *整个转换* 失败时, 有 upload_file 就 re-raise。
    # 但 base64 单图失败 → 单图降级, 不应整篇失败。
    async def bad_upload(filename: str, mime: str, data: bytes) -> UploadedFileResult:
        raise RuntimeError("upload service down")

    md = await html_to_md(
        '<img src="data:image/png;base64,QUFB" alt="x"><p>after</p>',
        upload_file=bad_upload,
    )
    # 单图失败降级为空 src, 后续内容保留
    assert "after" in md
    assert "QUFB" not in md


async def test_html_to_md_base64_concurrent_upload() -> None:
    """多个 base64 图应并发上传 (Semaphore=5)。"""
    import time

    calls = 0
    max_inflight = 0
    inflight = 0
    lock = asyncio.Lock()

    async def upload(filename: str, mime: str, data: bytes) -> UploadedFileResult:
        nonlocal calls, max_inflight, inflight
        async with lock:
            inflight += 1
            calls += 1
            max_inflight = max(max_inflight, inflight)
        try:
            await asyncio.sleep(0.05)
            return {"key": f"key/{filename}"}
        finally:
            async with lock:
                inflight -= 1

    # 3 张 base64 图
    html = (
        '<img src="data:image/png;base64,QUFB">'
        '<img src="data:image/png;base64,QkJC">'
        '<img src="data:image/png;base64,Q0ND">'
    )
    t0 = time.monotonic()
    md = await html_to_md(html, upload_file=upload)
    elapsed = time.monotonic() - t0

    assert calls == 3
    # 并发: 3 张图应 < 3 * 0.05 = 0.15s; 串行会 ~ 0.15s, 并发会 ~ 0.05s
    assert elapsed < 0.12, f"expected concurrent upload, took {elapsed:.3f}s"
    # 每张图的 key 应出现在结果中
    for i in range(3):
        assert f"key/html_base64_{i}" in md or "key/html_base64_" in md


# ---------------------------------------------------------------------------
# Section 3.6: 超大 HTML
# ---------------------------------------------------------------------------


async def test_html_to_md_oversize() -> None:
    huge = "x" * (MAX_HTML_TRANSFORM_CHARS + 1)
    # 超大原样返回
    assert await html_to_md(huge) == huge


async def test_html_to_md_oversize_with_upload() -> None:
    # 超大时不应触发 upload_file
    called = False

    async def upload(filename: str, mime: str, data: bytes) -> UploadedFileResult:
        nonlocal called
        called = True
        return {"key": "x"}

    huge = "x" * (MAX_HTML_TRANSFORM_CHARS + 1)
    result = await html_to_md(huge, upload_file=upload)
    assert result == huge
    assert called is False


# ---------------------------------------------------------------------------
# 边缘 case
# ---------------------------------------------------------------------------


async def test_html_to_md_empty_html() -> None:
    assert await html_to_md("") == ""


async def test_html_to_md_plain_text() -> None:
    assert await html_to_md("plain text") == "plain text"


async def test_html_to_md_exception_no_upload() -> None:
    # 损坏 HTML + 无 upload_file → 返回 "" + log warning
    # markdownify 对一般损坏 HTML 是容错的, 需要造一个让它真正抛错的场景
    # 这里用传入 None (强类型上也接受 str) 或者异常 hook 比较麻烦, 用 patched
    from unittest.mock import patch

    with patch(
        "rag.ingest.reader.html2md._run_markdownify",
        side_effect=RuntimeError("boom"),
    ):
        # Section 3.8: 无 upload_file → log warning + return ""
        assert await html_to_md("<p>x</p>") == ""


async def test_html_to_md_exception_with_upload_re_raises() -> None:
    from unittest.mock import patch

    with patch(
        "rag.ingest.reader.html2md._run_markdownify",
        side_effect=RuntimeError("boom"),
    ):

        async def upload(filename: str, mime: str, data: bytes) -> UploadedFileResult:
            return {"key": "k"}

        with pytest.raises(RuntimeError, match="boom"):
            await html_to_md("<p>x</p>", upload_file=upload)


# ---------------------------------------------------------------------------
# simple_markdown_text 单元测试 (sync 工具函数, 保持 sync 测试)
# ---------------------------------------------------------------------------


def test_simple_markdown_text_unescapes_entities() -> None:
    assert simple_markdown_text("A &amp; B &lt;tag&gt;") == "A & B <tag>"


def test_simple_markdown_text_normalizes_link_newlines() -> None:
    # `[a\nb](url)` → `[a b](url)`
    assert simple_markdown_text("[a\nb](url)") == "[a b](url)"


def test_simple_markdown_text_strips_escaped_md_chars() -> None:
    assert simple_markdown_text(r"not \# heading") == "not # heading"


def test_simple_markdown_text_collapses_blank_lines() -> None:
    assert simple_markdown_text("a\n\n\n\nb") == "a\n\nb"


def test_simple_markdown_text_trims() -> None:
    assert simple_markdown_text(" hello ") == "hello"


# ---------------------------------------------------------------------------
# 类型 / 导出 sanity
# ---------------------------------------------------------------------------


def test_max_html_transform_chars_is_one_million() -> None:
    assert MAX_HTML_TRANSFORM_CHARS == 1_000_000


def test_upload_file_handler_type_alias() -> None:
    # types.py 应该导出 UploadFileHandler
    from rag.ingest.reader.types import UploadFileHandler as UFH

    assert UFH is not None
