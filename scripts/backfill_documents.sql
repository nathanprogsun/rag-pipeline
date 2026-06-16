-- Backfill `documents` from distinct (dataset_id, filename) of existing chunks.
-- Idempotent: skip rows that already have a document for the same key.
--
-- Run AFTER schema changes (T2.1 lands `documents` table + `chunks.document_id`)
-- and BEFORE T2.5 enables NOT NULL on chunks.document_id.
--
-- Usage:
--   psql $DATABASE_URL -f scripts/backfill_documents.sql

BEGIN;

-- 1. 临时 distinct 源: 每 (dataset_id, filename) 一行
CREATE TEMP TABLE _chunk_groups ON COMMIT DROP AS
  SELECT DISTINCT dataset_id, filename
  FROM chunks
  WHERE deleted_at IS NULL;

-- 2. INSERT documents (skip if active doc already exists)
INSERT INTO documents (dataset_id, filename, status, generation)
SELECT g.dataset_id, g.filename, 'completed', 1
FROM _chunk_groups g
WHERE NOT EXISTS (
  SELECT 1 FROM documents d
  WHERE d.dataset_id = g.dataset_id
    AND d.filename = g.filename
    AND d.deleted_at IS NULL
);

-- 3. UPDATE chunks.document_id (only chunks without document_id yet)
UPDATE chunks c
SET document_id = d.id
FROM documents d
WHERE c.dataset_id = d.dataset_id
  AND c.filename = d.filename
  AND c.document_id IS NULL
  AND d.deleted_at IS NULL;

-- 4. Backfill total_chunks
UPDATE documents d
SET total_chunks = sub.n
FROM (
  SELECT document_id, COUNT(*) AS n
  FROM chunks
  WHERE deleted_at IS NULL
  GROUP BY document_id
) sub
WHERE d.id = sub.document_id;

COMMIT;
