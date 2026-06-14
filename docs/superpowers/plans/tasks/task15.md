# Task 15: Audit & CitationChecker (spec §0.1 挂载点旁路 + §7.0.3 + §7.7)

**Status**: SCOPED OUT (2026-06-13 同步) — 重构分支未交付,见下方"实际实现"段

## 状态: SCOPED OUT (2026-06-13 同步)

> **实际实现**(`refactor/chunker-reader` 分支,与原 plan 2026-06-10 Task 15 不符):
>
> - **未交付**。`src/rag/retrieval/` 下只有 `__init__.py` / `trace.py`,**不存在** `audit.py` / `citation_check.py`;`tests/unit/` 下也**不存在** `test_citation_check.py`。
> - 重构 plan `2026-06-11-chunker-reader-refactor.md` 重新编号任务:其 Task 15 = "Chunker 入口 (`chunker/core.py`) + E2E 测试",**与本 task (Audit & CitationChecker) 无关**。原 Task 15 (Audit & CitationChecker) 在重构 plan 中被整体跳过,未分配替代 task。
> - 旁路审计相关能力由 `src/rag/retrieval/trace.py::RetrievalTrace` 承担最小子集(仅 `q` / `a` 字段,供 `remove_duplicates` 去重),**不写 jsonl、不挂 Cite 之后、不暴露 `record` API**。
> - **过时期描述修正**:本 task 原"实际交付"段(已删除)声称"`src/rag/retrieval/audit.py` / `citation_check.py` / `tests/unit/test_citation_check.py` 已存在、RetrievalTrace 字段为 `query / dataset_id / chunk_id / score / source / latency_ms`、ReaderError.code 按 6 域分组"——这些与当前代码全部不符,统一在此处标 SCOPED OUT。
>
> **当前实现关键事实(便于后续如要恢复时对照)**:
>
> - `src/rag/ingest/pipeline.py::IngestPipeline.ingest` 是 `async def`(`async def ingest(self, source: IngestSource, *, get_format_text: bool = True) -> IngestResult`)。
> - 管线主链 2 个内部 stage(读者层之后):Normalizer (async) → Chunker (sync `split`)。
> - 4 个 IngestSource: `FileSource` / `UrlSource` / `BufferSource` / `ApiSource`(`src/rag/ingest/source.py`,tagged union,`IngestSource = FileSource | UrlSource | BufferSource | ApiSource`)。
> - 7 个 reader adapter 函数(`text` / `html` / `pdf` / `docx` / `pptx` / `csv` / `xlsx`)覆盖 9 个 extension key(`EXTENSION_ADAPTERS` 中 md/htm 是 text/html 的 alias),全部 `async def` 签名。
> - `src/rag/error_codes.py` 已拆 5 个 `StrEnum`: `ReaderErrorCode` (9 值) / `ChunkerErrorCode` (1 值) / `NormalizerErrorCode` (1 值) / `ConfigErrorCode` (2 值) / `RetrievalErrorCode` (2 值);`ErrorCode` 退化为这五者的 `Union` 兼容别名。
> - `src/rag/domain/enums.py` 已拆 `IngestDatasource` (`"file" | "url" | "api"`) / `StoredDatasource` (`"file" | "manual" | "api"`),旧 `Datasource` 是 `IngestDatasource` 的 deprecated alias;`ingest_to_stored_datasource(ingest, source)` 是唯一合法转换入口。
> - `src/rag/ingest/structure/` 目录**只剩空 `__pycache__`**,源文件已全删(`DocumentStructure` / `Heading` / `MarkdownStructureExtractor` / `HtmlStructureExtractor` 均 dead code);doc-level heading 在 `_derive_title` 走 regex 现场抽,不再维护 heading 树。
> - `chunk_repo` 走 mapper 层: `src/rag/infra/pg/mappers.py` + `repositories/chunk_repo.py`。
> - `ScoredDocument` (在 `src/rag/domain/document.py`) **已删 `q` / `a` 字段**,只保留 `chunk_id / dataset_id / text / score / rank / source / modality / image_path / metadata / embedding / rerank_score`;`RetrievalTrace` (在 `src/rag/retrieval/trace.py`) 字段为 `q: str | None` + `a: str | None`(**非**原 task15 描述的 `query / dataset_id / chunk_id / score / source / latency_ms`),作为平行数组传给 `remove_duplicates(docs, traces)`。
>
> **历史溯源**(本 task 原始描述):原 plan 写 stub-first (audit #1 P1-1) + subagent #9 修复 4 项,详见下方。原描述保留为溯源依据。注意:原 "实际交付" 段对 `RetrievalTrace` 字段名、`ReaderError.code` 分组(原 task 写 `"encoding" / "parse" / "not_found" / "permission" / "too_large" / "unsupported"` 六类)的描述,即便 task 真的实现,字段集也需对齐 `src/rag/retrieval/trace.py` 与 `src/rag/error_codes.py` 的实际签名。

> 源 spec: `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` §7.0.3 检索审计 (`retrieval/audit.py`) + §7.7 引用校验工具 (`retrieval/citation_check.py`) — Task 15 (Audit & CitationChecker) 在重构 plan `2026-06-11-chunker-reader-refactor.md` 中被整体 SCOPED OUT,原 spec 章节文本未迁移。
>
> Fixes applied:
> - (audit #1 P1-1) stub-first 违反: 加 Step 0 stub(`class RetrievalAudit: async def record(self, *a, **kw): pass` + `class CitationChecker: def check(self, *a, **kw): raise NotImplementedError` 占位),确保 RED 阶段模块可 import 而非 ImportError。
> - (subagent #9) `RetrievalAudit.record` 接受 `latency_ms: dict[str, float] | None = None` 可选参数,主 plan L3751 已使用 `dict | None` 语法,保留并明确 Pydantic / JSON 序列化容错。
> - (subagent #9) `CitationCheckResult.hallucinated_citations` 改用 `list[str]`(`["<invalid:5>"]` 占位符),与 spec §7.7 `Citation` 类型列表语义区分:越界时没有真实 Citation 可引用,主 plan L3731 已采用 `f"<invalid:{i+1}>"`。
> - (subagent #9) `tail(n)` 方法用于 CLI `rag audit --last=20`(Task 17 依赖),不写 `pathlib` 锁,debug 场景接受 L6 trade-off: 并发写可能行交错(主 plan L3760 注释明确接受)。
> - (subagent #9) Citation regex `\[([\d,\s]+)\]`(H6 修正)保留主 plan L3710 写法,支持 `[1]` `[1,2,3]` `[1, 2, 3]` 格式。

## Open P0s (2026-06-14 audit)

| P0 ID | 描述 | 文件:行 | 解决路径 |
|---|---|---|---|
| P0-1 (产品决策) | FastGPT **无 JSONL 旁路审计**, rag-pipeline 引入 JSONL audit (`Path.open("a")` 写 `audit_log.jsonl`) 是新增 side-effect, 需决策: 本地 debug only / 走 OTel SDK / 接 Mongo 业务审计 / 完全 SCOPED OUT | task15.md:43-45, 319-321 | M2 (5e) — Option A: docstring 标 "JSONL 是本地 debug 工具, production 路径后续 spec 定义", 不阻塞实现; 或维持 SCOPED OUT (推荐) |
| P0-2 (spec 引用无效) | task15.md:28 引用 `2026-06-10-python-rag-pipeline.md:3627-3783`, plan 仅 505 行, 范围不存在; spec §7.0.3 / §7.7 文本在重构 plan 中未迁移, 12 字段 AuditRecord / 4 指标 CitationCheckResult 字段集无原文依据 | task15.md:28 | M2 (5e) — 改 spec 引用为文件名+章节标题(无行号), 或从 git history 提取原 spec 段落存 `.agents/design/2026-06-10-spec-extracts.md` |
| P0-3 (RetrievalTrace 字段冲突) | task15 旧描述 `RetrievalTrace` 6 字段 (`query / dataset_id / chunk_id / score / source / latency_ms`) 与实际 `q / a` 2 字段不符; `record` 写入的 `query` / `latency_ms` 字段没有 producer, audit log 永远是稀疏空对象 | task15.md:14, 286-296 | M2 (5e) — 恢复时重写 Step 4 注释, 显式说明每个 audit 字段来源 (SearchRequest.query / RetrievalTrace.q / caller 测时 latency_ms) |

详细分析见 `audit/2026-06-14-task15-alignment.md` §5 (修复建议)。

**Files:**
- Create: `src/rag/retrieval/audit.py`
- Create: `src/rag/retrieval/citation_check.py`
- Create: `tests/unit/test_citation_check.py`

**Spec 引用**:
- §0.1 流水线全景图(本项目视角): 主流水线 Cite 之后旁路挂载 `RetrievalAudit`,写 `audit_log.jsonl`,**旁路, 不阻塞主流程**。
- §7.0.3 检索审计(`retrieval/audit.py`): `trace(query, result) -> AuditRecord`,写 jsonl 文件,CLI `rag audit --last=20` 查看。
- §7.7 引用校验工具(`retrieval/citation_check.py`): 工具而非流水线节点,由 caller 在 LLM 生成回答后显式调用。

---

- [ ] **Step 0: 写 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# src/rag/retrieval/audit.py (stub)
class RetrievalAudit:
    """Stub: 待实现 (Task 15 Step 4)。"""

    def __init__(self, log_path: str = "audit_log.jsonl"):
        self.log_path = log_path

    async def record(self, query, result, latency_ms=None):
        pass

    def tail(self, n: int = 20):
        return []
```

```python
# src/rag/retrieval/citation_check.py (stub)
class CitationCheckResult:
    """Stub: 待实现 (Task 15 Step 3)。"""

    def __init__(self, recall=0.0, precision=0.0,
                 hallucinated_citations=None, unused_citations=None):
        self.recall = recall
        self.precision = precision
        self.hallucinated_citations = hallucinated_citations or []
        self.unused_citations = unused_citations or []


class CitationChecker:
    """Stub: 待实现 (Task 15 Step 3)。"""

    def check(self, llm_response, citations):
        raise NotImplementedError
```

```bash
# 验证 stub 可 import:
uv run python -c "from rag.retrieval.audit import RetrievalAudit; from rag.retrieval.citation_check import CitationChecker; print('stub ok')"
# 期望: stub ok
```

- [ ] **Step 1: 写失败单测 (citation check — TDD RED)**

```python
# tests/unit/test_citation_check.py
import uuid
import pytest
from rag.domain.search import Citation
from rag.retrieval.citation_check import CitationChecker


def _c():
    return Citation(
        chunk_id=uuid.uuid4(), dataset_id=uuid.uuid4(),
        source_name="f.md", content="x", score=0.5,
    )


def test_normal_citation():
    """正常引用: [1] [2] 全部对应真实 citations。"""
    citations = [_c(), _c()]
    response = "根据 [1] 和 [2] 的内容, ..."
    result = CitationChecker().check(response, citations)
    assert result.recall == 1.0   # 2/2 used
    assert result.precision == 1.0  # 2/2 used
    assert result.hallucinated_citations == []


def test_hallucinated_citation():
    """幻觉引用: [5] 越界 (citations 只有 1 个)。"""
    citations = [_c()]
    response = "根据 [5] 的内容, ..."
    result = CitationChecker().check(response, citations)
    assert result.hallucinated_citations   # 5 越界
    assert "<invalid:5>" in result.hallucinated_citations


def test_unused_citation():
    """未引用 citation: 提供了 2 个, LLM 只用 [1]。"""
    citations = [_c(), _c()]
    response = "看 [1] 就够了"
    result = CitationChecker().check(response, citations)
    assert len(result.unused_citations) == 1
    assert result.precision == 0.5   # 1/2 used


def test_comma_separated_citations():
    """H6 修正: LLM 常见 [1,2,3] 格式应正确解析。"""
    citations = [_c(), _c(), _c()]
    response = "根据参考资料 [1,2,3] 的内容"
    result = CitationChecker().check(response, citations)
    assert result.recall == 1.0
    assert len(result.hallucinated_citations) == 0


def test_space_separated_citations():
    """H6 修正: [1, 2, 3] 带空格格式。"""
    citations = [_c(), _c()]
    response = "如 [1, 2] 所述"
    result = CitationChecker().check(response, citations)
    assert result.recall == 1.0
    assert len(result.hallucinated_citations) == 0
```

- [ ] **Step 2: 跑测试,确认 fail (RED)**

```bash
uv run pytest tests/unit/test_citation_check.py -v
# 期望: 5 failed (NotImplementedError, stub 不满足断言)
```

- [ ] **Step 3: 写 citation_check.py (GREEN — 实现 H6 regex + 4 项指标)**

```python
# src/rag/retrieval/citation_check.py
import re
from pydantic import BaseModel
from rag.domain.search import Citation


class CitationCheckResult(BaseModel):
    """引用校验结果。

    Spec §7.7:
    - recall: LLM 引用的编号中有多少对应有效 citation
    - precision: 提供的 citations 中有多少被实际引用
    - hallucinated_citations: 越界引用 (idx >= len(citations))
    - unused_citations: 提供的 citation 中未被 LLM 引用的
    """
    recall: float                          # 引用有效率
    precision: float                       # citations 实际被用比例
    hallucinated_citations: list[str]      # 越界引用 (<invalid:n> 占位)
    unused_citations: list[Citation]       # 未被引用


class CitationChecker:
    """校验 LLM 回答中的 [n] 引用编号是否对应真实 citations。

    Spec §7.7 工具,非流水线节点 — caller 在 LLM 生成回答后显式调用。
    H6 修正: 支持 [1] [1,2,3] [1, 2, 3] 等 LLM 常见引用格式。
    """

    # H6 修正: regex 同时支持单编号 [1] 与多编号 [1,2,3] [1, 2, 3]
    _CITE_RE = re.compile(r"\[([\d,\s]+)\]")

    def check(self, llm_response: str, citations: list[Citation]) -> CitationCheckResult:
        """提取 LLM 回答中的 [n] 引用编号,计算 recall/precision/越界/未用。"""
        # 1) 提取 LLM 回答中所有 [n,...] 引用编号
        cited_nums: list[int] = []
        for m in self._CITE_RE.findall(llm_response):
            for num in m.split(","):
                num = num.strip()
                if num.isdigit():
                    cited_nums.append(int(num))

        # 2) 1-based → 0-based
        cited_idx = [n - 1 for n in cited_nums]

        # 3) Recall: 有效引用 / 总引用 (越界不算有效)
        valid_idx = [i for i in cited_idx if 0 <= i < len(citations)]
        invalid = [i for i in cited_idx if i not in valid_idx]
        recall = len(valid_idx) / max(len(cited_idx), 1)

        # 4) Precision: 提供 citations 中被引用的比例
        used = set(valid_idx)
        precision = len(used) / max(len(citations), 1)

        return CitationCheckResult(
            recall=recall,
            precision=precision,
            hallucinated_citations=[f"<invalid:{i+1}>" for i in invalid],
            unused_citations=[c for i, c in enumerate(citations) if i not in used],
        )
```

- [ ] **Step 4: 写 audit.py (trace 写 jsonl + tail 读)**

```python
# src/rag/retrieval/audit.py
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from pydantic import BaseModel, Field


class AuditRecord(BaseModel):
    """P0-22 修复 (audit #7): 类型化审计记录。

    原实现 record 字段是内联 dict,无 schema,字段集降级(query_variants /
    per_dataset / cache_hits / global_ranking / final_citations 全缺)。
    本类统一所有审计字段,保证下游 audit_log.jsonl → query_ext / metrics
    聚合时 schema 稳定。
    """
    ts: str
    query: str
    query_variants: list[str] = Field(default_factory=list)
    per_dataset: dict[str, dict[str, Any]] = Field(default_factory=dict)
    cache_hits: dict[str, bool] = Field(
        default_factory=lambda: {"L1": False, "L2": False, "L3": False, "L4": False}
    )
    global_ranking: list[dict[str, Any]] = Field(default_factory=list)
    final_citations: list[dict[str, Any]] = Field(default_factory=list)
    failed_dataset_ids: list[uuid.UUID] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    citation_count: int = 0
    latency_ms: dict[str, float] = Field(default_factory=dict)


class RetrievalAudit:
    """检索审计: 记录 trace 写到 jsonl 文件。

    Spec §0.1: 主流水线 Cite 之后**旁路挂载**, 不阻塞主流程。
    Spec §7.0.3: trace(query, result) -> AuditRecord, 写 audit_log.jsonl。

    L6 trade-off (主 plan L3760 注释):
        jsonl 写入非原子, 并发 search 可能导致行交错。
        debug 场景可接受, production 可加 fcntl.flock。
    """

    def __init__(self, log_path: str = "audit_log.jsonl"):
        self.log_path = Path(log_path)

    async def record(
        self,
        query: str,
        result,
        latency_ms: dict[str, float] | None = None,
        query_variants: list[str] | None = None,
        per_dataset: dict[str, dict[str, Any]] | None = None,
        cache_hits: dict[str, bool] | None = None,
        global_ranking: list[dict[str, Any]] | None = None,
    ):
        """记录一次检索 trace。

        P0-22 修复 (audit #7): 新增 query_variants / per_dataset / cache_hits /
        global_ranking 参数,缺失默认空,所有字段最终落到 AuditRecord。

        result: SearchResult 对象 (Task 2 已定义) — 含 citations / failed_dataset_ids / warnings。
        latency_ms: 可选, 各 stage 耗时 (e.g. {"embed": 120, "rerank": 250, "fuse": 5})。
        query_variants: 来自 QueryExtensionRunnable (task13) 输出的子查询。
        per_dataset: {"<ds_id>": {"hits": int, "filtered": int, "top_score": float}, ...}。
        cache_hits: {"L1": bool, "L2": bool, "L3": bool, "L4": bool}。
        global_ranking: inter_dataset_fusion 后的 ranked hits 快照 (["{"chunk_id": ..., "score": ...}"])。
        """
        citations = getattr(result, "citations", []) or []
        rec = AuditRecord(
            ts=datetime.utcnow().isoformat(),
            query=query,
            query_variants=list(query_variants or []),
            per_dataset=dict(per_dataset or {}),
            cache_hits=dict(cache_hits or {"L1": False, "L2": False, "L3": False, "L4": False}),
            global_ranking=list(global_ranking or []),
            final_citations=[
                {
                    "chunk_id": str(c.chunk_id),
                    "dataset_id": str(c.dataset_id),
                    "source_name": c.source_name,
                    "score": c.score,
                }
                for c in citations
            ],
            failed_dataset_ids=list(getattr(result, "failed_dataset_ids", []) or []),
            warnings=list(getattr(result, "warnings", []) or []),
            citation_count=len(citations),
            latency_ms=dict(latency_ms or {}),
        )
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(rec.model_dump_json() + "\n")

    def tail(self, n: int = 20) -> list[dict]:
        """读最近 n 条 trace — CLI `rag audit --last=20` 使用 (Task 17 依赖)。"""
        if not self.log_path.exists():
            return []
        with self.log_path.open() as f:
            lines = f.readlines()
        return [json.loads(l) for l in lines[-n:]]
```

- [ ] **Step 5: 跑测试,确认 pass (GREEN)**

```bash
uv run pytest tests/unit/test_citation_check.py -v
# 期望: 5 passed
# 验证 audit stub 仍可 import:
uv run python -c "from rag.retrieval.audit import RetrievalAudit; import asyncio; asyncio.run(RetrievalAudit('/tmp/_t.jsonl').record('q', type('R', (), {'failed_dataset_ids': [], 'warnings': [], 'citations': []})())); print('audit ok')"
# 期望: audit ok + /tmp/_t.jsonl 含 1 行
```

- [ ] **Step 6: commit**

```bash
git add src/rag/retrieval tests/
git commit -m "feat(retrieval): audit (jsonl trace) + citation checker

- RetrievalAudit: 写 audit_log.jsonl 旁路记录, 主流水线 Cite 之后挂载 (spec §0.1 / §7.0.3)
- CitationChecker: LLM 回答中的 [n] 引用校验, recall/precision/越界/未用 4 项指标 (spec §7.7)
- H6 regex: \\[([\\d,\\s]+)\\] 兼容 [1] [1,2,3] [1, 2, 3] 格式
- L6 trade-off: jsonl 写入非原子, debug 可接受"
```

---

**Step 7: verify 跑通 Step 5 的 5 个 case**

| Case | 验证项 |
|------|--------|
| `test_normal_citation` | recall=1.0, precision=1.0, hallucinated=[] |
| `test_hallucinated_citation` | `[5]` 越界 → `hallucinated_citations=["<invalid:5>"]` |
| `test_unused_citation` | 2 个 citations LLM 只用 1 个 → precision=0.5, unused=1 |
| `test_comma_separated_citations` | `[1,2,3]` → 3 个全部有效 |
| `test_space_separated_citations` | `[1, 2]` → 2 个全部有效 (H6 regex strip) |

**Step 8: 落地 finding**

| Finding | 位置 | 处理 |
|---------|------|------|
| H6 regex `\[([\d,\s]+)\]` | `citation_check.py:24` | 支持 `[1]` `[1,2,3]` `[1, 2, 3]` 三种格式 |
| `hallucinated_citations: list[str]` | `citation_check.py:18` | 越界时返回 `"<invalid:n>"` 占位 (主 plan L3731) |
| `tail(n)` 读法 | `audit.py:50` | 给 Task 17 `rag audit --last=20` 用, 全文件读 (debug 规模可接受) |
| L6 jsonl 非原子写 | `audit.py:36` | 文档化 trade-off, production 加 `fcntl.flock` |

**禁止**:
- 不修改主 plan 文件 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md`。
- 不修改其他 task 文件 (task1-14, task16-20)。
- 审计写盘采用 `Path.open("a")` 追加模式, 不重写已存在文件。
