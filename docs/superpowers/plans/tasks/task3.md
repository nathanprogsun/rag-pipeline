# Task 3: PG — database.py + base.py + Models + Repositories

**Files:**
- Create: `src/rag/infra/__init__.py`
- Create: `src/rag/infra/pg/__init__.py`
- Create: `src/rag/infra/pg/database.py`     # engine + AsyncSessionLocal
- Create: `src/rag/infra/pg/base.py`         # DeclarativeBase + TimestampMixin
- Create: `src/rag/infra/pg/models/__init__.py`
- Create: `src/rag/infra/pg/models/dataset.py`
- Create: `src/rag/infra/pg/models/chunk.py`
- Create: `src/rag/infra/pg/repositories/__init__.py`
- Create: `src/rag/infra/pg/repositories/chunk_repo.py`
- Create: `src/rag/infra/pg/schema.sql`      # DDL 参考
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_pg_connection.py`

- [ ] **Step 0: 写集成测试 (AsyncSessionLocal + testcontainers)**

```python
# tests/integration/conftest.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def pg_url():
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        yield pg.get_connection_url().replace("psycopg2", "postgresql+asyncpg")

@pytest.fixture
async def db_session(pg_url):
    engine = create_async_engine(pg_url)
    from rag.infra.pg.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()
```

```python
# tests/integration/test_pg_connection.py
import pytest
from sqlalchemy import text

@pytest.mark.asyncio
async def test_schema_creates_datasets_table(db_session):
    result = await db_session.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name='datasets' AND column_name='id'")
    )
    assert result.scalar() == "id"

@pytest.mark.asyncio
async def test_extension_vector_enabled(db_session):
    result = await db_session.execute(
        text("SELECT extname FROM pg_extension WHERE extname='vector'")
    )
    assert result.scalar() == "vector"

@pytest.mark.asyncio
async def test_chunk_repository_roundtrip(db_session):
    """Repository 模式: insert → search → verify。"""
    import uuid
    from rag.infra.pg.repositories.chunk_repo import ChunkRepository
    from rag.infra.pg.models.dataset import DatasetModel
    from rag.infra.pg.models.chunk import ChunkModel

    ds = DatasetModel(id=uuid.uuid4(), name="t", embed_model="m", embed_dim=1536)  # P0-4 修复 (audit #5): embed_dim=3 → 1536 对齐 schema.sql  # P0-4: 与 schema.sql embed_dim=1536 对齐
    db_session.add(ds)
    await db_session.flush()

    c = ChunkModel(dataset_id=ds.id, text="test content", embedding=[0.0] * 1535 + [1.0])  # P0-4: 1536 维, 单测不再因维度不匹配崩
    db_session.add(c)
    await db_session.commit()

    repo = ChunkRepository(db_session)
    results = await repo.search_by_vector([1.0, 0.0, 0.0], ds.id, top_k=1)
    assert len(results) == 1
    assert results[0][0].text == "test content"
```

- [ ] **Step 1: 写 database.py + base.py (engine + AsyncSessionLocal)**

```python
# src/rag/infra/pg/database.py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from rag.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def close_pool():
    """L8: shutdown hook。长时间运行的服务应调用此函数释放连接池。
    命名与 asyncpg pool 保持一致 (init_pool / close_pool)。"""
    await engine.dispose()

async def init_pool():
    """Dev 用: 自动建表。Production 用 Alembic migration。
    命名与 asyncpg pool 保持一致 (init_pool / close_pool)。"""
    from rag.infra.pg.base import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

```python
# src/rag/infra/pg/base.py
from datetime import datetime
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime, func

class Base(DeclarativeBase):
    pass

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
```

- [ ] **Step 2: 写 models**

```python
# src/rag/infra/pg/models/__init__.py
from rag.infra.pg.models.dataset import DatasetModel
from rag.infra.pg.models.chunk import ChunkModel

__all__ = ["DatasetModel", "ChunkModel"]
```

```python
# src/rag/infra/pg/models/dataset.py
import uuid
from sqlalchemy import String, Integer, Float, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID
from rag.infra.pg.base import Base, TimestampMixin

class DatasetModel(Base, TimestampMixin):
    __tablename__ = "datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    embed_model: Mapped[str] = mapped_column(Text, nullable=False)
    embed_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000)
    rerank_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    rrf_k: Mapped[int] = mapped_column(Integer, default=60)   # spec §0.1: per-dataset RRF 参数
    vector_weight: Mapped[float] = mapped_column(Float, default=0.7)
    fulltext_weight: Mapped[float] = mapped_column(Float, default=0.3)
    query_select_alpha: Mapped[float] = mapped_column(Float, default=0.3)  # 对齐 FastGPT alpha=0.3 经验值
    prompt_template: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    chunks: Mapped[list["ChunkModel"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
```

```python
# src/rag/infra/pg/models/chunk.py
import uuid
from sqlalchemy import String, Integer, Text, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR
from pgvector.sqlalchemy import Vector
from rag.infra.pg.base import Base, TimestampMixin

class ChunkModel(Base, TimestampMixin):
    __tablename__ = "chunks"
    __table_args__ = (
        CheckConstraint("modality IN ('text', 'image_caption')", name="modality_chk"),
        CheckConstraint(
            "(modality = 'image_caption' AND image_path IS NOT NULL) OR (modality = 'text')",
            name="image_path_required",
        ),
        Index("chunks_dataset_id_idx", "dataset_id"),
        Index("chunks_modality_idx", "modality"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    modality: Mapped[str] = mapped_column(Text, default="text")
    image_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_title: Mapped[str] = mapped_column(Text, default="")
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding = mapped_column(Vector(1536), nullable=False)
    ts_tokens = mapped_column(TSVECTOR, nullable=True)

    dataset: Mapped["DatasetModel"] = relationship(back_populates="chunks")
```

- [ ] **Step 3: 写 chunk_repo.py (Repository 模式)**

```python
# src/rag/infra/pg/repositories/chunk_repo.py
import uuid
from sqlalchemy import select, func, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from rag.infra.pg.models.chunk import ChunkModel
from rag.infra.pg.models.dataset import DatasetModel

class ChunkRepository:
    """Chunk 数据访问 (Repository 模式). Session 由调用方注入。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── Vector ──────────────────────────────────────

    async def search_by_vector(
        self, query_vec: list[float], dataset_id: uuid.UUID, top_k: int = 10,
    ) -> list[tuple[ChunkModel, float]]:
        # H7: SET LOCAL ef_search to avoid silent truncation when top_k > 40
        from sqlalchemy import text as sa_text
        await self.session.execute(sa_text(f"SET LOCAL hnsw.ef_search = {max(top_k * 2, 40)}"))
        stmt = (
            select(ChunkModel, 1 - ChunkModel.embedding.cosine_distance(query_vec).label("score"))
            .where(ChunkModel.dataset_id == dataset_id)
            .order_by(ChunkModel.embedding.cosine_distance(query_vec))
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        return [(row.ChunkModel, float(row.score)) for row in result.all()]

    # ── Fulltext ────────────────────────────────────

    async def search_by_fulltext(
        self, ts_query: str, dataset_id: uuid.UUID, top_k: int = 10,
    ) -> list[tuple[ChunkModel, float]]:
        from sqlalchemy import text as sa_text
        stmt = (
            select(ChunkModel, func.ts_rank(ChunkModel.ts_tokens, func.to_tsquery('simple', ts_query)).label("score"))
            .where(
                ChunkModel.dataset_id == dataset_id,
                ChunkModel.ts_tokens.op("@@")(func.to_tsquery('simple', ts_query)),
            )
            .order_by(func.ts_rank(ChunkModel.ts_tokens, func.to_tsquery('simple', ts_query)).desc())
            .limit(top_k)
        )
        result = await self.session.execute(stmt)
        return [(row.ChunkModel, float(row.score)) for row in result.all()]

    # ── CRUD ────────────────────────────────────────

    async def delete_by_filename(self, dataset_id: uuid.UUID, filename: str):
        await self.session.execute(
            delete(ChunkModel).where(
                and_(ChunkModel.dataset_id == dataset_id, ChunkModel.filename == filename)
            )
        )

    async def bulk_insert(self, models: list[ChunkModel]):
        self.session.add_all(models)

    async def get_siblings(
        self, dataset_id: uuid.UUID, parent_title: str, lo: int, hi: int,
    ) -> list[ChunkModel]:
        stmt = (
            select(ChunkModel)
            .where(
                and_(
                    ChunkModel.dataset_id == dataset_id,
                    ChunkModel.parent_title == parent_title,
                    ChunkModel.chunk_index >= lo,
                    ChunkModel.chunk_index <= hi,
                )
            )
            .order_by(ChunkModel.chunk_index)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_dataset(self, dataset_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(ChunkModel).where(ChunkModel.dataset_id == dataset_id)
        result = await self.session.execute(stmt)
        return result.scalar() or 0
```

- [ ] **Step 4: 写 schema.sql (DDL 参考)**

```sql
-- src/rag/infra/pg/schema.sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS datasets (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL,
  embed_model     TEXT NOT NULL,
  embed_dim       INT  NOT NULL,
  chunk_size      INT  NOT NULL DEFAULT 1000,
  rerank_model    TEXT,
  rrf_k           INT  NOT NULL DEFAULT 60,        -- spec §0.1: per-dataset RRF 参数 (audit #4 补充)
  vector_weight   REAL NOT NULL DEFAULT 0.7,
  fulltext_weight REAL NOT NULL DEFAULT 0.3,
  query_select_alpha REAL NOT NULL DEFAULT 0.3,  -- 对齐 FastGPT alpha=0.3 经验值 (M6 Stage 2 submodular)
  prompt_template TEXT NOT NULL DEFAULT '',
  system_prompt   TEXT,                            -- audit #4 补充
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS chunks (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id    UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  text          TEXT NOT NULL,
  modality      TEXT NOT NULL DEFAULT 'text',
  image_path    TEXT,
  parent_title  TEXT NOT NULL DEFAULT '',
  chunk_index   INT  NOT NULL DEFAULT 0,
  filename      TEXT,
  embedding     VECTOR(1536) NOT NULL,
  ts_tokens     TSVECTOR,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT modality_chk CHECK (modality IN ('text', 'image_caption')),
  CONSTRAINT image_path_required CHECK (
    (modality = 'image_caption' AND image_path IS NOT NULL) OR (modality = 'text')
  )
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
  ON chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
CREATE INDEX IF NOT EXISTS chunks_ts_tokens_gin  ON chunks USING GIN (ts_tokens);
CREATE INDEX IF NOT EXISTS chunks_dataset_id_idx ON chunks (dataset_id);
CREATE INDEX IF NOT EXISTS chunks_modality_idx   ON chunks (modality);
```

- [ ] **Step 5: 跑测试,确认 pass**

```bash
uv run pytest tests/integration/test_pg_connection.py -v
# 期望: 2 passed (testcontainers 拉镜像,首次慢)
```

- [ ] **Step 6: commit**

```bash
git add src/rag/infra tests/integration
git commit -m "feat(pg): schema + asyncpg pool + testcontainers fixtures"
```
