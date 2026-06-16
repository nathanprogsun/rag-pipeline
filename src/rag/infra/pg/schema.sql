-- src/rag/infra/pg/schema.sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS datasets (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name            TEXT NOT NULL UNIQUE,
  embed_model     TEXT NOT NULL,
  embed_dim       INT  NOT NULL,
  chunk_size      INT  NOT NULL DEFAULT 1000,
  rerank_model    TEXT,
  rrf_k           INT  NOT NULL DEFAULT 60,
  vector_weight   REAL NOT NULL DEFAULT 0.7,
  fulltext_weight REAL NOT NULL DEFAULT 0.3,
  query_select_alpha REAL NOT NULL DEFAULT 0.3,
  prompt_template TEXT NOT NULL DEFAULT '',
  system_prompt   TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS documents (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id      UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  filename        TEXT NOT NULL,
  content_hash    BYTEA,
  modality        TEXT NOT NULL DEFAULT 'text',
  page_count      INT,
  total_chunks    INT NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','completed','failed')),
  generation      INT  NOT NULL DEFAULT 1,
  error_code      TEXT,
  last_processed_at TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at      TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS documents_active_uniq
  ON documents (dataset_id, filename) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS documents_dataset_id_idx
  ON documents (dataset_id);
CREATE INDEX IF NOT EXISTS documents_status_idx
  ON documents (status) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS chunks (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  dataset_id    UUID NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  text          TEXT NOT NULL,
  modality      TEXT NOT NULL DEFAULT 'text',
  image_path    TEXT,
  parent_title  TEXT NOT NULL DEFAULT '',
  chunk_index   INT  NOT NULL DEFAULT 0,
  filename      TEXT,
  embedding     VECTOR(1536) NOT NULL,
  ts_tokens     TSVECTOR,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at    TIMESTAMPTZ,
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
CREATE UNIQUE INDEX IF NOT EXISTS chunks_document_chunk_idx_uniq
  ON chunks (document_id, chunk_index) WHERE deleted_at IS NULL;
