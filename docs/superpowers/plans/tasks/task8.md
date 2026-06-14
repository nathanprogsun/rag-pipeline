# Task 8: Reader (dispatch + 7 adapters) + Structure

**Status**: REFACTOR → 已完成 (D1, D2, D7, D8) — 2026-06-12

## 状态: 已完成 (2026-06-12 同步)

> **实际交付**(`refactor/chunker-reader` 分支):
>
> - 落地路径:`src/rag/ingest/reader/` 子包
>   - `__init__.py` — 公共 API:`read_file / read_url / dispatch_bytes / EXTENSION_ADAPTERS / FormatReaderResult / TextDoc / DocMeta`
>   - `types.py` — `FormatReaderResult` dataclass + `UploadFileHandler` / `UploadedFileResult` TypedDict
>   - `dispatch.py` — `EXTENSION_ADAPTERS` 表 + `async dispatch_bytes(buffer, ext, source, datasource, filename, upload_file)` 内部 helper
>   - `file.py` — `read_file` 同步入口(内部 `asyncio.run(dispatch_bytes(...))`)
>   - `url.py` — `read_url` 异步入口(httpx + content-type 推断)
>   - `extensions/` — 8 个 async buffer-based adapter:`text.py` (txt + md 共享) / `html.py` (html2md 走 markdown) / `pdf.py` (pypdf) / `docx.py` (mammoth) / `pptx.py` (parse_office / python-zipfile) / `xlsx.py` (openpyxl 双轨) / `csv.py` (双轨:raw_text + format_text md table) / `base.py` (Protocol + `wrap_*_error`)
>   - 共享辅助模块:`raw_text.py` / `html2md.py` / `pdf_text_postprocess.py` / `parse_office.py`
> - 实际 dispatch 槽位:**9 项** (txt / md / html / htm / pdf / docx / pptx / csv / xlsx),`md` 与 `htm` 分别走 `text_adapter` 与 `html_adapter` 别名
> - **没有 json adapter**(FastGPT 也不支持,Phase 5/8 删除);**没有 api_response adapter**(Phase 3 下沉,Phase 8C 独立成 ApiSource)
> - 测试:`tests/unit/reader/` 14 个文件,合计 **48+ 测试**;`uv run pytest tests/unit/reader/ -v` 全过
> - 后续清理 phase A:**删除** `src/rag/ingest/structure/` 目录(静态抽取冗余);doc-level structure 不再由 reader 抽取,chunker 内部 per-chunk regex 现场重算 heading_stack

## 后续 review/audit 影响 (2026-06-13 同步)

> 本 task 在 2026-06-12 同步后又经历 3 轮 review/audit 修改,全部落地到 `refactor/chunker-reader` 分支。
>
> - **R-Audit** (6 个 P0/P1 问题):`FormatReaderResult` 单源化到 `reader/types.py`,删除备用 `ResultDocument` 别名;`Datasource` 删 `Enum` 只留 `Literal["file","url","api"]`;`RawDoc` 别名删除
> - **PAudit-3**: `dispatch.py` 删除 `inspect.getsource` 反射调用,改显式 `EXTENSION_ADAPTERS` 字典查找,启动期 fail-fast
> - **Pptx 测试修复**: 7 个 `def test_*` → `async def test_*` + `await dispatch_bytes(...)`,消除 pytest-asyncio deprecation warning(测试文件 `tests/unit/reader/test_extensions_pptx.py`)
>
> 当前 task8 相关累计:**48+ unit 测试 + 9 dispatch 槽位**,mypy 0 错 / ruff 全过。

> **历史溯源**(本 task 原始设计):原 plan 写 `reader.py` + `structure.py` 单一文件 + path-based registry 模式。调研 FastGPT `readFileContentByBuffer` 后改为 **dispatch + 7 个 buffer-based adapters** 架构,后缀分发完全在 `dispatch.py` 内部,公共 API 仅暴露 `read_file` / `read_url`。原描述保留在下方,作为 D1/D2/D7/D8 偏差的溯源依据。

**Files (实际落地):**
- Create: `src/rag/ingest/reader/__init__.py`  (公共 API)
- Create: `src/rag/ingest/reader/types.py`  (FormatReaderResult dataclass + UploadFileHandler TypedDict)
- Create: `src/rag/ingest/reader/dispatch.py`  (EXTENSION_ADAPTERS + async dispatch_bytes)
- Create: `src/rag/ingest/reader/file.py`  (read_file 同步入口,`asyncio.run` 包装 dispatch)
- Create: `src/rag/ingest/reader/url.py`  (read_url 异步入口,httpx + content-type 推断)
- Create: `src/rag/ingest/reader/extensions/__init__.py`
- Create: `src/rag/ingest/reader/extensions/base.py`  (FormatAdapter Protocol + wrap_*_error)
- Create: `src/rag/ingest/reader/extensions/text.py`  (text_adapter 共享 txt + md)
- Create: `src/rag/ingest/reader/extensions/html.py`  (html_adapter,raw_text=markdown via html2md)
- Create: `src/rag/ingest/reader/extensions/pdf.py`  (pdf_adapter,pypdf + 简化后处理)
- Create: `src/rag/ingest/reader/extensions/docx.py`  (docx_adapter,mammoth + 内嵌图上传)
- Create: `src/rag/ingest/reader/extensions/pptx.py`  (pptx_adapter,parse_office)
- Create: `src/rag/ingest/reader/extensions/xlsx.py`  (xlsx_adapter,openpyxl 双轨)
- Create: `src/rag/ingest/reader/extensions/csv.py`  (csv_adapter,含 format_text md table)
- Create: `src/rag/ingest/reader/raw_text.py`  (read_raw_text 共享解码 + base64 上传)
- Create: `src/rag/ingest/reader/html2md.py`  (html_to_md,Turndown 等价)
- Create: `src/rag/ingest/reader/pdf_text_postprocess.py`  (postprocess_lite_parse_pages)
- Create: `src/rag/ingest/reader/parse_office.py`  (pptx/docx 共享 ZIP 解析)
- Delete: `src/rag/ingest/structure/`  (整个目录,Phase A 清理)
- Create: `tests/unit/reader/test_extensions_text.py`  (txt + md)
- Create: `tests/unit/reader/test_extensions_html.py`
- Create: `tests/unit/reader/test_extensions_pdf.py`
- Create: `tests/unit/reader/test_extensions_docx.py`
- Create: `tests/unit/reader/test_extensions_pptx.py`
- Create: `tests/unit/reader/test_extensions_xlsx.py`
- Create: `tests/unit/reader/test_extensions_csv.py`
- Create: `tests/unit/reader/test_reader_e2e.py`  (覆盖 8 种格式)
- Create: `tests/unit/reader/test_reader_fixtures.py`  (用 tests/data/ 真实 fixture)
- Create: `tests/unit/reader/test_url.py` / `test_url_errors.py`
- Create: `tests/unit/reader/test_raw_text.py` / `test_html2md.py` / `test_parse_office.py` / `test_pdf_text_postprocess.py`
- Create: `tests/unit/reader/test_section_11_acceptance.py`
- Create: `tests/data/`  + 真实 fixture 文件

---

## 重构后架构 (FastGPT 对齐)

```
src/rag/ingest/reader/
├── __init__.py              # 公共 API: read_file / read_url / EXTENSION_ADAPTERS / dispatch_bytes
├── types.py                 # FormatReaderResult (frozen dataclass) + UploadFileHandler TypedDict
├── dispatch.py              # EXTENSION_ADAPTERS (9 后缀) + async dispatch_bytes (内部)
├── file.py                  # read_file(path) -> TextDoc (同步, asyncio.run 包装)
├── url.py                   # read_url(url) -> TextDoc (异步, httpx + content-type 推断)
├── raw_text.py              # read_raw_text 共享解码 + base64 上传
├── html2md.py               # html_to_md (Turndown 等价)
├── pdf_text_postprocess.py  # postprocess_lite_parse_pages
├── parse_office.py          # pptx/docx 共享 ZIP 解析
└── extensions/
    ├── __init__.py          # 重导出 8 个 adapter
    ├── base.py              # FormatAdapter async Protocol + wrap_parse_error + wrap_encoding_error
    ├── text.py              # text_adapter (txt + md 共享)
    ├── html.py              # html_adapter (html2md → markdown)
    ├── pdf.py               # pdf_adapter (pypdf + 简化后处理)
    ├── docx.py              # docx_adapter (mammoth + 内嵌图上传)
    ├── pptx.py              # pptx_adapter (parse_office)
    ├── xlsx.py              # xlsx_adapter (openpyxl 双轨)
    └── csv.py               # csv_adapter (+ format_text: md table)
```

**核心设计**: buffer 是唯一输入,所有 adapter 都是 `async def (buffer, *, encoding, upload_file) -> FormatReaderResult`;`dispatch_bytes` 用 `inspect.signature` 探测 `upload_file` 是否被接受并透传。

**两段关系** (当前):
```
read_file(path) ─┐                                          ┌─> text_adapter (txt, md)
                 ├─> dispatch_bytes(buffer, ext, source) ─> ├─> html_adapter (html, htm)
read_url(url)   ─┘   (async; sync 入口用 asyncio.run 包)    ├─> pdf_adapter
                                                            ├─> docx_adapter
                                                            ├─> pptx_adapter
                                                            ├─> csv_adapter
                                                            └─> xlsx_adapter
```
8 个 adapter 模块,9 个 dispatch 槽位(`md` 与 `htm` 走别名)。无 json、无 api_response。

---

## Step 0: Stub (确保模块可 import,测试可进 RED)

```python
# src/rag/ingest/reader/__init__.py (stub)
from pathlib import Path
from rag.ingest.types import TextDoc


def read_file(path: str | Path) -> TextDoc:
    raise NotImplementedError


async def read_url(url: str) -> TextDoc:
    raise NotImplementedError
```

```python
# src/rag/ingest/reader/types.py
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import NotRequired, TypedDict

from rag.ingest.types import DocMeta


class UploadedFileResult(TypedDict):
    key: str
    previewUrl: NotRequired[str]


UploadFileHandler = Callable[[str, str, bytes], Awaitable[UploadedFileResult]]


@dataclass(frozen=True)
class FormatReaderResult:
    raw_text: str
    meta: DocMeta  # 必含 mime / encoding / page_count / paragraph_count
    format_text: str | None = None
    images: list[str] = field(default_factory=list)
    extras: dict[str, object] = field(default_factory=dict)
```

```python
# src/rag/ingest/reader/dispatch.py
async def dispatch_bytes(  # type: ignore[no-untyped-def]
    buffer: bytes,
    extension: str,
    source: str,
    *,
    encoding: str = "utf-8",
    datasource: str = "file",
    filename: str | None = None,
    upload_file: UploadFileHandler | None = None,
):
    raise NotImplementedError
```

---

## Step 1: 写失败单测 (8 个 adapter + dispatch + file + url 共 48+ 个)

```python
# tests/unit/reader/test_extensions_text.py
import pytest
from rag.ingest.reader.extensions.text import text_adapter

@pytest.mark.asyncio
async def test_text_adapter_txt_plain() -> None:
    buf = b"hello world"
    result = await text_adapter(buf)
    assert result.raw_text == "hello world"
    assert result.meta.mime == "text/plain"


@pytest.mark.asyncio
async def test_text_adapter_md_keeps_markdown_syntax() -> None:
    buf = b"# Title\n\n## Section\n\n- item 1\n- item 2"
    result = await text_adapter(buf)
    assert "# Title" in result.raw_text
    assert "## Section" in result.raw_text
    assert "- item 1" in result.raw_text
    assert result.meta.mime == "text/plain"


# ── tests/unit/reader/test_reader_e2e.py ──
from pathlib import Path
from rag.ingest.reader import read_file

def test_e2e_txt(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    doc = read_file(tmp_path / "a.txt")
    assert doc.text == "hello"
    assert doc.meta.size_bytes == 5
    assert doc.meta.mime == "text/plain"


# ── tests/unit/reader/test_section_11_acceptance.py (节选) ──
import pytest
from rag.exception import RAGError
from rag.ingest.reader import EXTENSION_ADAPTERS, dispatch_bytes

@pytest.mark.asyncio
async def test_extension_adapters_have_all_builtins() -> None:
    assert set(EXTENSION_ADAPTERS) == {"txt", "md", "html", "htm", "pdf", "docx", "pptx", "csv", "xlsx"}


@pytest.mark.asyncio
async def test_dispatch_bytes_txt() -> None:
    doc = await dispatch_bytes(b"hello", "txt", source="file:///tmp/a.txt")
    assert doc.text == "hello"
    assert doc.meta.mime == "text/plain"


@pytest.mark.asyncio
async def test_dispatch_bytes_unsupported_raises() -> None:
    with pytest.raises(RAGError) as exc_info:
        await dispatch_bytes(b"x", "xyz", source="file:///x.xyz")
    assert exc_info.value.code == "reader.unsupported"
    assert ".xyz" in exc_info.value.message
```

---

## Step 2: 跑测试,确认 fail (NotImplementedError, RED)

```bash
uv run pytest tests/unit/test_adapter_*.py tests/unit/test_reader_dispatch.py tests/unit/test_reader_e2e.py -v
# 期望: 28+ 个 RED (NotImplementedError,无 ImportError)
```

---

## Step 3: 实现 dispatch + 8 async adapters

```python
# src/rag/ingest/reader/extensions/base.py
from typing import Protocol

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.types import FormatReaderResult, UploadFileHandler


class FormatAdapter(Protocol):
    """bytes + encoding (+ 可选 upload_file) -> FormatReaderResult。"""

    async def __call__(
        self,
        buffer: bytes,
        *,
        encoding: str = "utf-8",
        upload_file: UploadFileHandler | None = None,
    ) -> FormatReaderResult: ...


def wrap_parse_error(source: str, exc: Exception, parser: str) -> RAGError:
    return RAGError(code=ReaderErrorCode.PARSE, message=f"{source}: {parser} parse failed: {exc}")


def wrap_encoding_error(source: str, exc: Exception, parser: str) -> RAGError:
    return RAGError(code=ReaderErrorCode.ENCODING, message=f"{source}: {parser} encoding failed: {exc}")
```

```python
# src/rag/ingest/reader/extensions/text.py (txt + md 共享 text_adapter)
from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.raw_text import read_raw_text
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

TEXT_MIME = "text/plain"


async def text_adapter(
    buffer: bytes, *, encoding: str = "utf-8", upload_file=None,
) -> FormatReaderResult:
    try:
        raw_text = await read_raw_text(buffer, encoding=encoding, upload_file=upload_file)
    except UnicodeDecodeError as e:
        raise wrap_encoding_error("<buffer:text>", e, "text/md") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:text>", e, "text/md") from e
    return FormatReaderResult(
        raw_text=raw_text, format_text=None, images=[],
        meta=DocMeta(datasource="api", mime=TEXT_MIME, encoding=encoding, size_bytes=len(buffer)),
    )
```

```python
# src/rag/ingest/reader/extensions/html.py (raw_text + html2md → markdown)
from rag.ingest.reader.extensions.base import wrap_encoding_error, wrap_parse_error
from rag.ingest.reader.html2md import html_to_md
from rag.ingest.reader.raw_text import read_raw_text
from rag.ingest.reader.types import FormatReaderResult
from rag.ingest.types import DocMeta

HTML_MIME = "text/html"


async def html_adapter(
    buffer: bytes, *, encoding: str = "utf-8", upload_file=None,
) -> FormatReaderResult:
    try:
        html = await read_raw_text(buffer, encoding=encoding, upload_file=upload_file)
        markdown = await html_to_md(html, upload_file=upload_file)
    except UnicodeDecodeError as e:
        raise wrap_encoding_error("<buffer:html>", e, "html") from e
    except Exception as e:
        raise wrap_parse_error("<buffer:html>", e, "html") from e
    return FormatReaderResult(
        raw_text=markdown, format_text=None, images=[], extras={},
        meta=DocMeta(datasource="api", mime=HTML_MIME, encoding=encoding, size_bytes=len(buffer)),
    )
```

```python
# src/rag/ingest/reader/dispatch.py (内部, 公共 API 在 __init__.py)
import inspect

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.extensions import (
    csv_adapter, docx_adapter, html_adapter, pdf_adapter,
    pptx_adapter, text_adapter, xlsx_adapter,
)
from rag.ingest.reader.types import FormatReaderResult, UploadFileHandler
from rag.ingest.types import TextDoc

AsyncFormatAdapter = "Callable[..., Awaitable[FormatReaderResult]]"  # 类型别名

EXTENSION_ADAPTERS: dict[str, AsyncFormatAdapter] = {
    "txt": text_adapter, "md": text_adapter,
    "html": html_adapter, "htm": html_adapter,
    "pdf": pdf_adapter, "docx": docx_adapter,
    "pptx": pptx_adapter,
    "csv": csv_adapter, "xlsx": xlsx_adapter,
}


async def dispatch_bytes(
    buffer: bytes, extension: str, source: str, *,
    encoding: str = "utf-8", datasource: str = "file", filename: str | None = None,
    upload_file: UploadFileHandler | None = None,
) -> TextDoc:
    ext = extension.lower().lstrip(".")
    adapter = EXTENSION_ADAPTERS.get(ext)
    if adapter is None:
        raise RAGError(
            code=ReaderErrorCode.UNSUPPORTED,
            message=(
                f"{source}: only support .txt, .md, .html, .pdf, .docx, .pptx, "
                f".csv, .xlsx. '.{ext}' is not supported."
            ),
        )
    # upload_file 仅在 adapter 接受该参数时透传 (xlsx 当前不接受)。
    if "upload_file" in inspect.signature(adapter).parameters:
        result = await adapter(buffer, encoding=encoding, upload_file=upload_file)
    else:
        result = await adapter(buffer, encoding=encoding)
    full_meta = result.meta.model_copy(update={
        "datasource": datasource, "filename": filename, "source": source, "size_bytes": len(buffer),
    })
    return TextDoc(
        text=result.raw_text, format_text=result.format_text,
        meta=full_meta, images=list(result.images),
    )
```

```python
# src/rag/ingest/reader/file.py (sync 包装 async dispatch)
import asyncio
from pathlib import Path

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.dispatch import dispatch_bytes
from rag.ingest.types import TextDoc


def read_file(path: str | Path) -> TextDoc:
    p = Path(path)
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
    return asyncio.run(dispatch_bytes(
        buffer=buffer, extension=p.suffix,
        source=f"file://{p.resolve()}", datasource="file", filename=p.name,
    ))
```

```python
# src/rag/ingest/reader/url.py (httpx + content-type / body sniff 推断)
import httpx

from rag.error_codes import ReaderErrorCode
from rag.exception import RAGError
from rag.ingest.reader.dispatch import EXTENSION_ADAPTERS, dispatch_bytes, filename_from_url
from rag.ingest.types import TextDoc


async def read_url(
    url: str, *, max_size: int = 1_000_000_000, timeout_s: float = 600.0, encoding: str = "utf-8",
) -> TextDoc:
    timeout = httpx.Timeout(connect=10.0, read=timeout_s, write=10.0, pool=10.0)
    async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
        # HEAD 预检 (失败不阻塞)
        try:
            head = await client.head(url, timeout=10.0)
            cl = head.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > max_size:
                raise RAGError(code=ReaderErrorCode.TOO_LARGE,
                               message=f"{url}: file too large: {cl} > {max_size}")
        except (httpx.HTTPError, Exception):
            pass
        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RAGError(code=ReaderErrorCode.PARSE, message=f"{url}: httpx failed: {e}") from e
        buffer, content_type, final_url = resp.content, resp.headers.get("content-type"), str(resp.url)

    if len(buffer) > max_size:
        raise RAGError(code=ReaderErrorCode.TOO_LARGE,
                       message=f"{url}: file too large: {len(buffer)} > {max_size}")
    extension = _infer_extension(final_url, content_type, buffer)
    return await dispatch_bytes(
        buffer=buffer, extension=extension, source=final_url,
        datasource="url", filename=filename_from_url(final_url), encoding=encoding,
    )
```

---

## Step 4: 跑全部测试 (48+ 测试全过)

```bash
uv run pytest tests/unit/reader/ -v
# 期望: tests/unit/reader/ 下全部通过
```

---

## Step 5: 写 tests/data/ 真实 fixture + e2e 测试

```python
# tests/unit/test_reader_fixtures.py
def test_sample_txt_readable(sample_txt: Path) -> None:
    doc = read_file(sample_txt)
    assert "Plain text fixture" in doc.text
    assert "中文" in doc.text
    assert "🎉" in doc.text


def test_sample_pdf_reads_pages(sample_pdf: Path) -> None:
    doc = read_file(sample_pdf)
    assert doc.meta.page_count == 3
    assert "Sample PDF" in doc.text


def test_sample_csv_with_format_text(sample_csv: Path) -> None:
    doc = read_file(sample_csv)
    # csv adapter 同时填 raw_text (CSV) + format_text (md table)
    assert "Alice" in doc.text
    assert doc.text.startswith("id,name,age,city")
```

---

## Step 6: commit

```bash
git add src/rag/ingest/reader/ src/rag/exception.py tests/
git commit -m "refactor(ingest): dispatch + 8 async buffer-based reader adapters (FastGPT-aligned, D1-D8)"
```

---

## Deviation Notes (D1, D2, D7, D8 + 后续 phase)

- **(D1) Reader 架构重做**: 原 plan 写 path-based registry + 单文件 reader.py,改为 dispatch + 8 async adapters 子包,buffer 是唯一输入。FastGPT 调研后批准。
- **(D2) Document Structure 不再独立 stage**: 原 plan 把 `structure.py` 与 `reader.py` 并列,实际 reader 不抽 doc-level structure(Phase A 删 `src/rag/ingest/structure/`),chunker 内部 per-chunk regex 现场重算 heading_stack。
- **(D7) Exception 统一**: 全局用 `RAGError(code, message)`,reader 系列用 `ReaderErrorCode`(`reader.not_found` / `reader.permission` / `reader.unsupported` / `reader.too_large` / `reader.encoding` / `reader.parse`)。旧 `ReaderError(path=...)` 模式已废弃。
- **(D8) FormatReaderResult 扩展**: 加 `format_text: str | None` (csv/xlsx markdown table) + `images: list[str]` (docx 内嵌图上传结果) + `extras: dict[str, object]`(兜底字段,如 csv `row_count`)。
- **(Async 适配)**: 所有 8 个 adapter 都是 `async def`,`dispatch_bytes` 统一 `await`(无 `inspect.iscoroutine` 分支);`upload_file` 通过 `inspect.signature` 单点探测透传。`read_file` 同步入口用 `asyncio.run(dispatch_bytes(...))` 包装,`read_url` 异步入口直接 `await`。
- **html adapter 走 html2md**: 与原 BS4 strip 不同,本模块走 `read_raw_text` + `html_to_md`(Turndown 等价),raw_text 直接是 markdown,无 format_text。
- **csv/xlsx format_text 双轨**: raw_text (CSV 原样或 xlsx 多 sheet 拼接) + format_text (markdown table 视图),下游 chunker 选用。
- **docx 内嵌图**: mammoth 解出 HTML → `html_to_md` → markdown;图片回调 `upload_file` 必传(无则抛 `RAGError`);结果图片 key 进入 `images: list[str]`。
- **pptx 不抽图**: 与 docx 不同,解析走 `parse_office` 共享 ZIP 路径,仅抽 `<a:t>` 文本,不上传内嵌媒体。
