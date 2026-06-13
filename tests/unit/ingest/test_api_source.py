"""ApiSource 单元测试: 走 IngestPipeline.ingest(ApiSource(...)) 端到端。

策略: 通过 ApiSource.http_client 注入 ``httpx.AsyncClient(transport=MockTransport)``,
避免对外网依赖, 走真实 httpx 路径 (timeout / status / 内容长度 全部走 httpx 自身)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine

import httpx
import pytest

from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.source import ApiSource
from rag.ingest.types import IngestResult


def run_ingest(coro: Coroutine[object, object, IngestResult]) -> IngestResult:
    return asyncio.run(coro)


def _pipeline() -> IngestPipeline:
    return IngestPipeline(chunker=Chunker(ChunkSettings()))


def _client_with(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    """构造带 MockTransport 的 AsyncClient。"""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(5.0))


# ── 字段抽取 ─────────────────────────────────────────────────


def test_api_source_extracts_text_field() -> None:
    """``{"text": "hello"}`` → text='hello'。"""
    source = ApiSource(
        server_url="https://api.example.com",
        endpoint="/v1/files",
        http_client=_client_with(
            lambda req: httpx.Response(200, content=b'{"text": "hello"}', request=req)
        ),
    )

    result = run_ingest(_pipeline().ingest(source))

    assert result.chunks, "ApiSource produced 0 chunks"
    assert "hello" in result.chunks[0].text
    # DocMeta 字段
    assert result.doc_meta.datasource == "api"
    assert result.doc_meta.mime == "application/json"
    assert result.doc_meta.source == "https://api.example.com/v1/files"


def test_api_source_field_priority() -> None:
    """``{"message": "msg"}`` → 跳过 text/content/data 取 message='msg'。"""
    source = ApiSource(
        server_url="https://api.example.com",
        endpoint="/v1/chat",
        http_client=_client_with(
            lambda req: httpx.Response(
                200,
                content=b'{"message": "msg"}',
                request=req,
            )
        ),
    )

    result = run_ingest(_pipeline().ingest(source))

    assert "msg" in result.chunks[0].text
    # 确保不是把整 JSON 丢进去
    assert "{" not in result.chunks[0].text


# ── 鉴权 header ─────────────────────────────────────────────


def test_api_source_auth_header() -> None:
    """传 auth_token → 请求带 ``Authorization: Bearer <token>``。"""
    captured: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["Authorization"] = req.headers.get("Authorization", "")
        return httpx.Response(200, content=b'{"text": "ok"}', request=req)

    source = ApiSource(
        server_url="https://api.example.com",
        endpoint="/v1/x",
        auth_token="secret-token-abc",
        http_client=_client_with(handler),
    )

    run_ingest(_pipeline().ingest(source))

    assert captured["Authorization"] == "Bearer secret-token-abc"


# ── 错误码细分 ─────────────────────────────────────────────


def test_api_source_404() -> None:
    """HTTP 404 → RAGError(code=READER_API_STATUS)。"""
    source = ApiSource(
        server_url="https://api.example.com",
        endpoint="/missing",
        http_client=_client_with(
            lambda req: httpx.Response(404, content=b"not found", request=req)
        ),
    )

    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError

    with pytest.raises(RAGError) as exc_info:
        run_ingest(_pipeline().ingest(source))
    assert exc_info.value.code == ReaderErrorCode.API_STATUS


def test_api_source_timeout() -> None:
    """MockTransport 抛 ConnectTimeout → RAGError(code=READER_API_TIMEOUT)。"""
    from rag.error_codes import ReaderErrorCode
    from rag.exception import RAGError

    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connection timed out")

    source = ApiSource(
        server_url="https://slow.example.com",
        endpoint="/x",
        timeout_s=1.0,
        http_client=_client_with(handler),
    )

    with pytest.raises(RAGError) as exc_info:
        run_ingest(_pipeline().ingest(source))
    assert exc_info.value.code == ReaderErrorCode.API_TIMEOUT


# ── http_client 注入校验 ────────────────────────────────────


def test_api_source_uses_injected_client() -> None:
    """传 httpx.AsyncClient mock → pipeline 直接复用, 不内部新建。"""
    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, content=b'{"text": "x"}', request=req)
        ),
        timeout=httpx.Timeout(5.0),
    )
    source = ApiSource(
        server_url="https://api.example.com",
        endpoint="/v1/x",
        http_client=mock_client,
    )

    # 注入的 client 应被复用: aclose() 也不会被内部调用 (owns_client=False)。
    result = run_ingest(_pipeline().ingest(source))

    assert result.chunks[0].text.strip() == "x"
    # 注入 client 仍可继续使用, 说明内部未对其 aclose
    assert not mock_client.is_closed
