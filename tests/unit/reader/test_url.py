"""read_url 单元测试 (mock httpx)。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _mock_httpx_response(
    text: str = "<html><body>hi</body></html>",
    *,
    content_type: str = "text/html",
    content: bytes | None = None,
) -> MagicMock:
    """构造一个 mock httpx Response。"""
    resp = MagicMock()
    resp.text = text
    resp.content = content if content is not None else text.encode("utf-8")
    resp.headers = {"content-type": content_type}
    resp.url = "https://example.com/page.html"
    resp.raise_for_status = MagicMock()
    return resp


@pytest.mark.asyncio
async def test_read_url_success_html() -> None:
    """Mock httpx 返回 HTML, 验证抽取 + datasource='url'。"""
    resp = _mock_httpx_response(
        text="<html><body><h1>Web Title</h1><p>网页正文</p></body></html>",
        content_type="text/html; charset=utf-8",
    )

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        from rag.ingest.reader.url import read_url

        doc = await read_url("https://example.com/page.html")

    assert "Web Title" in doc.text
    assert "网页正文" in doc.text
    assert doc.meta.datasource == "url"
    assert doc.meta.mime == "text/html"


@pytest.mark.asyncio
async def test_read_url_http_error() -> None:
    """httpx 抛错 → RAGError(reader.parse)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client_cls.return_value = mock_client

        from rag.ingest.reader.url import read_url

        with pytest.raises(RAGError) as exc_info:
            await read_url("https://bad.example")
    assert exc_info.value.code == ReaderErrorCode.PARSE


@pytest.mark.asyncio
async def test_read_url_extension_from_content_type() -> None:
    """URL 无扩展名 → 用 content-type 推断。"""
    resp = _mock_httpx_response(
        text="name,age\nAlice,30",
        content_type="text/csv; charset=utf-8",
        content=b"name,age\nAlice,30",
    )
    resp.url = "https://api.example.com/export"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        from rag.ingest.reader.url import read_url

        doc = await read_url("https://api.example.com/export")
    assert doc.meta.mime == "text/csv"
    assert "Alice" in doc.text


@pytest.mark.asyncio
async def test_read_url_too_large_from_content_length() -> None:
    """HEAD content-length > max_size → too_large。"""
    head_resp = MagicMock()
    head_resp.headers = {"content-length": str(10_000_000_000)}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=head_resp)
        mock_client_cls.return_value = mock_client

        from rag.error_codes import ReaderErrorCode
        from rag.exception import RAGError
        from rag.ingest.reader.url import read_url

        with pytest.raises(RAGError) as exc_info:
            await read_url("https://big.example/file", max_size=1_000_000)
    assert exc_info.value.code == ReaderErrorCode.TOO_LARGE


@pytest.mark.asyncio
async def test_read_url_too_large_after_download() -> None:
    """下载后 buffer > max_size → too_large。"""
    resp = _mock_httpx_response(text="x", content=b"x" * 1000)
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        from rag.error_codes import ReaderErrorCode
        from rag.exception import RAGError
        from rag.ingest.reader.url import read_url

        with pytest.raises(RAGError) as exc_info:
            await read_url("https://x.example", max_size=10)
    assert exc_info.value.code == ReaderErrorCode.TOO_LARGE


@pytest.mark.asyncio
async def test_read_url_html_uses_content_type_not_path_suffix() -> None:
    """``text/html`` 响应走 html adapter, 不受 ``.shtml`` 等未知后缀影响。"""
    resp = _mock_httpx_response(
        text="<html><body><h1>新浪标题</h1><p>正文</p></body></html>",
        content_type="text/html; charset=utf-8",
    )
    resp.url = "https://finance.sina.com.cn/jjxw/2026/doc.shtml"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        from rag.ingest.reader.url import read_url

        doc = await read_url("https://finance.sina.com.cn/jjxw/2026/doc.shtml")

    assert "新浪标题" in doc.text
    assert doc.meta.mime == "text/html"


@pytest.mark.asyncio
async def test_read_url_sniffs_html_when_content_type_missing() -> None:
    """无 Content-Type 时按 body 嗅探 HTML (如 ``.shtml`` 路径)。"""
    resp = _mock_httpx_response(
        text="<html><body><p>sniffed</p></body></html>",
        content_type="application/octet-stream",
    )
    resp.url = "https://news.example/article.shtml"

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        from rag.ingest.reader.url import read_url

        doc = await read_url("https://news.example/article.shtml")

    assert "sniffed" in doc.text
    assert doc.meta.mime == "text/html"


@pytest.mark.asyncio
async def test_read_url_infer_extension_unknown_content_type() -> None:
    """content-type 未知 → 兜底 'txt'。"""
    resp = _mock_httpx_response(
        text="hi", content_type="application/octet-stream", content=b"hi"
    )
    resp.url = "https://example.com/data"  # 无扩展名
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mock_client_cls.return_value = mock_client

        from rag.ingest.reader.url import read_url

        doc = await read_url("https://example.com/data")
    # 兜底 txt
    assert doc.text == "hi"
