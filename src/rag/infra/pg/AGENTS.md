# PG 层规范（`src/rag/infra/pg/`）

## Base 与时间戳

- 所有表继承 `base.py` 中的 `Base` 与 mixin。
- `TimestampMixin`：`created_at`、`updated_at` — `DateTime(timezone=True)`，`server_default=func.now()`，`updated_at` 使用 `onupdate=func.now()`。
- `SoftDeleteMixin`：可空 `deleted_at`。`base.py` 中通过 `Session.do_orm_execute` + `with_loader_criteria` 全局过滤已删行；需包含已删数据时用 `.execution_options(include_deleted=True)`。
- 增删列时同步更新 `schema.sql`。

## Models

- `models/` 下一模型一文件；仅在 `models/__init__.py` 中 re-export。
- **禁止 `relationship()`** — 仅用 `dataset_id` 等 FK 列，模型文件之间不互相 import 做 ORM 导航。
- pgvector：`Vector(1536)`；测试中 `create_all` 前须 `CREATE EXTENSION vector`。
- 检查约束与索引写在 `__table_args__`（参考 `ChunkModel`）。

## Repositories

- 所有方法须有完整类型注解（含 `-> None`）；禁止裸 `Any`（见根 `AGENTS.md`）。
- 构造函数接收 `AsyncSession`；事务边界（`commit`/`rollback`）由调用方负责。
- 所有 SQLAlchemy import 在文件顶部（含 `text`、`func`、`select`、`update`）。
- 向量检索前执行 `SET LOCAL hnsw.ef_search = {max(top_k * 2, 40)}`。
- 软删除：`UPDATE ... SET deleted_at = func.now()`，不用 `delete()`；读操作依赖全局软删过滤。

## 数据库初始化

- `database.py`：`create_async_engine(str(settings.database_url))`；共享 `engine` 与 `AsyncSessionLocal`。
- `init_pool()` 用于开发环境自动建表；生产环境用 Alembic（尚未接入，勿自行编造迁移工具）。

## documents 表与 chunk 唯一性

- `documents` 是 `datasets` 与 `chunks` 之间的一级实体: 一个 `(dataset_id, filename)` 对应一个 active document (由 `documents_active_uniq` 部分唯一索引保证)。
- `chunks.document_id` 为 NOT NULL FK; `(document_id, chunk_index)` 由 `chunks_document_chunk_idx_uniq` 部分唯一索引保证不重复。
- 同一文件重 ingest 时, 通过 `documents.generation` 区分版本; 读取时取 `MAX(generation)` 决定可见性。
- `datasets.name` UNIQUE: CLI `--dataset-name` 走 get-or-create; 重跑同名 ingest 复用已有 `dataset_id`, document/chunk 按 filename upsert。
