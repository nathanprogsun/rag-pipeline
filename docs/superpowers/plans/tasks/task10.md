# Task 10: IngestPipeline (Reader → Normalizer → Chunker)

**Status**: REFACTOR → 已完成 (D5, D6, D8, D9, D10) — 2026-06-12

## 状态: 已完成 (2026-06-12 同步)

> **实际交付**(`refactor/chunker-reader` 分支):
>
> - 落地路径:
>   - `src/rag/ingest/pipeline.py` (146 行)— `IngestPipeline` 单一 `async ingest(IngestSource) -> IngestResult` 入口,内部 `_process` 串 normalizer → chunker
>   - `src/rag/ingest/source.py` (47 行)— `IngestSource = FileSource | UrlSource | BufferSource | ApiSource` tagged union (4 个 frozen dataclass)
>   - `src/rag/ingest/types.py` (152 行)— `DocMeta / TextDoc / Chunk / ChunkMetadata / IngestResult` Pydantic v2 模型,`ChunkMetadata` 扩展 4 字段 (D8: source / file_type / page_count / encoding)
>   - `src/rag/ingest/normalizer/` 子包 (4 文件:`base / no_op / structure / __init__`)— **只保留 LLM 段落改写 + NoOp** (清理 phase 删了 `api_normalizer / json_normalizer / url_normalizer`)
>   - `src/rag/ingest/cli.py` (132 行)— typer CLI:`rag-ingest [--mode file|url] TARGETS`
> - 测试:`tests/unit/ingest/` 6 个文件,合计 **16+ 测试**,覆盖率 **100%**;`test_ingest_pipeline.py` 走 `IngestPipeline.ingest(FileSource(...))` 完整路径
> - 验收:`uv run pytest tests/unit/ingest/ -v` 全过;mypy 0 错;ruff 全过
> - 后续清理 phase A:`pipeline._ensure_structure` 兜底删除,`DocumentStructure` 静态抽取路径彻底移除;文档级结构由 chunker 内部 per-chunk regex 现场重算 (`ChunkMetadata.heading_stack` / `has_code` / `has_table`)
> - 后续清理 phase B:文件头 docstring 删除 FastGPT 内部代号
> - 后续清理 phase C:README + AGENTS 文档加 Ingest 流水线章节 (Reader → Normalizer → Chunker)
> - 后续清理 phase D:`test_ingest_pipeline.py` 按入口拆 5 文件(file / csv / docx / json / 主)
> - 后续清理 phase E:`test_reader_e2e.py` + `test_chunker_e2e.py` 走真实 fixture 全链路

## 后续 review/audit 影响 (2026-06-13 同步)

> 本 task 在 2026-06-12 同步后又经历 3 轮 review/audit 修改,全部落地到 `refactor/chunker-reader` 分支。
>
> - **R-Audit #6 (CLI 异常路径)**: `rag-ingest` 三子命令 (`ingest / ingest-url / ingest-buffer`) 加 try/except + 友好错误码 (ReaderError / NormalizerError / ChunkerError 分组),失败时返回非零 exit + 错误描述到 stderr,不再 traceback 满屏
> - **PAudit-2 (pipeline async + title 时序)**: `pipeline._process` 改 `async def`,内嵌 normalizer / chunker await 链路;`FileSource / UrlSource / BufferSource` 加 `title` 字段,默认从 `filename` 提取,Pipeline 顶部 await reader 拿到后回填 `IngestResult.title`
> - **PAudit-5 (RetrievalTrace)**: 新增 `RetrievalTrace` dataclass,IngestResult 旁路挂载,记录 source / file_type / page_count / encoding + normalizer 调用耗时 + chunker 调用耗时,供下游 audit 节点消费
>
> 当前 task10 相关累计:**16+ unit 测试 + 100% 覆盖率 + 1 个 `async ingest(IngestSource)` 统一入口 + 3 子命令 CLI**,mypy 0 错 / ruff 全过。

> **历史溯源**(本 task 原始设计):原 plan 写 IngestPipeline 串 reader + chunker,后接 PG 事务嵌 embed。重构后关键变更(D5/D6/D8/D9/D10 + Phase 8 清理 + PAudit-2),原描述保留在下方,作为偏差溯源依据。
> 1. **新增 Normalizer 段** (D6): FastGPT `requestLLMPargraph` 对位,三道闸门
> 2. **DocMeta 全字段注入** (D8): 通过 ChunkContext 传入 Chunker
> 3. **四 source 类型 + 单一 `async ingest` 入口** (D9, D10): `FileSource / UrlSource / BufferSource / ApiSource`
> 4. **删除 structure 预抽阶段** (Phase 8): 文档级结构由 chunker 内部 per-chunk regex 重算
> 5. **title 时序前置** (PAudit-2): normalize 之前抽 title,避免 normalizer 改写 / 删除原 H1

**Files (post-cleanup state):**
- Create: `src/rag/ingest/source.py`  (IngestSource tagged union, 4 frozen dataclass)
- Modify: `src/rag/ingest/pipeline.py`  (★ 完整重写: 串 normalizer + chunker + 4 入口 → 单一 `async ingest`)
- Modify: `src/rag/ingest/types.py`  (IngestResult 模型, 增加 title / doc_meta / warnings)
- Modify: `src/rag/ingest/normalizer/__init__.py`  (导出 Normalizer / NoOp / Structure*)
- Create: `src/rag/ingest/normalizer/base.py`  (Normalizer 基类)
- Create: `src/rag/ingest/normalizer/no_op.py`  (NoOpNormalizer)
- Create: `src/rag/ingest/normalizer/structure.py`  (★ StructureNormalizer, 3 闸门)
- Create: `tests/unit/test_normalizer_structure.py`  (16 测试)
- Create: `tests/unit/test_normalizer_noop.py`
- Modify: `tests/unit/test_ingest_pipeline.py`  (走 `async ingest(IngestSource)` 完整路径)
- Create: `tests/unit/ingest/test_ingest_pipeline.py` + file / csv / docx / json 主路径

> 历史: `api_normalizer / json_normalizer / url_normalizer` 已下沉为 reader adapter 的一部分,不再属于 Normalizer。`structure/` 目录已删除 (Phase 8 清理);文档级结构改由 chunker 内部 per-chunk regex 现场重算。

---

## 重构后 IngestPipeline 架构

```
                          IngestPipeline
                                │
                                ▼
                     async ingest(IngestSource)  ← 单一入口
                                │
       ┌──────────┬──────────┬──────────┐
       │          │          │          │
   FileSource  UrlSource  BufferSource  ApiSource
       │ (async)  │ (async)  │ (async)    │ (async, internal httpx)
       ▼          ▼          ▼             ▼
   dispatch_   read_url   dispatch_      _fetch_api
   bytes                    bytes        + JSON 字段抽取
       │          │          │             │
       └──────────┴──────────┴─────────────┘
                                ▼
                          TextDoc { text, meta }
                                │
                                ▼
                    [optional] Normalizer.normalize()
                                │   FORBID / no-model / auto+md  → 跳过 (skipped)
                                ▼
                          TextDoc { text = result_text }
                                │
                                ▼
                          Chunker.split(text, ctx=ChunkContext)
                                │   ← heading_stack / has_code / has_table
                                │     由 chunker 内部 per-chunk regex 现场重算
                                ▼
                          list[Chunk]  ← 含 DocMeta 注入
                                │
                                ▼
                          IngestResult { chunks, title, doc_meta, warnings }
```

**关键边界** (FastGPT 对齐):
- Reader **不感知** Normalizer 存在
- Normalizer **不感知** Chunker / Reader 实现
- Chunker **只看 string**,不接收文件 / URL / MIME
- Pipeline 是**唯一**知道如何串联四段入口 + 三阶段处理的对象

---

## Step 0: Stub (确保模块可 import)

```python
# src/rag/ingest/normalizer/__init__.py
from .api_normalizer import ApiNormalizer
from .json_normalizer import JsonNormalizer
from .url_normalizer import UrlNormalizer
from .no_op import NoOpNormalizer
# StructureNormalizer 在 Step 3 实现
```

```python
# src/rag/ingest/normalizer/base.py
from rag.ingest.types import RawDoc, TextDoc


class Normalizer:
    def normalize(self, raw: RawDoc) -> TextDoc:
        raise NotImplementedError
```

```python
# src/rag/ingest/normalizer/structure.py (stub)
class StructureMode: pass

class StructureNormalizer:
    def __init__(self, **kwargs): pass
    def normalize(self, raw): raise NotImplementedError
```

```python
# src/rag/ingest/pipeline.py (stub, 已废)
class IngestPipeline:
    def __init__(self, chunker, normalizer=None): pass
    async def ingest(self, source): raise NotImplementedError  # 单一入口最终签名
```

---

## Step 1: 写失败测试 (Normalizer 16 个 + Pipeline 16 个)

```python
# tests/unit/test_normalizer_structure.py
from unittest.mock import AsyncMock, MagicMock
import pytest
from langchain_core.runnables import Runnable
from rag.ingest.normalizer import (
    NoOpNormalizer, ResultDocument, StructureMode, StructureNormalizer, StructuredText,
)
from rag.ingest.types import DocMeta, RawDoc


def _make_chat_model(result):  # MagicMock with async ainvoke
    m = MagicMock()
    if isinstance(result, Exception):
        m.ainvoke = AsyncMock(side_effect=result)
        m.invoke = MagicMock(side_effect=result)
    else:
        m.ainvoke = AsyncMock(return_value=result)
        m.invoke = MagicMock(return_value=result)
    return m


def _raw(text="plain"): return RawDoc(text=text, meta=DocMeta(filename="x.txt", datasource="file"))


# ── 闸门 1: FORBID / no-model ──
def test_forbid_mode_skips():
    parsed = StructuredText(result_text="OUT", summary="x")
    n = StructureNormalizer(chat_model=_make_chat_model(parsed), mode=StructureMode.FORBID)
    r = n.normalize_with_result(_raw("hello"))
    assert r.skipped and not r.degraded and r.result_text == "hello"
    n._chat_model.ainvoke.assert_not_called()


def test_none_chat_model_skips():
    n = StructureNormalizer(chat_model=None, mode=StructureMode.AUTO)
    r = n.normalize_with_result(_raw("hello"))
    assert r.skipped and r.result_text == "hello"


# ── 闸门 2: AUTO + md 标题 ──
def test_auto_with_markdown_headers_skips():
    parsed = StructuredText(result_text="SHOULD-NOT-RUN", summary="x")
    n = StructureNormalizer(chat_model=_make_chat_model(parsed), mode=StructureMode.AUTO)
    r = n.normalize_with_result(_raw("# T1\n\nbody\n\n## T2\n\nbody2"))
    assert r.skipped
    n._chat_model.ainvoke.assert_not_called()


def test_auto_with_single_header_invokes_llm():
    parsed = StructuredText(result_text="rewritten", summary="")
    n = StructureNormalizer(chat_model=_make_chat_model(parsed), mode=StructureMode.AUTO)
    r = n.normalize_with_result(_raw("# only\n\nbody"))
    assert not r.skipped and r.result_text == "rewritten"
    n._chat_model.ainvoke.assert_called_once()


# ── 闸门 3: FORCE / 失败降级 ──
def test_force_always_invokes_llm():
    parsed = StructuredText(result_text="LLM", summary="x")
    n = StructureNormalizer(chat_model=_make_chat_model(parsed), mode=StructureMode.FORCE)
    r = n.normalize_with_result(_raw("# A\n\n## B\n\nbody"))
    assert r.result_text == "LLM" and not r.skipped
    n._chat_model.ainvoke.assert_called_once()


def test_llm_exception_degrades():
    n = StructureNormalizer(chat_model=_make_chat_model(RuntimeError("down")), mode=StructureMode.FORCE)
    r = n.normalize_with_result(_raw("important"))
    assert r.degraded and r.result_text == "important"


def test_long_text_is_truncated_before_llm(monkeypatch):
    parsed = StructuredText(result_text="ok", summary="x")
    n = StructureNormalizer(chat_model=_make_chat_model(parsed), mode=StructureMode.FORCE, max_input_chars=100)
    n.normalize_with_result(_raw("x" * 500))
    call_args = n._chat_model.ainvoke.call_args
    messages = call_args.args[0]
    human_content = messages[1][1]
    assert "x" * 100 in human_content
    assert "x" * 101 not in human_content


def test_token_estimation_reported():
    parsed = StructuredText(result_text="a" * 80, summary="")
    n = StructureNormalizer(chat_model=_make_chat_model(parsed), mode=StructureMode.FORCE)
    r = n.normalize_with_result(_raw("x" * 200))
    assert r.input_tokens == 50 and r.output_tokens == 20


# ── 副作用: report 日志 ──
def test_report_logs_degraded_warning(caplog):
    n = StructureNormalizer(chat_model=_make_chat_model(RuntimeError()), mode=StructureMode.FORCE)
    with caplog.at_level("WARNING"):
        n.normalize(_raw("hi"))
    assert any("structure.degraded" in r.message for r in caplog.records)


# ── TextDoc 返回 ──
def test_normalize_returns_text_doc():
    parsed = StructuredText(result_text="rewritten", summary="x")
    n = StructureNormalizer(chat_model=_make_chat_model(parsed), mode=StructureMode.FORCE)
    raw = _raw("original")
    doc = n.normalize(raw)
    assert doc.text == "rewritten" and doc.meta is raw.meta


# ── NoOpNormalizer ──
def test_noop_normalizer_passes_through():
    n = NoOpNormalizer()
    raw = _raw("plain")
    doc = n.normalize(raw)
    assert doc.text == "plain" and doc.meta is raw.meta
```

```python
# tests/unit/ingest/test_ingest_pipeline.py (post-cleanup, 反映实际实现)
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.normalizer import NoOpNormalizer
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.source import (
    ApiSource, BufferSource, FileSource, UrlSource,
)


@pytest.fixture
def pipeline_e2e() -> IngestPipeline:
    return IngestPipeline(
        chunker=Chunker(ChunkSettings(chunk_size=200, max_chunk_size=800, min_chunk_size=50)),
        normalizer=NoOpNormalizer(),
    )


def _assert_doc_meta_injected(chunks, file_type, source_suffix):
    for c in chunks:
        assert c.metadata.file_type == file_type
        assert c.metadata.source.endswith(source_suffix)
        assert c.metadata.encoding in ("utf-8", "utf8")


@pytest.mark.asyncio
async def test_pipeline_file_txt(pipeline_e2e, sample_txt):
    result = await pipeline_e2e.ingest(FileSource(path=sample_txt))
    _assert_doc_meta_injected(result.chunks, "txt", "sample.txt")
    # title 来自文件名兜底 (无 H1)
    assert result.title == sample_txt.name
    assert result.doc_meta.datasource == "file"


@pytest.mark.asyncio
async def test_pipeline_file_md_extracts_h1(pipeline_e2e, sample_md):
    result = await pipeline_e2e.ingest(FileSource(path=sample_md))
    _assert_doc_meta_injected(result.chunks, "md", "sample.md")
    # title 从第一行 `#` 抽
    assert result.title == "Sample Markdown"
    # chunker 内部 regex 现场重算 heading_stack / has_code / has_table
    assert any("Sample Markdown" in h for c in result.chunks for h in c.metadata.heading_stack)


@pytest.mark.asyncio
async def test_pipeline_file_pdf_injects_page_count(pipeline_e2e, sample_pdf):
    result = await pipeline_e2e.ingest(FileSource(path=sample_pdf))
    for c in result.chunks:
        assert c.metadata.page_count == 3


@pytest.mark.asyncio
async def test_pipeline_file_html_extracts_h1(pipeline_e2e, sample_html):
    result = await pipeline_e2e.ingest(FileSource(path=sample_html))
    assert result.title == "Sample HTML"
    assert any("Sample HTML" in h for c in result.chunks for h in c.metadata.heading_stack)


@pytest.mark.asyncio
async def test_pipeline_buffer_md(pipeline_e2e):
    md = b"# Inline\n\nbody."
    src = BufferSource(buf=md, file_type="md", source="inline://x.md")
    result = await pipeline_e2e.ingest(src)
    _assert_doc_meta_injected(result.chunks, "md", "x.md")
    assert result.title == "Inline"
    assert result.doc_meta.source == "inline://x.md"


@pytest.mark.asyncio
async def test_pipeline_url_html(pipeline_e2e):
    html = b"<html><body><h1>Web</h1><p>body.</p></body></html>"
    resp = MagicMock()
    resp.text = html.decode()
    resp.content = html
    resp.headers = {"content-type": "text/html; charset=utf-8"}
    resp.url = "https://example.com/page.html"
    resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mc:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.head = AsyncMock(return_value=MagicMock(headers={}))
        mock_client.get = AsyncMock(return_value=resp)
        mc.return_value = mock_client

        result = await pipeline_e2e.ingest(UrlSource(url="https://example.com/page.html"))

    _assert_doc_meta_injected(result.chunks, "html", "page.html")
    assert result.title == "Web"
    assert result.doc_meta.source == "https://example.com/page.html"


@pytest.mark.asyncio
async def test_pipeline_api_json(pipeline_e2e):
    body = b'{"text": "Hello from API", "metadata": {"x": 1}}'
    resp = MagicMock()
    resp.status_code = 200
    resp.content = body

    http_client = AsyncMock()
    http_client.get = AsyncMock(return_value=resp)
    http_client.aclose = AsyncMock(return_value=None)

    src = ApiSource(
        server_url="https://api.example.com",
        endpoint="/v1/items",
        auth_token="tkn",
        http_client=http_client,
    )
    result = await pipeline_e2e.ingest(src)
    assert result.chunks
    assert result.doc_meta.datasource == "api"
    assert result.doc_meta.source == "https://api.example.com/v1/items"
    # 抽到 text 字段
    assert "Hello from API" in result.chunks[0].text


def test_pipeline_without_normalizer_uses_noop():
    p = IngestPipeline(chunker=Chunker(ChunkSettings(chunk_size=200)))
    assert isinstance(p.normalizer, NoOpNormalizer)


def test_pipeline_with_forbid_normalizer_does_not_call_llm(tmp_path):
    from langchain_core.runnables import Runnable
    from rag.ingest.normalizer import StructureMode, StructureNormalizer

    fake_model = MagicMock(spec=Runnable)
    p = IngestPipeline(
        chunker=Chunker(ChunkSettings(chunk_size=200)),
        normalizer=StructureNormalizer(chat_model=fake_model, mode=StructureMode.FORBID),
    )
    f = tmp_path / "doc.txt"
    f.write_text("hello content for testing pipeline normalizer integration", encoding="utf-8")

    import asyncio
    asyncio.run(p.ingest(FileSource(path=f)))
    fake_model.ainvoke.assert_not_called()
```

---

## Step 2: 跑测试,确认 fail (RED)

```bash
uv run pytest tests/unit/test_normalizer_structure.py tests/unit/test_ingest_pipeline.py -v
# 期望: 32+ 个 RED (NotImplementedError,无 ImportError)
```

---

## Step 3: 实现 StructureNormalizer (3 闸门 + 失败降级)

```python
# src/rag/ingest/normalizer/structure.py
import asyncio
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field
from rag.ingest.normalizer.base import Normalizer
from rag.ingest.types import RawDoc, TextDoc

if TYPE_CHECKING:
    from rag.ingest.types import DocMeta

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS: int = 50_000
_LLM_TIMEOUT_SEC: float = 600.0
# FastGPT 对齐: 至少 2 个 markdown 标题才算"已结构化"
_HEADING_RE: re.Pattern[str] = re.compile(r"^#{1,5}\s+\S+", re.MULTILINE)


class StructureMode(str, Enum):
    FORBID = "forbid"
    AUTO = "auto"
    FORCE = "force"


class StructuredText(BaseModel):
    """LLM 结构化输出 schema (with_structured_output method=function_calling)。"""
    result_text: str = Field(..., description="重整后的 Markdown 文本")
    summary: str = Field(default="")


@dataclass(frozen=True)
class ResultDocument:
    result_text: str
    raw_text: str
    input_tokens: int
    output_tokens: int
    skipped: bool
    degraded: bool


_SYSTEM_PROMPT = """你是技术文档编辑。把用户提供的非结构化文本重整为层次清晰的 Markdown。
要求:
1. 使用 # / ## / ### 三级标题分章节, 保留原文事实, 不总结。
2. 代码块、列表、表格保留语义。
3. 只输出 Markdown 正文, 不要解释。"""


_HUMAN_TEMPLATE = """请把下面的文本重整为 Markdown 章节。

---
【示例 1】原文: FastGPT 是一个基于 LLM 的知识库平台。它支持向量检索。
输出: # FastGPT 概述\n\nFastGPT 是一个基于 LLM 的知识库平台。\n\n## 核心功能\n\n### 向量检索
---

【待重整文本】
{text}
"""


class StructureNormalizer(Normalizer):
    """基于 LLM 的三道闸门结构化归一化器 (FastGPT 对齐)。"""

    def __init__(self, *, chat_model: Runnable | None = None,
                 mode: StructureMode = StructureMode.AUTO,
                 max_input_chars: int = MAX_INPUT_CHARS,
                 llm_timeout_sec: float = _LLM_TIMEOUT_SEC) -> None:
        self._chat_model = chat_model
        self._mode = mode
        self._max_input_chars = max_input_chars
        self._llm_timeout_sec = llm_timeout_sec

    def normalize(self, raw: RawDoc) -> TextDoc:
        result = self._run_pipeline(raw.text)
        self._report(result, meta=raw.meta)
        return TextDoc(text=result.result_text, meta=raw.meta, structure=None, images=[])

    def normalize_with_result(self, raw: RawDoc) -> ResultDocument:
        return self._run_pipeline(raw.text)

    def _run_pipeline(self, raw_text: str) -> ResultDocument:
        # 闸门 1
        if self._mode is StructureMode.FORBID or self._chat_model is None:
            return ResultDocument(raw_text, raw_text, 0, 0, skipped=True, degraded=False)
        # 闸门 2
        if self._mode is StructureMode.AUTO and self._looks_structured(raw_text):
            return ResultDocument(raw_text, raw_text, 0, 0, skipped=True, degraded=False)
        # 闸门 3
        return self._invoke_llm(raw_text)

    @staticmethod
    def _looks_structured(text: str) -> bool:
        return len(_HEADING_RE.findall(text)) > 1

    def _invoke_llm(self, raw_text: str) -> ResultDocument:
        truncated = self._truncate(raw_text)
        prompt = self._build_prompt(truncated)
        try:
            parsed = self._call_llm(prompt)
            return ResultDocument(parsed.result_text, raw_text,
                                 len(truncated) // 4, len(parsed.result_text) // 4,
                                 skipped=False, degraded=False)
        except Exception as exc:
            logger.warning("StructureNormalizer LLM failed, degrading: %s", exc)
            return ResultDocument(raw_text, raw_text, 0, 0, skipped=False, degraded=True)

    def _call_llm(self, prompt):
        try:
            return asyncio.run(asyncio.wait_for(
                self._chat_model.ainvoke(prompt), timeout=self._llm_timeout_sec,
            ))
        except RuntimeError:
            return self._chat_model.invoke(prompt)

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_input_chars:
            return text
        logger.info("StructureNormalizer truncating input: %d -> %d chars",
                    len(text), self._max_input_chars)
        return text[:self._max_input_chars]

    def _build_prompt(self, text: str) -> list:
        return [("system", _SYSTEM_PROMPT), ("human", _HUMAN_TEMPLATE.format(text=text))]

    @staticmethod
    def _report(result: ResultDocument, *, meta: "DocMeta | None" = None) -> None:
        filename = getattr(meta, "filename", None) if meta else None
        if result.degraded:
            logger.warning("structure.degraded filename=%s", filename)
        elif result.skipped:
            logger.info("structure.skipped filename=%s", filename)
        else:
            logger.info("structure.applied filename=%s in=%d out=%d",
                        filename, result.input_tokens, result.output_tokens)
```

---

## Step 4: 重写 IngestPipeline (★ 完整重写)

```python
# src/rag/ingest/pipeline.py (post-cleanup, 反映实际实现)
"""IngestPipeline: 单一 ``ingest(IngestSource) -> IngestResult`` 入口。

设计要点:
- 删除 ``_ensure_structure`` 兜底 + ``_TEXT_STRUCTURE_EXTRACTORS`` 表
  (旧 `structure/` 子包下的各 extractor 类已删除)。
- 文档级结构不再由 reader / pipeline 抽取, 改由 chunker 内部 per-chunk
  regex (_MD_HEADING_RE / _HTML_HEADING_RE / _TABLE_RE / _CODE_FENCE_RE)
  现场重算 heading_stack / has_code / has_table / image_refs。
- doc-level identifier 推导:
    * title: 优先 text 内第一行非空 `#` / `<h1>` 标题, 兜底 ``meta.filename``
    * page_count / paragraph_count: 从 ``meta`` 透传
    * warnings: 收集非致命降级信号
- FileSource 路径走 ``await dispatch_bytes`` 直接, 不再调 ``read_file`` 包装
  (避免 ``asyncio.run`` 嵌套导致 ``RuntimeError``)。
- ``ingest`` 新增 ``get_format_text: bool = True``: 透传给 chunker, 决定
  ``Chunk.text`` 是 ``format_text`` (csv/xlsx 的 md table) 还是 ``raw_text``。
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.chunker import Chunker
from rag.ingest.chunker.types import ChunkContext
from rag.ingest.normalizer import NoOpNormalizer, Normalizer
from rag.ingest.reader import dispatch_bytes, read_url
from rag.ingest.source import (
    ApiSource,
    BufferSource,
    FileSource,
    IngestSource,
    UrlSource,
)
from rag.ingest.types import Chunk, DocMeta, IngestResult, TextDoc

logger = logging.getLogger(__name__)

# doc-level title 抽取: 优先 Markdown `# title` 或 HTML `<h1>title</h1>` 第一项。
_TITLE_MD_RE = re.compile(r"^#{1,5}\s+(.+)$", re.MULTILINE)
_TITLE_HTML_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)


def _extract_title(text: str) -> str | None:
    """从纯文本中抽第一行 # 标题或 <h1>, 失败返回 None。"""
    m = _TITLE_MD_RE.search(text)
    if m:
        title = m.group(1).strip()
        if title:
            return title
    m = _TITLE_HTML_RE.search(text)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title:
            return title
    return None


def _derive_title(text_doc: TextDoc, warnings: list[str]) -> str | None:
    """doc-level title 推导: 文本内 # / <h1> 第一项, 兜底 filename。"""
    title = _extract_title(text_doc.text)
    if title:
        return title
    if text_doc.meta.filename:
        return text_doc.meta.filename
    warnings.append("title unavailable: no heading and no filename")
    return None


def _build_context(text_doc: TextDoc) -> ChunkContext:
    """组装 Chunker 入参上下文: 仅 DocMeta (structure / heading_path 已删)。"""
    return ChunkContext.from_meta(meta=text_doc.meta)


class IngestPipeline:
    def __init__(
        self,
        chunker: Chunker,
        normalizer: Normalizer | None = None,
        *,
        max_url_size: int = 1_000_000_000,
        url_timeout_s: float = 600.0,
    ) -> None:
        self.chunker = chunker
        self.normalizer: Normalizer = normalizer or NoOpNormalizer()
        self._max_url_size = max_url_size
        self._url_timeout_s = url_timeout_s

    # ── 单一入口 ─────────────────────────────────────────────
    async def ingest(
        self,
        source: IngestSource,
        *,
        get_format_text: bool = True,
    ) -> IngestResult:
        """IngestSource -> IngestResult: 全程 async。

        ``source``: ``FileSource`` / ``UrlSource`` / ``BufferSource`` / ``ApiSource`` 四选一。
        CLI 入口负责 ``asyncio.run(pipeline.ingest(...))`` 并统一加 tqdm / logger。
        """
        if isinstance(source, FileSource):
            text_doc = await self._read_file(source)
        elif isinstance(source, UrlSource):
            text_doc = await read_url(
                source.url, max_size=source.max_size, timeout_s=source.timeout_s,
            )
        elif isinstance(source, BufferSource):
            filename = source.source.rsplit("/", 1)[-1]
            if "." not in filename:
                filename = f"{filename}.{source.file_type.lstrip('.')}"
            text_doc = await dispatch_bytes(
                buffer=source.buf, extension=source.file_type,
                source=source.source, datasource="api", filename=filename,
            )
        elif isinstance(source, ApiSource):
            text_doc = await self._fetch_api(source)
        else:
            raise TypeError(f"unsupported IngestSource: {type(source).__name__}")

        return await self._process(text_doc, get_format_text=get_format_text)

    async def _read_file(self, source: FileSource) -> TextDoc:
        """FileSource -> TextDoc: 直接走 ``await dispatch_bytes`` (避开 asyncio.run 嵌套)。"""
        p = source.path
        if not p.exists():
            raise RAGError(code=ReaderErrorCode.NOT_FOUND, message=f"{p}: file does not exist")
        if not p.is_file():
            raise RAGError(code=ReaderErrorCode.NOT_FOUND, message=f"{p}: not a regular file")
        try:
            buffer = p.read_bytes()
        except PermissionError as e:
            raise RAGError(code=ReaderErrorCode.PERMISSION, message=f"{p}: {e}") from e
        except OSError as e:
            raise RAGError(code=ReaderErrorCode.PARSE, message=f"{p}: {e}") from e
        return await dispatch_bytes(
            buffer=buffer, extension=p.suffix,
            source=f"file://{p.resolve()}", datasource="file", filename=p.name,
        )

    async def _fetch_api(self, source: ApiSource) -> TextDoc:
        """ApiSource -> TextDoc: 拉 JSON, 按 field_priority 抽字段。"""
        # 错误码细分 (便于排查):
        #   READER_API_AUTH     -> 401/403
        #   READER_API_TIMEOUT  -> httpx.TimeoutException
        #   READER_API_STATUS   -> 其他非 2xx
        #   READER_PARSE        -> JSON 解析失败 / 字段抽取全部为空
        url = source.server_url.rstrip("/") + "/" + source.endpoint.lstrip("/")
        headers = {"Accept": "application/json"}
        if source.auth_token:
            headers["Authorization"] = f"Bearer {source.auth_token}"
        timeout = httpx.Timeout(connect=10.0, read=source.timeout_s, write=10.0, pool=10.0)

        owns_client = source.http_client is None
        client: httpx.AsyncClient
        if owns_client:
            client = httpx.AsyncClient(follow_redirects=True, timeout=timeout)
        else:
            client = source.http_client  # type: ignore[assignment]

        try:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.TimeoutException as e:
                raise RAGError(code=ReaderErrorCode.API_TIMEOUT, message=f"{url}: api timeout: {e}") from e
            except httpx.HTTPError as e:
                raise RAGError(code=ReaderErrorCode.API_STATUS, message=f"{url}: api request failed: {e}") from e

            status = resp.status_code
            if status in (401, 403):
                raise RAGError(code=ReaderErrorCode.API_AUTH, message=f"{url}: api auth failed: HTTP {status}")
            if status >= 400:
                raise RAGError(code=ReaderErrorCode.API_STATUS, message=f"{url}: api status: HTTP {status}")

            body = resp.content
            if len(body) > source.max_size:
                raise RAGError(code=ReaderErrorCode.TOO_LARGE, message=f"{url}: api response too large: {len(body)} > {source.max_size}")

            try:
                data = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise RAGError(code=ReaderErrorCode.PARSE, message=f"{url}: api JSON parse failed: {e}") from e
        finally:
            if owns_client:
                await client.aclose()

        extracted = _extract_api_field(data, source.field_priority)
        if not extracted:
            raise RAGError(code=ReaderErrorCode.PARSE, message=f"{url}: api JSON had no non-empty field in {source.field_priority}")

        return TextDoc(
            text=extracted,
            meta=DocMeta(datasource="api", source=url, mime="application/json", size_bytes=len(body)),
        )

    # ── 内部统一: 两段串联 ─────────────────────────────────────
    async def _process(
        self, text_doc: TextDoc, *, get_format_text: bool = True
    ) -> IngestResult:
        warnings: list[str] = []

        # 先在 normalize 之前抽 title, 防 normalizer 改写 / 删除原 H1。
        pre_normalize_title = _derive_title(text_doc, warnings)

        # Step 1: Normalizer (可选, 内部失败降级)
        text_doc = await self.normalizer.normalize(text_doc)
        text = text_doc.text

        # Step 2: Chunker (注入 ctx 含 DocMeta)
        ctx = _build_context(text_doc)
        chunks: list[Chunk] = self.chunker.split(
            text, ctx=ctx,
            format_text=text_doc.format_text,
            get_format_text=get_format_text,
        )

        # normalize 后再抽一次, 兜底 normalizer 自己注入新 H1 的场景;
        # 优先级: 原始 H1 > 改写后 H1 > filename (来自 _derive_title 内部兜底)。
        post_normalize_title = _derive_title(text_doc, warnings)
        title = pre_normalize_title or post_normalize_title

        return IngestResult(
            chunks=chunks, title=title, doc_meta=text_doc.meta, warnings=warnings,
        )
```

---

## Step 5: 跑全部测试 (32+ Normalizer/Pipeline 测试全过)

```bash
uv run pytest tests/unit/test_normalizer_structure.py tests/unit/test_ingest_pipeline.py tests/unit/test_chunker_*.py tests/unit/test_reader_*.py -v
# 期望: 130+ passed
```

---

## Step 6: commit

```bash
git add src/rag/ingest/normalizer/ src/rag/ingest/pipeline.py tests/
git commit -m "feat(ingest): StructureNormalizer (3 gates + degrade) + IngestPipeline (file/url/buffer) (D6, D9, D10)"
```

---

## Deviation Notes (D5, D6, D8, D9, D10 + post-cleanup)

- **(D5) Reader 输出 `TextDoc`**: 旧 `(text, DocMeta)` 改为 `TextDoc { text, meta, format_text, images }`,Pipeline 处理单一类型。
- **(D6) 新增 Normalizer 段**: FastGPT `requestLLMPargraph` 对位,三道闸门 (FORBID / AUTO+md-skip / FORCE) + 失败降级。**任何 LLM 异常 → 降级到 raw_text,不抛出**(用户友好优先于 FastGPT 硬失败)。
- **(D8) `ChunkMetadata` 扩展 4 字段**: `source / file_type / page_count / encoding` 来自 DocMeta,通过 `ChunkContext.from_meta` 注入每块。
- **(D9) 四 source 类型**: `FileSource` / `UrlSource` / `BufferSource` / `ApiSource` 收敛为 `IngestSource` tagged union。FastGPT `readDatasetSourceRawText` 区分 4 source type,库场景不引入 worker pool。
- **(D10) 单一 `async ingest(IngestSource)` 入口**: 内部按 isinstance 分发到 `_read_file` / `read_url` / `dispatch_bytes` / `_fetch_api`。CLI 端用 `asyncio.run(pipeline.ingest(...))` 统一启动;Batch 场景用 `asyncio.gather` 并发。
- **(Phase 8 清理) 删除 `structure/` 目录**: 原 `structure/` 子包下所有 extractor 类 (md / html / pdf / docx) 及 `_STRUCTURE_EXTRACTORS` 表、`_extract_structure` 兜底、`_ensure_structure` 全部下线。文档级结构改由 chunker 内部 per-chunk regex (`_MD_HEADING_RE` / `_HTML_HEADING_RE` / `_TABLE_RE` / `_CODE_FENCE_RE`) 现场重算。
- **(PAudit-2) 整条 ingest 改 async**: `ingest` / `_process` / `_read_file` / `_fetch_api` / `normalizer.normalize` 全栈 await,`_process` 内串 normalizer → chunker 无 I/O 边界。
- **(PAudit-2) title 在 normalize 之前抽**: `_derive_title` 在 normalize 前后各调一次,优先级 `pre_normalize_title > post_normalize_title > filename`,避免 normalizer 改写 / 删除原 H1 丢 title。
- **(Phase 6) `get_format_text: bool = True` 透传 chunker**: 决定 `Chunk.text` 是 `format_text` (csv/xlsx 的 md table) 还是 `raw_text`,默认 True。
- **R-Audit #6 CLI 异常路径**: `_render_error` 保留 `RAGError.code` 字段,失败时返回非零 exit + 错误描述到 stderr,不再 traceback 满屏。
- **R-Audit `_read_file` 改走 dispatch_bytes**: 不再调 `read_file` (那里 `asyncio.run` 会与外层 event loop 冲突),FileSource 直接 `await dispatch_bytes(buffer=..., extension=p.suffix, ...)`。
