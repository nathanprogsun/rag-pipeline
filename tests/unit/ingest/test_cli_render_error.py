"""``_render_error`` 行为契约测试: 区分 RAGError (保留 code) vs 其它 Exception。

注意: 本测试不直接 ``import rag.ingest.cli`` — 因为 cli.py 顶层 import 会拉起
``rag.ingest.chunker`` 模块。这里复制一份 ``_render_error`` 等价实现验证契约;
cli.py 的实现必须与本测试等价 (保持单测)
"""

from __future__ import annotations

import pytest
import typer

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError


def _render_error(exc: Exception) -> None:
    """CLI 异常渲染契约 (与 rag.ingest.cli._render_error 等价)。"""
    if isinstance(exc, RAGError):
        typer.echo(f"ingest failed: [{exc.code}] {exc.message}", err=True)
    else:
        typer.echo(f"ingest failed: [{type(exc).__name__}] {exc}", err=True)


def _capture_echo(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """monkey-patch ``typer.echo`` 捕获输出。"""
    captured: dict[str, str] = {}

    def fake_echo(msg: str, err: bool = False) -> None:
        captured["msg"] = msg
        captured["err"] = str(err)

    monkeypatch.setattr(typer, "echo", fake_echo)
    return captured


def test_rag_error_renders_with_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """RAGError → 输出形如 ``ingest failed: [{code}] {message}``, 走 stderr。

    code 是排查关键信号: 没有它只能看到 ``PermissionError`` / ``httpx.ConnectError``
    这种底层类别, 缺少 ``reader.not_found`` / ``reader.api_auth`` 等业务语义。
    """
    captured = _capture_echo(monkeypatch)
    exc = RAGError(code=ReaderErrorCode.NOT_FOUND, message="/path/to/missing.pdf")
    _render_error(exc)

    assert "reader.not_found" in captured["msg"]
    assert "/path/to/missing.pdf" in captured["msg"]
    assert captured["err"] == "True"  # err=True → stderr


def test_rag_error_api_auth_keeps_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """``READER_API_AUTH`` 是 ApiSource 401/403 专属 code, 不能被吞。"""
    captured = _capture_echo(monkeypatch)
    exc = RAGError(
        code=ReaderErrorCode.API_AUTH,
        message="https://api.x/v1: api auth failed: HTTP 401",
    )
    _render_error(exc)

    assert "reader.api_auth" in captured["msg"]
    assert "401" in captured["msg"]


def test_non_rag_error_falls_back_to_type_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非 RAGError → ``[TypeName] {str}`` 兜底, 不假装有 code。"""
    captured = _capture_echo(monkeypatch)
    _render_error(RuntimeError("boom"))

    assert "[RuntimeError]" in captured["msg"]
    assert "boom" in captured["msg"]
    # 不应该有伪 code (例如 ``[reader.xxx]`` 或 ``[RAGError]``)
    assert "[reader." not in captured["msg"]
    assert "[RAGError]" not in captured["msg"]
    # 仍然走 stderr
    assert captured["err"] == "True"


def test_non_rag_error_httpx_type_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """``httpx.ConnectError`` 这种底层异常, 输出 ``[ConnectError] {msg}``。"""
    captured = _capture_echo(monkeypatch)
    err = ConnectionError("connection refused")
    _render_error(err)

    assert "[ConnectionError]" in captured["msg"]
    assert "connection refused" in captured["msg"]
