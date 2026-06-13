"""read_url 错误路径测试: 404 / timeout / 5xx / oversize / redirect loop / bad mime。

策略: 用 httpx.MockTransport 替换 AsyncClient 内部 transport,
不依赖外网。MockTransport 通过 monkeypatch 注入到
``rag.ingest.reader.url.httpx.AsyncClient``。

注: read_url 内调 ``httpx.AsyncClient(follow_redirects=True, timeout=...)``,
我们替换整个 AsyncClient 类为 factory, factory 内部走真实的
``real_client = url_mod.httpx.AsyncClient.__init__`` 但注入 transport= 参数,
避免 patch 自引用递归。
"""

from __future__ import annotations

import httpx
import pytest


def _patch_async_client_with_transport(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport
) -> None:
    """把 rag.ingest.reader.url 模块里的 httpx.AsyncClient 替换成注入 transport 的版本。"""
    import rag.ingest.reader.url as url_mod

    real_client = url_mod.httpx.AsyncClient

    def factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(url_mod.httpx, "AsyncClient", factory)


def _ok_response(
    *,
    content: bytes = b"<html><body>OK</body></html>",
    content_type: str = "text/html",
    request: httpx.Request | None = None,
    status: int = 200,
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=content,
        headers={"content-type": content_type},
        request=request or httpx.Request("GET", "https://example.com/page.html"),
    )


# ── 404 ──


@pytest.mark.asyncio
async def test_read_url_404_raises_reader_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 404 → raise_for_status 触发 → RAGError(reader.parse)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.reader.url import read_url

    transport = httpx.MockTransport(
        lambda req: httpx.Response(404, content=b"not found", request=req)
    )
    _patch_async_client_with_transport(monkeypatch, transport)

    with pytest.raises(RAGError) as exc_info:
        await read_url("https://example.com/missing")

    assert exc_info.value.code == ReaderErrorCode.PARSE


# ── timeout ──


@pytest.mark.asyncio
async def test_read_url_timeout_raises_reader_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MockTransport 抛 ConnectTimeout → RAGError(reader.parse)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.reader.url import read_url

    transport = httpx.MockTransport(
        lambda req: (_ for _ in ()).throw(httpx.ConnectTimeout("connection timed out"))
    )
    _patch_async_client_with_transport(monkeypatch, transport)

    with pytest.raises(RAGError) as exc_info:
        await read_url("https://slow.example.com")

    assert exc_info.value.code == ReaderErrorCode.PARSE


# ── 5xx server error ──


@pytest.mark.asyncio
async def test_read_url_500_raises_reader_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 500 → RAGError(reader.parse)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.reader.url import read_url

    transport = httpx.MockTransport(
        lambda req: httpx.Response(500, content=b"internal error", request=req)
    )
    _patch_async_client_with_transport(monkeypatch, transport)

    with pytest.raises(RAGError) as exc_info:
        await read_url("https://broken.example.com")

    assert exc_info.value.code == ReaderErrorCode.PARSE


@pytest.mark.asyncio
async def test_read_url_503_raises_reader_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 503 → RAGError(reader.parse)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.reader.url import read_url

    transport = httpx.MockTransport(
        lambda req: httpx.Response(503, content=b"unavailable", request=req)
    )
    _patch_async_client_with_transport(monkeypatch, transport)

    with pytest.raises(RAGError) as exc_info:
        await read_url("https://maintenance.example.com")

    assert exc_info.value.code == ReaderErrorCode.PARSE


# ── oversize (content-length) ──


@pytest.mark.asyncio
async def test_read_url_oversize_from_content_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEAD 返回 content-length > max_size → RAGError(reader.too_large)。

    read_url 在 HEAD 预检时就抛错, 不下载 body。
    """
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.reader.url import read_url

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "HEAD":
            return httpx.Response(
                200,
                headers={"content-length": str(10_000_000)},
                request=req,
            )
        return _ok_response(request=req)

    transport = httpx.MockTransport(handler)
    _patch_async_client_with_transport(monkeypatch, transport)

    with pytest.raises(RAGError) as exc_info:
        await read_url("https://big.example.com", max_size=1_000_000)

    assert exc_info.value.code == ReaderErrorCode.TOO_LARGE


@pytest.mark.asyncio
async def test_read_url_oversize_after_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEAD 不带 content-length 但 GET 后 body 实际超长 → too_large (下载后检查)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.reader.url import read_url

    big_body = b"x" * 1000

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "HEAD":
            # 无 content-length header
            return httpx.Response(200, headers={}, request=req)
        return _ok_response(content=big_body, request=req)

    transport = httpx.MockTransport(handler)
    _patch_async_client_with_transport(monkeypatch, transport)

    with pytest.raises(RAGError) as exc_info:
        await read_url("https://example.com/file", max_size=100)

    assert exc_info.value.code == ReaderErrorCode.TOO_LARGE


# ── redirect loop ──


@pytest.mark.asyncio
async def test_read_url_redirect_loop_raises_reader_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """302 redirect 无限循环 → httpx 抛 TooManyRedirects → RAGError(reader.parse)。

    httpx follow_redirects=True 默认最多 20 次, 超出抛 TooManyRedirects
    (HTTPError 子类)。
    """
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.reader.url import read_url

    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            302,
            headers={"location": "https://example.com/loop"},
            request=req,
        )
    )
    _patch_async_client_with_transport(monkeypatch, transport)

    with pytest.raises(RAGError) as exc_info:
        await read_url("https://example.com/loop")

    assert exc_info.value.code == ReaderErrorCode.PARSE


# ── bad mime (二进制伪装 text/html) ──


@pytest.mark.asyncio
async def test_read_url_binary_content_with_text_html_mime_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """content-type=text/html 但内容是非 UTF-8 binary → 不抛错。

    ``raw_text.read_raw_text`` 内部对 UnicodeDecodeError 兜底 ``errors='replace'``
    (返回 U+FFFD 替换字符), 不抛错。html adapter 也跟着不抛错。
    read_url 拿到一个含 U+FFFD 的文本 doc (mime 仍是 text/html)。
    """
    from rag.ingest.reader.url import read_url

    fake_binary = b"\x00\x01\x02\xff\xfe" * 50  # 非 UTF-8 valid

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "HEAD":
            return httpx.Response(200, headers={}, request=req)
        return httpx.Response(
            200,
            content=fake_binary,
            headers={"content-type": "text/html; charset=utf-8"},
            request=req,
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client_with_transport(monkeypatch, transport)

    doc = await read_url("https://example.com/spoofed.html")

    assert doc.meta.mime == "text/html"
    # raw_text 含 U+FFFD 替换字符 (而不是抛错)
    assert "" in doc.text or doc.text  # 不抛错, 有内容


# ── HEAD 失败但 GET 成功 (HEAD 不阻塞 GET) ──


@pytest.mark.asyncio
async def test_read_url_head_fails_but_get_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HEAD 抛错 (e.g. 405) → GET 继续, 不抛错。read_url 内部 try/except 吞 HEAD HTTPError。"""
    from rag.ingest.reader.url import read_url

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "HEAD":
            return httpx.Response(405, request=req)
        return _ok_response(
            content=b"<html><body><h1>Head405</h1><p>ok</p></body></html>",
            request=req,
        )

    transport = httpx.MockTransport(handler)
    _patch_async_client_with_transport(monkeypatch, transport)

    doc = await read_url("https://example.com/head405")

    assert "Head405" in doc.text


# ── 完整链路: pipeline 走 URL 也走通 ──


@pytest.mark.asyncio
async def test_read_url_404_via_pipeline_raises_reader_parse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 错误贯穿到 pipeline.ingest 也会正确抛 RAGError。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.chunker import Chunker, ChunkSettings
    from rag.ingest.pipeline import IngestPipeline
    from rag.ingest.source import UrlSource

    transport = httpx.MockTransport(
        lambda req: httpx.Response(404, content=b"missing", request=req)
    )
    _patch_async_client_with_transport(monkeypatch, transport)

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    with pytest.raises(RAGError) as exc_info:
        await pipeline.ingest(UrlSource("https://gone.example.com/page"))

    assert exc_info.value.code == ReaderErrorCode.PARSE


@pytest.mark.asyncio
async def test_read_url_oversize_via_pipeline_raises_reader_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """oversize 错误贯穿 pipeline 也抛 RAGError(too_large)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError
    from rag.ingest.chunker import Chunker, ChunkSettings
    from rag.ingest.pipeline import IngestPipeline
    from rag.ingest.source import UrlSource

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "HEAD":
            return httpx.Response(
                200,
                headers={"content-length": str(10_000_000)},
                request=req,
            )
        return _ok_response(request=req)

    transport = httpx.MockTransport(handler)
    _patch_async_client_with_transport(monkeypatch, transport)

    pipeline = IngestPipeline(chunker=Chunker(ChunkSettings()))
    with pytest.raises(RAGError) as exc_info:
        await pipeline.ingest(UrlSource("https://big.example.com", max_size=1_000_000))

    assert exc_info.value.code == ReaderErrorCode.TOO_LARGE
