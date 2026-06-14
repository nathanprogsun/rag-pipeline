# Task 9: Chunker (11-level recursive split, returns list[Chunk])

**Status**: REFACTOR → 已完成 (D3, D4) — 2026-06-12

## 状态: 已完成 (2026-06-12 同步)

> **实际交付**(`refactor/chunker-reader` 分支):
>
> - 落地路径:`src/rag/ingest/chunker/` 子包(10 文件)
>   - `__init__.py` — 公共 API:`Chunker / ChunkSettings / Chunk / ChunkContext`
>   - `types.py` — `ChunkContext` frozen dataclass + `from_meta_and_structure` 工厂
>   - `settings.py` — `ChunkSettings` Pydantic frozen
>   - `core.py` — `Chunker.split(text, ctx) -> list[Chunk]` + `split_str(text) -> list[str]` 兼容
>   - `rules.py` — **11 级 Rule** 元数据表(custom_sign + H1-H5 + code + md + \n\n + \n + punct_merged)
>   - `recursive.py` — `common_split` 递归主体
>   - `code_block.py` / `table.py` / `overlap.py` / `finalize.py` / `utils.py` — 单一职责拆分
> - 测试:`tests/unit/chunker/` 9 个文件,合计 **65+ 测试**,覆盖率 **100%**
> - 验收:`uv run pytest tests/unit/chunker/ -v` 全过;mypy 0 错;ruff 全过
> - 11 级 Rule 与 FastGPT 对齐,见下方"重构后 11 级 Rule"段
> - 后续清理 phase A:无新动作(Chunker 不依赖 structure/)
> - 后续清理 phase B:文件头 docstring 删除 FastGPT 内部代号
> - 后续清理 phase D:`test_chunker_*.py` 9 个文件按 rule 路径拆开(code_block / finalize / overlap / recursive / rules / settings / table / utils / e2e)

## 后续 review/audit 影响 (2026-06-13 同步)

> 本 task 在 2026-06-12 同步后又经历 1 轮 R-Audit 修订,落地到 `refactor/chunker-reader` 分支。
>
> - **R-Audit #2 (Chunk 三层类型)**: 收敛到单 `Chunk` + `ChunkMetadata`,删除旧 `ChunkV2` / `ChunkNew` 类型别名与备份 dataclass,所有 `core.py` / `rules.py` / `recursive.py` 引用统一为 `from rag.ingest.types import Chunk, ChunkMetadata`
> - **R-Audit #4 (死代码)**: 删除 `_apply_rule` 的 `rule.reg.startswith(r"^(") and "#" in rule.reg[:6]` heading hack,改为 `Rule.kind: Literal["heading", "code", "table", "newline", "punct", "custom"]` 字段显式标注,`common_split` 据此判断是否累加 `parent_title`
>
> 当前 task9 相关累计:**65+ unit 测试 + 100% 覆盖率 + 11 级 Rule** 与 FastGPT 对齐,mypy 0 错 / ruff 全过。

> **历史溯源**(本 task 原始设计):原 plan 写 17 级分隔符 (H1-H5 + code + html + md + \n\n + \n + 5 punct),在 `chunker.py` 单文件实现。调研 FastGPT `textSplitter.ts` + 实测后收敛到 **11 级**: 5 punct 合并为 1 (overlap 行为一致),HTML table 删除(其正则实际匹配 md 格式,重复 md_table)。同时 **Chunker.split() 签名升级为返回 `list[Chunk]`**,保留 `split_str()` 返回 `list[str]` 兼容旧 API。原描述保留在下方,作为 D3/D4 偏差的溯源依据。

**Files:**
- Modify: `src/rag/ingest/chunker/__init__.py`  (导出 Chunker, ChunkSettings, Chunk, ChunkContext)
- Create: `src/rag/ingest/chunker/types.py`  (★ 新增 ChunkContext dataclass)
- Create: `src/rag/ingest/chunker/settings.py`  (ChunkSettings Pydantic frozen)
- Create: `src/rag/ingest/chunker/core.py`  (Chunker, split + split_str)
- Create: `src/rag/ingest/chunker/rules.py`  (★ 11 级 Rule 元数据表)
- Create: `src/rag/ingest/chunker/recursive.py`  (common_split 递归主体)
- Create: `src/rag/ingest/chunker/code_block.py`  (代码块保护)
- Create: `src/rag/ingest/chunker/table.py`  (markdownTableSplit)
- Create: `src/rag/ingest/chunker/overlap.py`  (overlap 倒序累积)
- Create: `src/rag/ingest/chunker/finalize.py`  (merge_small + sliding_window)
- Create: `src/rag/ingest/chunker/utils.py`  (valid_len + simple_text)
- Modify: `src/rag/ingest/types.py`  (★ ChunkMetadata 扩展 4 字段)
- Modify: `tests/unit/test_chunker_*.py`  (用 split_str 兼容,step 索引更新)
- Modify: `tests/unit/test_coverage_gaps.py`  (step 11→10)

---

## 重构后 11 级 Rule (与 FastGPT 对齐)

```
step  0:  CUSTOM_SPLIT_SIGN (默认占位)
step  1:  H1  (^#\s+[^\n]+\n)        forbid_overlap
step  2:  H2  (^##\s+[^\n]+\n)       forbid_overlap
step  3:  H3  (^###\s+[^\n]+\n)      forbid_overlap
step  4:  H4  (^####\s+[^\n]+\n)     forbid_overlap
step  5:  H5  (^#####\s+[^\n]+\n)    forbid_overlap
step  6:  code_block (```/~~~)      split_around, forbid_overlap
step  7:  md_table (|...|)          split_around, forbid_overlap
step  8:  \n{2,}                   forbid_overlap
step  9:  \n                       forbid_overlap
step 10:  punct_merged ([。！？；，]|[!?;,] )   ← 新合并, 允许 overlap
```

旧 17 级 → 新 11 级 (5 个合并, 1 个删除):

| 旧 | 新 | 理由 |
|---|----|------|
| 0: CUSTOM_SPLIT_SIGN | 0: CUSTOM_SPLIT_SIGN | 保留 |
| 1-5: H1-H5 | 1-5: H1-H5 | 保留 (FastGPT 默认 5 级) |
| 6: code | 6: code | 保留 |
| **7: html_table** | (删除) | 正则实际匹配 md 格式, 重复 md_table |
| 8: md_table | 7: md_table | 保留 |
| 9-10: \n\n, \n | 8-9: \n\n, \n | 保留 |
| **11-15: 5 punct** | **10: punct_merged** | 5 合并 1 (overlap 行为一致) |

---

## Chunker 新签名 (D4)

```python
# 主签名: 返回 list[Chunk] (含元数据)
def split(self, text: str, *, ctx: ChunkContext | None = None) -> list[Chunk]

# 旧 API 兼容: 返回 list[str] (供旧测试 / 旧调用方)
def split_str(self, text: str) -> list[str]
```

`ChunkContext` 数据类 (`frozen=True`):
- `source / file_type / page_count / encoding` ← DocMeta 注入
- `heading_path / has_code / has_table` ← DocumentStructure 派生
- `image_refs` ← 预留 Normalizer 阶段图片引用

`ChunkMetadata` 扩展 4 字段:
```python
class ChunkMetadata(BaseModel):
    # ── 位置 (chunker 填) ──
    chunk_index, total_chunks, char_count, valid_len
    # ── 来自 DocMeta (D8 新增) ──
    source: str = ""
    file_type: str = ""
    page_count: int | None = None
    encoding: str = "utf-8"
    # ── 来自 DocumentStructure ──
    heading_path, has_code, has_table, image_refs
```

---

## Step 0: Stub (确保模块可 import)

```python
# src/rag/ingest/chunker/__init__.py
from .core import Chunker  # Chunker stub
from .settings import ChunkSettings
from .types import Chunk, ChunkMetadata  # 重新导出


class Chunker:  # stub
    def __init__(self, settings): self.s = settings
    def split(self, text: str, *, ctx=None) -> list:
        raise NotImplementedError
    def split_str(self, text: str) -> list:
        return []
```

```python
# src/rag/ingest/chunker/types.py
from dataclasses import dataclass, field
from rag.ingest.structure.base import DocumentStructure
from rag.ingest.types import Chunk, ChunkMetadata, DocMeta


@dataclass(frozen=True)
class ChunkContext:
    source: str = ""
    file_type: str = ""
    page_count: int | None = None
    encoding: str = "utf-8"
    heading_path: tuple[str, ...] = ()
    has_code: bool = False
    has_table: bool = False
    image_refs: tuple[str, ...] = ()

    @classmethod
    def empty(cls): return cls()

    @classmethod
    def from_meta_and_structure(cls, meta: DocMeta, structure: DocumentStructure | None,
                                 heading_path: list[str]) -> "ChunkContext":
        return cls(source=meta.filename or "", file_type=_guess_file_type(meta),
                   page_count=meta.page_count, encoding=meta.encoding,
                   heading_path=tuple(heading_path),
                   has_code=bool(structure and structure.has_code_blocks),
                   has_table=bool(structure and structure.has_tables))


def _guess_file_type(meta: DocMeta) -> str:
    if meta.filename and "." in meta.filename:
        return meta.filename.rsplit(".", 1)[-1].lower()
    if meta.mime:
        return meta.mime.split("/")[-1].lower()
    return ""
```

---

## Step 1: 写失败单测 (12 个主测 + 6 个边界 case)

```python
# tests/unit/test_chunker_e2e.py (用 split_str 兼容旧 API)
def test_basic_paragraph_split() -> None:
    s = ChunkSettings(chunk_size=50, max_chunk_size=200, min_chunk_size=10)
    text = "段落一。\n\n段落二。\n\n段落三。"
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 2


def test_markdown_heading_creates_chunks() -> None:
    s = ChunkSettings(chunk_size=100, max_chunk_size=500)
    text = "# 标题A\n\n内容A。\n\n# 标题B\n\n内容B。"
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 1


def test_code_block_preserved_intact() -> None:
    s = ChunkSettings(chunk_size=1000, max_chunk_size=8000)
    text = "前文\n```python\ndef f():\n    return 1\n```\n后文"
    chunks = Chunker(s).split_str(text)
    code_chunk = next(c for c in chunks if "def f():" in c)
    assert "```python" in code_chunk
    assert "return 1" in code_chunk


def test_max_chunk_size_enforced_on_huge_text() -> None:
    s = ChunkSettings(chunk_size=1000, max_chunk_size=200)
    text = "字" * 1000
    chunks = Chunker(s).split_str(text)
    assert all(len(c) <= s.max_chunk_size for c in chunks)


def test_empty_input_returns_empty() -> None:
    assert Chunker(ChunkSettings()).split_str("") == []


def test_custom_separator_splits() -> None:
    s = ChunkSettings(chunk_size=100, max_chunk_size=500, custom_separator=r"---")
    text = "part1\n---\npart2\n---\npart3"
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 2


def test_min_chunk_size_merge() -> None:
    s = ChunkSettings(chunk_size=200, min_chunk_size=64, max_chunk_size=500)
    text = "短。" * 5 + "\n\n" + "长内容。" * 20
    chunks = Chunker(s).split_str(text)
    for c in chunks:
        if c != chunks[-1]:
            assert len(c) >= s.min_chunk_size or len(c) == 0


def test_chinese_punctuation_split() -> None:
    s = ChunkSettings(chunk_size=20, max_chunk_size=200)
    text = "第一句。第二句!还有问句?还有分号;还有逗号,继续。"
    chunks = Chunker(s).split_str(text)
    assert len(chunks) >= 2


# ── 新主签名: split() 返回 list[Chunk] ──
def test_split_returns_chunk_objects() -> None:
    s = ChunkSettings(chunk_size=50, max_chunk_size=200, min_chunk_size=10)
    text = "段落一。\n\n段落二。\n\n段落三。"
    chunks = Chunker(s).split(text)
    assert all(hasattr(c, "text") and hasattr(c, "metadata") for c in chunks)
    assert all(c.metadata.chunk_index == i for i, c in enumerate(chunks))
    assert all(c.metadata.total_chunks == len(chunks) for c in chunks)


def test_split_injects_chunk_context() -> None:
    from rag.ingest.chunker.types import ChunkContext

    s = ChunkSettings(chunk_size=50, max_chunk_size=200, min_chunk_size=10)
    ctx = ChunkContext(source="file:///x.md", file_type="md", encoding="utf-8")
    chunks = Chunker(s).split("段落一。\n\n段落二。", ctx=ctx)
    for c in chunks:
        assert c.metadata.source == "file:///x.md"
        assert c.metadata.file_type == "md"
        assert c.metadata.encoding == "utf-8"


def test_split_empty_returns_empty_list() -> None:
    assert Chunker(ChunkSettings()).split("") == []


# ── tests/unit/test_chunker_rules.py (11 级新长度) ──
def test_default_steps_count() -> None:
    """默认 deep=5 时: 1 sign + 5 H + 1 code + 1 md + 2 nl + 1 punct = 11 (收敛后)。"""
    from rag.ingest.chunker.rules import STEPS
    assert len(STEPS) == 11


def test_default_steps_contains_required_levels() -> None:
    """验证 STEPS 包含 H1-H5 / code / md / nl*2 / punct。"""
    from rag.ingest.chunker.rules import STEPS
    heading_rules = [r for r in STEPS if r.reg.startswith("^(#")]
    assert len(heading_rules) == 5
    code_rules = [r for r in STEPS if r.split_around and "```" in r.reg]
    assert len(code_rules) == 1
    has_double_nl = any(r.reg == r"\n{2,}" for r in STEPS)
    has_single_nl = any(r.reg == r"\n" for r in STEPS)
    assert has_double_nl and has_single_nl
    # punct_merged 唯一允许 overlap
    punct_rules = [r for r in STEPS if not r.forbid_overlap]
    assert any("。" in r.reg for r in punct_rules)
```

```python
# tests/unit/test_chunker_overlap.py (step 索引更新 11→10)
def test_overlap_returns_empty_when_step_is_final() -> None:
    result = get_overlap_tail(text="段落内容。另一段。", step=11, ...)  # out of bounds
    assert result == ""


def test_overlap_returns_15_percent_of_text() -> None:
    result = get_overlap_tail(text=text, step=10, ...)  # punct_merged 级
    assert 10 <= valid_len(result) <= 20
```

---

## Step 2: 跑测试,确认 fail (RED)

```bash
uv run pytest tests/unit/test_chunker_*.py -v
# 期望: 18+ 个 RED (NotImplementedError,无 ImportError)
```

---

## Step 3: 实现 11 级 rules + Chunker core

```python
# src/rag/ingest/chunker/rules.py
from dataclasses import dataclass

CUSTOM_SPLIT_SIGN = "-----CUSTOM_SPLIT_SIGN-----"


@dataclass(frozen=True)
class Rule:
    reg: str
    max_len: int
    split_around: bool = False
    forbid_overlap: bool = False
    custom: bool = False


def _heading_rules(chunk_size: int, deep: int) -> list[Rule]:
    max_deep = min(deep, 5)
    return [
        Rule(reg=r"^(" + "#" * i + r"\s+[^\n]+\n)", max_len=chunk_size, forbid_overlap=True)
        for i in range(1, max_deep + 1)
    ]


def _code_block_rule(code_block_max_len: int) -> Rule:
    return Rule(reg=r"(```[\s\S]*?```|~~~[\s\S]*?~~~)",
                max_len=code_block_max_len, split_around=True, forbid_overlap=True)


def _md_table_rule(chunk_size: int) -> Rule:
    return Rule(reg=r"((?:^|\n)(?:\|[^\n]*\|\n)+)",
                max_len=chunk_size, split_around=True, forbid_overlap=True)


def _newline_rules(chunk_size: int) -> list[Rule]:
    return [
        Rule(reg=r"\n{2,}", max_len=chunk_size, forbid_overlap=True),
        Rule(reg=r"\n", max_len=chunk_size, forbid_overlap=True),
    ]


def _punct_merged_rule(chunk_size: int) -> Rule:
    """5 种标点合并为 1 条: 中: 。！？；, + 英: ! ? ; , (后接空格)。"""
    return Rule(reg=r"([。！？；，]|[!?;,] )", max_len=chunk_size, forbid_overlap=False)


def build_steps(chunk_size: int, max_size: int, paragraph_chunk_deep: int = 5,
                custom_reg: list[str] | None = None) -> list[Rule]:
    code_block_max_len = min(max_size, chunk_size * 4)
    custom_rules = [Rule(reg=reg.replace("\\n", "\n"), max_len=max_size, custom=True)
                    for reg in (custom_reg or [])]
    return [
        *custom_rules,
        Rule(reg=CUSTOM_SPLIT_SIGN, max_len=max_size, custom=True),
        *_heading_rules(chunk_size, paragraph_chunk_deep),
        _code_block_rule(code_block_max_len),
        _md_table_rule(chunk_size),
        *_newline_rules(chunk_size),
        _punct_merged_rule(chunk_size),
    ]


def default_steps(chunk_size: int, max_size: int) -> list[Rule]:
    return build_steps(chunk_size, max_size, paragraph_chunk_deep=5)


STEPS: list[Rule] = default_steps(chunk_size=1000, max_size=8000)
```

```python
# src/rag/ingest/chunker/core.py
import re
from rag.ingest.chunker.types import ChunkContext
from rag.ingest.chunker.utils import valid_len
from rag.ingest.types import Chunk, ChunkMetadata
from .code_block import protect_code_block
from .finalize import enforce_max_size, merge_small_chunks
from .recursive import common_split
from .rules import CUSTOM_SPLIT_SIGN, build_steps
from .settings import ChunkSettings
from .utils import restore_code_block_marker, simple_text

_ASCII_PUNCT_BETWEEN_CN_RE = re.compile(r"([一-鿿])([!?,;.])([一-鿿])")
_PRESPLIT_RE = re.compile(r"(\n\n|。|！|？|；|，|[!?,;.])(?=\S)")


def _normalize_ascii_punct(text: str) -> str:
    return _ASCII_PUNCT_BETWEEN_CN_RE.sub(r"\1\2 \3", text)


def _pre_split(text: str) -> list[str]:
    if not text.strip():
        return []
    parts = _PRESPLIT_RE.split(text)
    return [p for p in parts if p and p.strip()]


class Chunker:
    def __init__(self, settings: ChunkSettings) -> None:
        self.s = settings

    # ── 新主签名: 返回 list[Chunk] ──
    def split(self, text: str, *, ctx: ChunkContext | None = None) -> list[Chunk]:
        if not text or not text.strip():
            return []
        raw = self._split_legacy(text)
        if not raw:
            return []
        context = ctx or ChunkContext.empty()
        n = len(raw)
        return [
            Chunk(text=seg, metadata=ChunkMetadata(
                chunk_index=i, total_chunks=n, char_count=len(seg), valid_len=valid_len(seg),
                source=context.source, file_type=context.file_type,
                page_count=context.page_count, encoding=context.encoding,
                heading_path=list(context.heading_path),
                has_code=context.has_code, has_table=context.has_table,
                image_refs=list(context.image_refs),
            ))
            for i, seg in enumerate(raw)
        ]

    # ── 旧 API 兼容 ──
    def split_str(self, text: str) -> list[str]:
        return self._split_legacy(text)

    def _split_legacy(self, text: str) -> list[str]:
        if not text or not text.strip():
            return []
        text = simple_text(text)
        text = protect_code_block(text)
        if self.s.custom_separator:
            custom_chunks = self._split_custom(text)
            if custom_chunks is not None:
                return self._finalize(custom_chunks)
        segments = text.split(CUSTOM_SPLIT_SIGN)
        all_chunks: list[str] = []
        for seg in segments:
            if not seg.strip():
                continue
            all_chunks.extend(self._split_segment(seg))
        return self._finalize(all_chunks)

    # ... (其余 _split_custom / _split_segment / _split_long / _finalize 保留原逻辑)
```

---

## Step 4: 跑全部测试

```bash
uv run pytest tests/unit/test_chunker_*.py -v
# 期望: 65+ passed (含 11 级新 STEPS 长度断言)
```

---

## Step 5: commit

```bash
git add src/rag/ingest/chunker/ src/rag/ingest/types.py tests/
git commit -m "refactor(chunker): 17→11 level convergence + ChunkContext + split() returns list[Chunk] (D3, D4)"
```

---

## Deviation Notes (D3, D4, D8)

- **(D3) 17 → 11 级收敛**: 删除 html_table rule(其正则实际匹配 md 格式),合并 5 punct 为 1(`[。！？；，]|[!?;,] `,overlap 行为一致),H1-H5 保留(FastGPT 默认)。共减少 6 条规则但语义覆盖不变。
- **(D4) `split()` 返回 `list[Chunk]`**: 旧 `list[str]` 改为 `list[Chunk]`,每块含 `chunk_index / total_chunks / char_count / valid_len`,接受 `ChunkContext` 注入 `DocMeta` 字段。保留 `split_str()` 兼容旧测试。
- **(D8) `ChunkMetadata` 扩展 4 字段**: `source / file_type / page_count / encoding` 来自 DocMeta 注入,审计/缓存/检索需要。
- **`step` 索引更新**: overlap / recursive 测试中 `step=11` (旧第 11 级 = 句号) 改为 `step=10` (新第 10 级 = punct_merged)。
- **`custom_reg` 兼容**: `build_steps(custom_reg=...)` 仍支持(向后兼容),旧 API 调用方式不变。
- **旧 chunker.py 单文件 → chunker/ 子包**: 拆分为 `core / rules / recursive / finalize / code_block / table / overlap / utils / types / settings / __init__`,单一职责更清晰。
