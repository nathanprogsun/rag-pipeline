"""StructureNormalizer + NoOpNormalizer 单元测试。

所有 normalize 调用都是 async (基类契约), 测试用 pytest-asyncio AUTO 模式。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rag.ingest.normalizer import (
    NoOpNormalizer,
    StructuredText,
    StructureMode,
    StructureNormalizer,
)
from rag.ingest.types import DocMeta, TextDoc

# ─────────────────────────────────────────────────────────────────────────────
# 辅助: 构造 chat_model mock (async ainvoke 返回 StructuredText)
# ─────────────────────────────────────────────────────────────────────────────


def _make_chat_model(result: StructuredText | Exception) -> MagicMock:
    """构造 Runnable mock, ainvoke 返回 result (StructuredText or 异常)。"""
    model = MagicMock()
    if isinstance(result, Exception):
        model.ainvoke = AsyncMock(side_effect=result)
        model.invoke = MagicMock(side_effect=result)
    else:
        model.ainvoke = AsyncMock(return_value=result)
        model.invoke = MagicMock(return_value=result)
    return model


def _raw_doc(text: str = "plain text body") -> TextDoc:
    return TextDoc(text=text, meta=DocMeta(filename="x.txt", datasource="file"))


# ─────────────────────────────────────────────────────────────────────────────
# 闸门 1: FORBID / no-model
# ─────────────────────────────────────────────────────────────────────────────


async def test_forbid_mode_skips() -> None:
    """mode=FORBID → skipped=True, text 保持原样, LLM 不被调。"""
    fake_model = _make_chat_model(StructuredText(result_text="LLM-OUTPUT", summary="x"))
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORBID)
    result = await n.normalize_with_result(_raw_doc("hello"))
    assert result.skipped is True
    assert result.degraded is False
    assert result.result_text == "hello"
    assert result.input_tokens == 0
    fake_model.ainvoke.assert_not_called()
    fake_model.invoke.assert_not_called()


async def test_none_chat_model_skips() -> None:
    """chat_model=None → skipped=True。"""
    n = StructureNormalizer(chat_model=None, mode=StructureMode.AUTO)
    result = await n.normalize_with_result(_raw_doc("hello"))
    assert result.skipped is True
    assert result.result_text == "hello"


# ─────────────────────────────────────────────────────────────────────────────
# 闸门 2: AUTO + md 标题
# ─────────────────────────────────────────────────────────────────────────────


async def test_auto_with_markdown_headers_skips() -> None:
    """AUTO + 文本含 >= 2 个 markdown 标题 → 跳过 LLM。"""
    fake_model = _make_chat_model(
        StructuredText(result_text="SHOULD-NOT-RUN", summary="x")
    )
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.AUTO)
    text = "# Title 1\n\nbody 1\n\n## Title 2\n\nbody 2"
    result = await n.normalize_with_result(_raw_doc(text))
    assert result.skipped is True
    assert result.result_text == text
    fake_model.ainvoke.assert_not_called()


async def test_auto_with_single_markdown_header_invokes_llm() -> None:
    """AUTO + 仅 1 个标题 → 不算"已结构化", 调 LLM。"""
    parsed = StructuredText(result_text="# Only One Title\n\nrewritten", summary="")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.AUTO)
    result = await n.normalize_with_result(_raw_doc("# Only One Title\n\nbody"))
    assert result.skipped is False
    assert result.degraded is False
    assert result.result_text == "# Only One Title\n\nrewritten"
    fake_model.ainvoke.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 闸门 3: FORCE 模式
# ─────────────────────────────────────────────────────────────────────────────


async def test_force_mode_always_invokes_llm() -> None:
    """FORCE 模式 → 即使已有 markdown 标题, 仍调 LLM。"""
    parsed = StructuredText(result_text="LLM-replaced", summary="x")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    text = "# A\n\n## B\n\nbody"  # 即使有 2 个标题, FORCE 也调
    result = await n.normalize_with_result(_raw_doc(text))
    assert result.skipped is False
    assert result.degraded is False
    assert result.result_text == "LLM-replaced"
    fake_model.ainvoke.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 失败降级
# ─────────────────────────────────────────────────────────────────────────────


async def test_llm_exception_degrades() -> None:
    """LLM 抛任何异常 → degraded=True, text=raw, 不向上抛。"""
    fake_model = _make_chat_model(RuntimeError("llm down"))
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    result = await n.normalize_with_result(_raw_doc("important raw text"))
    assert result.degraded is True
    assert result.skipped is False
    assert result.result_text == "important raw text"
    assert result.input_tokens == 0


async def test_llm_output_parser_error_degrades() -> None:
    """OutputParserException 类异常 → 降级。"""
    fake_model = _make_chat_model(ValueError("schema mismatch"))
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.AUTO)
    result = await n.normalize_with_result(_raw_doc("no structure here"))
    assert result.degraded is True
    assert result.result_text == "no structure here"


# ─────────────────────────────────────────────────────────────────────────────
# 截断 + token 估算
# ─────────────────────────────────────────────────────────────────────────────


async def test_long_text_is_truncated_before_llm() -> None:
    """超长输入被截断到 max_input_chars 后送 LLM。"""
    parsed = StructuredText(result_text="ok", summary="x")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(
        chat_model=fake_model,
        mode=StructureMode.FORCE,
        max_input_chars=100,
    )
    long_text = "x" * 500
    await n.normalize_with_result(_raw_doc(long_text))

    # ainvoke 被调一次
    fake_model.ainvoke.assert_called_once()
    # 第二次位置参数: human 消息; 验证 human 消息里的 text 长度
    call_args = fake_model.ainvoke.call_args
    messages = call_args.args[0]  # [(role, content), ...]
    human_content = messages[1][1]
    # human 模板中包含 {text} 占位符, 被替换后的 text 部分长度应为 100
    assert long_text[:100] in human_content
    assert long_text[100:] not in human_content


async def test_token_estimation_reported() -> None:
    """input/output_tokens 字段基于字符数 // 4 估算。"""
    parsed = StructuredText(result_text="a" * 80, summary="")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    # 输入 200 chars (无 markdown, AUTO 也会调) → 估 50 input tokens
    text = "x" * 200
    result = await n.normalize_with_result(_raw_doc(text))
    assert result.input_tokens == 50
    assert result.output_tokens == 20  # 80 // 4


# ─────────────────────────────────────────────────────────────────────────────
# 副作用: report 日志
# ─────────────────────────────────────────────────────────────────────────────


async def test_report_logs_degraded_warning(caplog: pytest.LogCaptureFixture) -> None:
    """LLM 失败时 logger.warning 被调。"""
    fake_model = _make_chat_model(RuntimeError("fail"))
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    with caplog.at_level("WARNING"):
        await n.normalize(_raw_doc("hi"))
    assert any("structure.degraded" in r.message for r in caplog.records)


async def test_report_logs_skipped_info(caplog: pytest.LogCaptureFixture) -> None:
    """FORBID 跳过时 logger.info 被调。"""
    n = StructureNormalizer(chat_model=None, mode=StructureMode.FORBID)
    with caplog.at_level("INFO"):
        await n.normalize(_raw_doc("hi"))
    assert any("structure.skipped" in r.message for r in caplog.records)


async def test_report_logs_applied_info(caplog: pytest.LogCaptureFixture) -> None:
    """成功调用 LLM 时 logger.info 含 token 估算。"""
    parsed = StructuredText(result_text="out", summary="x")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    with caplog.at_level("INFO"):
        await n.normalize(_raw_doc("hi" * 100))
    matched = [r for r in caplog.records if "structure.applied" in r.message]
    assert len(matched) == 1
    assert "in=" in matched[0].message
    assert "out=" in matched[0].message


# ─────────────────────────────────────────────────────────────────────────────
# Normalize 公共 API (TextDoc 返回)
# ─────────────────────────────────────────────────────────────────────────────


async def test_normalize_returns_text_doc() -> None:
    """normalize 返回 TextDoc, text 来自 result.result_text, meta 透传。"""
    parsed = StructuredText(result_text="rewritten body", summary="x")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    raw = _raw_doc("original body")
    doc = await n.normalize(raw)
    assert doc.text == "rewritten body"
    assert doc.meta is raw.meta
    assert doc.images == []


async def test_normalize_skip_returns_same_text() -> None:
    """FORBID 模式 → normalize 返回的 text == raw.text。"""
    n = StructureNormalizer(chat_model=None, mode=StructureMode.FORBID)
    raw = _raw_doc("keep me as is")
    doc = await n.normalize(raw)
    assert doc.text == "keep me as is"
    assert doc.meta is raw.meta


# ─────────────────────────────────────────────────────────────────────────────
# NoOpNormalizer (FORBID 等价)
# ─────────────────────────────────────────────────────────────────────────────


async def test_noop_normalizer_passes_through() -> None:
    """NoOpNormalizer 不调 LLM, 透传 raw.text。"""
    n = NoOpNormalizer()
    raw = _raw_doc("plain")
    doc = await n.normalize(raw)
    assert doc.text == "plain"
    assert doc.meta is raw.meta


async def test_normalize_degraded_returns_raw_text() -> None:
    """LLM 失败降级后仍返回 TextDoc (原文), 不抛 AttributeError。"""
    fake_model = _make_chat_model(RuntimeError("fail"))
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    raw = _raw_doc("keep on degrade")
    doc = await n.normalize(raw)
    assert doc.text == "keep on degrade"
    assert doc.meta is raw.meta


async def test_normalize_preserves_format_text() -> None:
    """normalize 保留 format_text (csv/xlsx md table 视图)。"""
    parsed = StructuredText(result_text="rewritten", summary="")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    raw = TextDoc(
        text="raw",
        format_text="| a | b |",
        meta=DocMeta(filename="x.csv", datasource="file"),
    )
    doc = await n.normalize(raw)
    assert doc.format_text == "| a | b |"


async def test_noop_normalizer_does_not_modify_meta() -> None:
    """NoOpNormalizer 保留 meta 引用 (不复制)。"""
    n = NoOpNormalizer()
    raw = _raw_doc("x")
    doc = await n.normalize(raw)
    assert doc.meta.filename == "x.txt"
    assert doc.meta.datasource == "file"


# ─────────────────────────────────────────────────────────────────────────────
# 回归测试: async 化后必须 100% 走 ainvoke, 无 sync fallback
# ─────────────────────────────────────────────────────────────────────────────


async def test_normalize_uses_ainvoke_unconditionally() -> None:
    """重构后: 不论上下文中是否有 running loop, 一律 await ainvoke。无 sync 退路。"""
    parsed = StructuredText(result_text="ok", summary="")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)
    doc = await n.normalize(_raw_doc("plain text body"))
    assert doc.text == "ok"
    fake_model.ainvoke.assert_called_once()
    fake_model.invoke.assert_not_called()


async def test_normalize_does_not_warn_inside_running_loop(
    recwarn: pytest.WarningsRecorder,
) -> None:
    """嵌套 event loop 场景 (CLI 入口) 不再创建 orphan coroutine。"""
    parsed = StructuredText(result_text="ok", summary="")
    fake_model = _make_chat_model(parsed)
    n = StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORCE)

    # 模拟 CLI: asyncio.run 内嵌 pipeline._process → normalizer.normalize
    async def _drive() -> None:
        doc = await n.normalize(_raw_doc("plain text body"))
        assert doc.text == "ok"

    await _drive()  # pytest-asyncio AUTO 模式下, 本测试已在 running loop 内

    orphan_warnings = [
        w
        for w in recwarn.list
        if issubclass(w.category, RuntimeWarning)
        and "was never awaited" in str(w.message)
    ]
    assert orphan_warnings == [], (
        f"orphan coroutines leaked: {[str(w.message) for w in orphan_warnings]}"
    )
