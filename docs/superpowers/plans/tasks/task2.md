# Task 2: Domain Models (Pydantic v2)

**Files:**
- Create: `src/rag/domain/__init__.py`
- Create: `src/rag/domain/dataset.py`
- Create: `src/rag/domain/document.py`
- Create: `src/rag/domain/search.py`
- Create: `tests/unit/test_domain.py`

- [ ] **Step 0: 创建 domain 包占位 (先创建空 __init__.py，避免 import 失败)**

```python
# src/rag/domain/__init__.py (stub — 等 models 创建后再更新)
# Domain models — see dataset.py, document.py, search.py
```

- [ ] **Step 1: 写 dataset.py**

```python
# src/rag/domain/dataset.py
import uuid
from datetime import datetime, timezone
from pydantic import BaseModel, Field

DEFAULT_PROMPT_TEMPLATE = """基于以下参考资料回答用户问题。

## 参考资料
{citations}

## 用户问题
{query}

## 回答"""

DEFAULT_SYSTEM_PROMPT = "你是一个基于参考资料回答问题的助手。请严格依据提供的参考资料,不要编造信息。如果参考资料不足以回答问题,请明确说明。"

class Dataset(BaseModel):
    """知识库配置: 一个 Dataset 等价于一个独立的 RAG 知识库。"""
    id: uuid.UUID
    name: str
    embed_model: str
    embed_dim: int
    chunk_size: int = 1000
    rerank_model: str | None = None
    rrf_k: int = 60                  # spec §0.1: per-dataset RRF 参数, Cormack 2009
    query_select_alpha: float = 0.3  # M6: Stage 2 submodular α (0=多样性, 1=相关性)
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    # ── P0-2 修复 (audit #5 关联): 默认 "" 与 SQL DDL DEFAULT '' 对齐 ──
    # 应用层在 build_prompt() 检测到空字符串时,回退使用 DEFAULT_PROMPT_TEMPLATE
    prompt_template: str = ""
    system_prompt: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
```

- [ ] **Step 2: 写 document.py**

```python
# src/rag/domain/document.py
import uuid
from datetime import datetime
from typing import Literal
from pydantic import BaseModel

class ChunkMetadata(BaseModel):
    """Chunk 的元数据载荷: 定位与溯源信息。"""
    dataset_id: uuid.UUID
    datasource: Literal["file", "manual", "api"]
    filename: str | None = None
    parent_title: str = ""
    chunk_index: int = 0
    custom_separator: str | None = None
    created_at: datetime | None = None

class Chunk(BaseModel):
    """入库前的原始 Chunk: reader + chunker 出来的内容块。"""
    id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None
    metadata: ChunkMetadata
    embedding: list[float] | None = None

class ScoredDocument(BaseModel):
    """召回结果: RRF 公式需要 score + rank 同时存。

    扩展字段:
    - q / a: 触发该 chunk 的 query 变体与该变体下的 top-1 答案片段,
             用于 ``remove_duplicates`` 按 (q, a) 元组做去重。
    - rerank_score: rerank 模型返回的独立相关性分数 (0~1),
             在 ``filter_by_score`` 切换 rerank 模式时取代 score 进行阈值过滤。
    """
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    text: str
    score: float
    rank: int
    source: Literal["vector", "fulltext", "caption", "rerank"]
    modality: Literal["text", "image_caption"] = "text"
    image_path: str | None = None    # modality=image_caption 时有值, cite 组装引用用
    metadata: ChunkMetadata
    embedding: list[float] | None = None
    q: str | None = None             # remove_duplicates 用: 触发该 chunk 的 query 变体
    a: str | None = None             # remove_duplicates 用: 该变体下 chunk 的 top-1 答案片段
    rerank_score: float | None = None  # filter_by_score 切换 rerank 时用的相关性分数
```

- [ ] **Step 3: 写 search.py (含 resolve_rerank_model)**

```python
# src/rag/domain/search.py
import uuid
from datetime import datetime
from pydantic import BaseModel

class SearchRequest(BaseModel):
    """用户搜索请求: 必填 3 个 + 默认配置。"""
    query: str
    image_urls: list[str] = []
    dataset_ids: list[uuid.UUID]
    top_k: int = 10
    score_threshold: float | None = None
    use_rerank: bool = True
    rerank_model: str | None = None
    rerank_weight: float = 0.5     # M3: Stage 2 RRF 混合权重, 向量侧与 rerank 侧各占 0.5
    query_extension: bool = True
    max_query_variants: int = 3
    max_tokens: int = 4000
    embedding_model: str | None = None
    temperature: float = 0.1
    query_decomposition: bool = False
    parent_doc_window: int = 0
    use_global_rerank: bool = False
    audit: bool = False
    chat_bg: str = ""                            # C5: 多轮对话背景 (指代消解用)
    histories: list[dict] = []                    # C5: 对话历史 [{"role":"user","content":"..."}]

class Citation(BaseModel):
    """返回给前端的引用条目 DTO。"""
    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    source_name: str
    content: str
    image_path: str | None = None
    score: float
    update_time: datetime | None = None

class SearchResult(BaseModel):
    """Search 接口完整响应。"""
    citations: list[Citation]
    prompt: str
    failed_dataset_ids: list[uuid.UUID] = []
    warnings: list[str] = []

def resolve_rerank_model(req: SearchRequest, dataset) -> str | None:
    """Spec §3: 解析 rerank 模型优先级: req.rerank_model > dataset.rerank_model > None。"""
    if not req.use_rerank:
        return None
    return req.rerank_model or getattr(dataset, 'rerank_model', None)
```

- [ ] **Step 3.5: 更新 __init__.py (H1 修正: 所有 model 文件已存在后再 re-export)**

```python
# src/rag/domain/__init__.py (重新写入完整 export)
from rag.domain.dataset import Dataset, DEFAULT_PROMPT_TEMPLATE, DEFAULT_SYSTEM_PROMPT
from rag.domain.document import Chunk, ChunkMetadata, ScoredDocument
from rag.domain.search import Citation, SearchRequest, SearchResult, resolve_rerank_model

__all__ = ["Dataset", "DEFAULT_PROMPT_TEMPLATE", "DEFAULT_SYSTEM_PROMPT",
           "Chunk", "ChunkMetadata", "ScoredDocument",
           "Citation", "SearchRequest", "SearchResult", "resolve_rerank_model"]
```

- [ ] **Step 4: 写测试**

```python
# tests/unit/test_domain.py
import uuid
from rag.domain.dataset import Dataset
from rag.domain.document import Chunk, ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest, Citation, SearchResult, resolve_rerank_model

def test_dataset_creation():
    ds = Dataset(id=uuid.uuid4(), name="test", embed_model="text-embedding-3-small", embed_dim=1536)
    assert ds.vector_weight == 0.7  # default
    assert ds.prompt_template  # non-empty default

def test_chunk_requires_dataset_id():
    meta = ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file")
    chunk = Chunk(id=uuid.uuid4(), dataset_id=meta.dataset_id, text="hello", metadata=meta)
    assert chunk.modality == "text"  # default
    assert chunk.image_path is None

def test_scored_document_has_image_path():
    meta = ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file")
    doc = ScoredDocument(
        chunk_id=uuid.uuid4(), dataset_id=meta.dataset_id,
        text="x", score=0.5, rank=0, source="vector", metadata=meta,
        modality="image_caption", image_path="/img/1.png",
    )
    assert doc.image_path == "/img/1.png"

def test_search_request_minimum_fields():
    req = SearchRequest(query="q", dataset_ids=[uuid.uuid4()])
    assert req.top_k == 10
    assert req.use_rerank is True
    assert req.query_decomposition is False  # default off

def test_search_result_has_failure_signals():
    r = SearchResult(citations=[], prompt="")
    assert r.failed_dataset_ids == []
    assert r.warnings == []

def test_resolve_rerank_model():
    req = SearchRequest(query="q", dataset_ids=[uuid.uuid4()], rerank_model="cohere")
    class FakeDS: rerank_model = None
    assert resolve_rerank_model(req, FakeDS()) == "cohere"
    req2 = SearchRequest(query="q", dataset_ids=[uuid.uuid4()], use_rerank=False)
    assert resolve_rerank_model(req2, FakeDS()) is None
```

- [ ] **Step 5: 跑测试,确认 pass (domain 是纯数据, 无逻辑需 fail)**

```bash
uv run pytest tests/unit/test_domain.py -v
# 期望: 6 passed
```

- [ ] **Step 6: commit**

```bash
git add src/rag/domain tests/unit/test_domain.py
git commit -m "feat(domain): add Dataset / Chunk / SearchRequest Pydantic models"
```
