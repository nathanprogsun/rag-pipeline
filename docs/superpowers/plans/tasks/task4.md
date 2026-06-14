# Task 4: Vector Retriever (HNSW cosine)

**Files:**
- Create: `src/rag/infra/pg/vector_store.py`
- Create: `tests/unit/test_vector_store.py`
- Create: `tests/integration/test_vector_retrieval.py`

- [ ] **Step 0: 写 stub**

```python
# src/rag/infra/pg/vector_store.py (stub)
class VectorRetriever:
    def __init__(self, dataset_id, embed_model):
        self.dataset_id = dataset_id
        self.embed_model = embed_model
    async def search(self, query, top_k=10) -> list:
        return []
```

- [ ] **Step 1: 写单测 (mock AsyncSessionLocal + ChunkRepository)**

```python
# tests/unit/test_vector_store.py
import pytest
from unittest.mock import AsyncMock, patch
from rag.infra.pg.vector_store import VectorRetriever

class FakeEmbeddings:
    async def aembed_query(self, text):
        return [0.1] * 1536

@pytest.mark.asyncio
async def test_vector_retriever_uses_repository():
    with patch("rag.infra.pg.vector_store.AsyncSessionLocal") as mock_factory:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_session.__aexit__.return_value = None
        mock_factory.return_value = mock_session

        from rag.infra.pg.repositories.chunk_repo import ChunkRepository
        with patch.object(ChunkRepository, "search_by_vector", return_value=[]) as mock_search:
            retriever = VectorRetriever(dataset_id="ds-1", embed_model=FakeEmbeddings())
            hits = await retriever.search("test", top_k=5)
            assert isinstance(hits, list)
            mock_search.assert_called_once()
```

- [ ] **Step 2: 跑测试 → RED**

```bash
uv run pytest tests/unit/test_vector_store.py -v
```

- [ ] **Step 3: 完整实现 (AsyncSessionLocal + ChunkRepository)**

```python
# src/rag/infra/pg/vector_store.py
import uuid
from langchain_core.embeddings import Embeddings
from langchain_core.runnables import Runnable
from rag.infra.pg.database import AsyncSessionLocal
from rag.infra.pg.repositories.chunk_repo import ChunkRepository
from rag.domain.document import ScoredDocument, ChunkMetadata

class VectorRetriever(Runnable):
    """pgvector HNSW 检索。每次 search 创建新 session, 完成后自动回收。"""

    def __init__(self, dataset_id: uuid.UUID, embed_model: Embeddings):
        self.dataset_id = dataset_id
        self.embed_model = embed_model

    async def search(self, query: str, top_k: int = 10) -> list[ScoredDocument]:
        vec = await self.embed_model.aembed_query(query)
        async with AsyncSessionLocal() as session:
            repo = ChunkRepository(session)
            rows = await repo.search_by_vector(vec, self.dataset_id, top_k)
        return [
            ScoredDocument(
                chunk_id=row.id, dataset_id=row.dataset_id,
                text=row.text, score=score, rank=i,
                source="vector", modality=row.modality,
                image_path=row.image_path,
                metadata=ChunkMetadata(
                    dataset_id=row.dataset_id, datasource="file",
                    filename=row.filename, parent_title=row.parent_title,
                    chunk_index=row.chunk_index, created_at=row.created_at,
                ),
            )
            for i, (row, score) in enumerate(rows)
        ]

    async def ainvoke(self, input: dict, config=None) -> list[ScoredDocument]:
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
# tests/integration/test_vector_retrieval.py
import pytest, uuid
from sqlalchemy import text

@pytest.mark.asyncio
async def test_hnsw_index_actually_used(db_session):
    dataset_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO datasets (id, name, embed_model, embed_dim) VALUES (:id, 'test', 'fake', 3)"),
        {"id": dataset_id},
    )
    vecs = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    for i, v in enumerate(vecs):
        await db_session.execute(
            text("INSERT INTO chunks (id, dataset_id, text, embedding) VALUES (:id, :ds, :text, :vec::vector)"),
            {"id": uuid.uuid4(), "ds": dataset_id, "text": f"text {i}", "vec": str(v)},
        )
    await db_session.commit()

    # patch AsyncSessionLocal 返回 test session
    from unittest.mock import patch
    with patch("rag.infra.pg.vector_store.AsyncSessionLocal", return_value=db_session):
        from rag.infra.pg.vector_store import VectorRetriever
        class E:
            async def aembed_query(self, t): return [1.0, 0.0, 0.0]
        rt = VectorRetriever(dataset_id, E())
        hits = await rt.search("x", top_k=2)
    assert len(hits) == 2
    assert hits[0].text == "text 0"
    assert hits[0].score > hits[1].score
```

- [ ] **Step 5: 跑全部测试**

```bash
uv run pytest tests/unit/test_vector_store.py tests/integration/test_vector_retrieval.py -v
# 期望: 2 passed
```

- [ ] **Step 6: commit**

```bash
git add src/rag/infra/pg/vector_store.py tests/
git commit -m "feat(pg): vector retriever with HNSW cosine + Runnable interface"
```
