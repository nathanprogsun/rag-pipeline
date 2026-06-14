# Task 5: Fulltext Retriever (jieba + tsvector)

> Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 1089-1232).
>
> Fixes applied:
> - (audit #1 P1-1) stub-first 违反: 加 Step 0 stub(`def tokenize_chinese(text): return []` 占位) + Step 0 内容

**Files:**
- Create: `src/rag/infra/pg/fulltext_store.py`
- Create: `tests/unit/test_fulltext_store.py`
- Create: `tests/integration/test_fulltext_retrieval.py`

- [ ] **Step 0: 写 stub (audit #1 P1-1 修正: 先 stub 后 test,确保 RED 阶段模块可 import)**

```python
# src/rag/infra/pg/fulltext_store.py (stub)
import uuid

def tokenize_chinese(text: str) -> list[str]:
    """Stub: 待实现 jieba 分词。"""
    return []

class FulltextRetriever:
    def __init__(self, dataset_id):
        self.dataset_id = dataset_id
    async def search(self, query, top_k=10):
        return []
```

- [ ] **Step 1: 写失败单测**

```python
# tests/unit/test_fulltext_store.py
import pytest
from rag.infra.pg.fulltext_store import tokenize_chinese

def test_tokenize_chinese():
    tokens = tokenize_chinese("Python 是一种编程语言,常用于数据科学。")
    assert "Python" in tokens
    assert "编程" in tokens
    assert "数据" in tokens
    assert "语言" in tokens
```

- [ ] **Step 2: 跑测试,确认 fail**

```bash
uv run pytest tests/unit/test_fulltext_store.py -v
# 期望: 1 failed (tokenize_chinese 返回 [],断言失败 — 非 ImportError)
```

- [ ] **Step 3: 写 fulltext_store.py**

```python
# src/rag/infra/pg/fulltext_store.py
import uuid
import jieba
from langchain_core.runnables import Runnable
from rag.infra.pg.database import AsyncSessionLocal
from rag.domain.document import ScoredDocument, ChunkMetadata

# jieba 加载一次, 进程级共享
_jieba_loaded = False
def _ensure_jieba():
    global _jieba_loaded
    if not _jieba_loaded:
        jieba.initialize()
        _jieba_loaded = True

def tokenize_chinese(text: str) -> list[str]:
    """应用层 jieba 分词, 空格 join 用于 tsvector。"""
    _ensure_jieba()
    return [t for t in jieba.cut(text) if t.strip()]

def build_tsvector(text: str) -> str:
    """把 jieba 分词结果转 tsvector 字面量。"""
    tokens = tokenize_chinese(text)
    return " ".join(tokens)

class FulltextRetriever(Runnable):
    """jieba 预分词 + tsvector GIN 检索。每次 search 创建新 session。"""

    def __init__(self, dataset_id: uuid.UUID):
        self.dataset_id = dataset_id

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        from rag.infra.pg.database import AsyncSessionLocal
        from rag.infra.pg.repositories.chunk_repo import ChunkRepository

        tokens = tokenize_chinese(query)
        ts_query = " & ".join(tokens)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_fulltext(ts_query, self.dataset_id, top_k)

        return [
            ScoredDocument(
                chunk_id=row.id, dataset_id=row.dataset_id,
                text=row.text, score=score, rank=i,
                source="fulltext", modality=row.modality,
                image_path=row.image_path,
                metadata=ChunkMetadata(
                    dataset_id=row.dataset_id, datasource="file",
                    filename=row.filename, parent_title=row.parent_title,
                    chunk_index=row.chunk_index, created_at=row.created_at,
                ),
            )
            for i, (row, score) in enumerate(rows)
        ]

    async def ainvoke(self, input, config=None):
        return await self.search(input["query"], input.get("top_k", 10))

    def invoke(self, input, config=None):
        import asyncio
        try:
            _loop = asyncio.get_running_loop()
        except RuntimeError:
            _loop = None
        return asyncio.run(self.ainvoke(input, config))
```

- [ ] **Step 4: 写集成测试**

```python
# tests/integration/test_fulltext_retrieval.py
import pytest, uuid
from sqlalchemy import text

@pytest.mark.asyncio
async def test_chinese_tokenization_and_search(db_session):
    dataset_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO datasets (id, name, embed_model, embed_dim) VALUES (:id, 't', 'fake', 1536)"),
        {"id": dataset_id},
    )
    from rag.infra.pg.fulltext_store import build_tsvector
    await db_session.execute(
        text("INSERT INTO chunks (id, dataset_id, text, embedding, ts_tokens) "
             "VALUES (:id, :ds, :text, :vec::vector, to_tsvector('simple', :tokens))"),
        {"id": uuid.uuid4(), "ds": dataset_id, "text": "Python 教程 入门",
         "vec": str([0.0]*1536), "tokens": build_tsvector("Python 教程 入门")},
    )
    await db_session.commit()

    with patch("rag.infra.pg.fulltext_store.AsyncSessionLocal", return_value=db_session):
        from rag.infra.pg.fulltext_store import FulltextRetriever
        rt = FulltextRetriever(dataset_id)
        hits = await rt.search("Python 教程", top_k=5)
    assert len(hits) >= 1
    assert "Python" in hits[0].text
```

- [ ] **Step 5: 跑全部测试**

```bash
uv run pytest tests/unit/test_fulltext_store.py tests/integration/test_fulltext_retrieval.py -v
# 期望: 2 passed
```

- [ ] **Step 6: commit**

```bash
git add src/rag/infra/pg/fulltext_store.py tests/
git commit -m "feat(pg): fulltext retriever with jieba + tsvector GIN"
```
