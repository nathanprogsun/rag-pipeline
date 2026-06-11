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
