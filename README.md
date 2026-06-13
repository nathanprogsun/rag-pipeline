# rag-pipeline

Python 3.13 RAG ingestion pipeline: turn heterogeneous sources (files, URLs, raw buffers)
into clean, structured, chunked text ready for embedding. Pydantic v2 domain, async
SQLAlchemy 2.0 infra, optional LLM-based paragraph rewriting.

The whole ingest pipeline is **fully async end-to-end** (`IngestPipeline.ingest` →
`Normalizer.normalize` → `html_to_md` → reader adapters), so LLM calls, HTTP fetches, and
file uploads never block the event loop. CLI uses `asyncio.run` only at the very top.

## Quick start

```bash
# 1. Install (Python 3.13, uv)
uv sync --extra dev

# 2. (Optional) bring up local Postgres + Redis
make up

# 3. Copy env template and fill keys if you want LLM features
cp .env.example .env

# 4. Run the CLI on fixtures
uv run rag-ingest tests/data/sample.txt
uv run rag-ingest tests/data/sample.pdf
uv run rag-ingest tests/data/sample.docx

# 5. Run on a URL via the dedicated subcommand
uv run rag-ingest ingest-url https://example.com/article.html

# 6. Recursive ingest of a directory
uv run rag-ingest ingest ./docs/ -r
```

The CLI prints one chunk per line with `[index/total] source: heading_path | preview...`.

## Architecture

Three-stage ingest pipeline, single async entry point:

```
  IngestSource ─►  (FileSource │ UrlSource │ BufferSource │ ApiSource)
                          │
                          ▼
               reader  (FormatAdapter)      async: bytes → TextDoc
                          │
                          ▼
               normalizer (optional)        async: LLM 段落改写 (ainvoke) or NoOp
                          │
                          ▼
               chunker (12 rules)            sync (无 I/O): text + ctx → Chunk[]
                          │
                          ▼
                   IngestResult           (chunks, title, doc_meta, warnings)
```

- **Reader** (`src/rag/ingest/reader/`): bytes + extension → `TextDoc`. One adapter per
  format. Adapters are `async def`; internally they `await` `html_to_md` (also async)
  for HTML / DOCX → markdown conversion.
- **Normalizer** (`src/rag/ingest/normalizer/`): optional async LLM paragraph rewriting
  (`StructureNormalizer` — `await chat_model.ainvoke(prompt)`) or async pass-through
  (`NoOpNormalizer`). The `Normalizer` base class is async; subclasses must `await` it.
- **Chunker** (`src/rag/ingest/chunker/`): 12-rule recursive splitter with code-block
  protection, table detection, per-chunk heading stack rebuild, and `valid_len` based on
  whitespace-stripped character count. Sync (no I/O); doesn't block the event loop.
- **Domain / Infra** (`src/rag/domain/`, `src/rag/infra/`): Pydantic DTOs and async
  SQLAlchemy 2.0 persistence (pgvector).

`static structure/` is **no longer a top-level pipeline stage**; structure lives inside
`TextDoc` and is filled by readers (or pipeline fallback). `TextDoc.structure` is retained
as a field.

### Sync/async contract

The whole chain is async by design. If you must call from sync code (e.g. a third-party
sync callback like `mammoth.images.img_element`), use `run_coroutine_sync` from
`rag.infra.pg.runnable_sync` — it wraps `asyncio.run` with coroutine-factory pattern
and running-loop detection, so it never produces orphan coroutines or nested-loop
errors.

## Supported formats

| Format | Extension | Adapter | Notes |
|--------|-----------|---------|-------|
| Plain text | `txt` | `txt_adapter` | encoding from DocMeta |
| Markdown | `md`, `markdown` | `md_adapter` | structure extracted |
| HTML | `html`, `htm` | `html_adapter` | structure extracted |
| PDF | `pdf` | `pdf_adapter` | per-page structure |
| DOCX | `docx` | `docx_adapter` | images extracted |
| PPTX | `pptx` | `pptx_adapter` | |
| XLSX | `xlsx` | `xlsx_adapter` | |
| CSV | `csv` | `csv_adapter` | |
| JSON | `json` | `json_adapter` | structured payloads |
| API response | `api` | `api_response_adapter` | datasource=`api` |

## CLI commands

```bash
uv run rag-ingest ingest FILE_PATH [FILE_PATH ...]    # one or more files
uv run rag-ingest ingest DIR/ -r                     # recursive directory
uv run rag-ingest ingest-url URL                     # HTTP fetch (httpx, async)
uv run rag-ingest ingest ... --normalize force       # force LLM paragraph rewriting
uv run rag-ingest ingest ... --chunk-stats           # chunk quality stats
```

Exit code `1` on `RAGError` (unsupported format, decode failure, network error, etc.).

## Core API

```python
import asyncio
from pathlib import Path

from rag.ingest.chunker import Chunker, ChunkSettings
from rag.ingest.pipeline import IngestPipeline
from rag.ingest.source import FileSource

pipeline = IngestPipeline(
    chunker=Chunker(ChunkSettings(chunk_size=800, overlap_ratio=0.15)),
    normalizer=None,  # default: NoOpNormalizer (no LLM rewrite)
)

result = asyncio.run(pipeline.ingest(FileSource(Path("docs/handbook.pdf"))))

for chunk in result.chunks:
    print(chunk.metadata.heading_stack, chunk.text[:80])
```

`IngestPipeline.ingest` accepts `FileSource | UrlSource | BufferSource` (see
`src/rag/ingest/source.py`).

## Configuration

### Chunking (`ChunkSettings`)

| Field | Default | Meaning |
|-------|---------|---------|
| `chunk_size` | `1000` | target chunk size in characters |
| `max_chunk_size` | `8000` | hard upper bound, enforced in finalize |
| `overlap_ratio` | `0.10` | fraction of overlap (clamped to `[0, 0.5]`) |
| `min_chunk_size` | `256` | merge-small threshold (then merge-to-target toward `chunk_size`) |
| `paragraph_chunk_deep` | `5` | recursion depth |
| `paragraph_chunk_min_size` | `200` | min paragraph size |
| `custom_separator` | `None` | optional first-cut separator |

### Environment (`.env`)

See `.env.example`. Key variables: `OPENAI_*` (chat), `OPENAI_EMBEDDING_*` (embedding),
`OPENAI_RERANK_*` (rerank), `DATABASE_URL`, `REDIS_URL`, optional `LANGSMITH_*` for
tracing. Embedding dim defaults to `1536` (must match `schema.sql`).

### LLM paragraph rewriting

`StructureNormalizer` is **async** (`async def normalize`) — the `Normalizer` base class
contract requires it. Three gates:
1. `mode=forbid` or no `chat_model` → skip entirely (returns raw `TextDoc`).
2. `mode=auto` with ≥ 2 existing markdown headings → skip (already structured).
3. otherwise → `await chat_model.ainvoke(prompt)` (via `asyncio.wait_for` with 600s
   default timeout); on any exception, log a warning and degrade to raw text.

CLI flag: `--normalize {off,auto,force}` (default `off`).

## Testing

```bash
make test-unit          # unit tests only — fast, no Docker
make test-integration   # requires local Postgres (`make up`)
make test               # both
uv run pytest tests/unit/ -q
```

- **Unit** (`tests/unit/`): pure logic, no network, no DB. Reader adapters, chunker rules,
  normalizer structure parsing, dispatch routing, error code mapping.
- **Integration** (`tests/integration/`): real Postgres + Redis; chunk repository, vector
  retrieval, fulltext search, LLM live (`@pytest.mark.live_llm`).

Shared fixtures under `tests/data/` (txt / md / html / pdf / docx / pptx / xlsx / csv /
json). See `tests/unit/test_reader_dispatch.py` for adapter coverage.

## Project structure

```
src/rag/
  config.py / exception.py / error_codes.py
  domain/   # Pydantic DTOs only (no SQLAlchemy, no I/O) — AGENTS.md
  infra/    # pg/ (models, repositories, schema.sql) + cache/ + llm/ — AGENTS.md
            #   pg/runnable_sync.py: sync↔async bridge helper (coroutine factory + loop detect)
  ingest/   # pipeline + source + types + cli — fully async end-to-end
    pipeline.py    # async IngestPipeline.ingest — single entry point
    reader/        # async adapters per format (bytes + ext → TextDoc)
    normalizer/    # async Normalizer base class + NoOp / StructureNormalizer
    chunker/       # sync 12-rule recursive splitter + finalize + overlap
tests/
  unit/         # fast, no Docker; reads tests/data/ fixtures
  integration/  # real PG/Redis, optional live LLM
  data/         # sample fixtures (txt/md/html/pdf/docx/pptx/xlsx/csv/json)
```

Sub-`AGENTS.md` files carry layer-specific rules: `src/rag/domain/AGENTS.md`,
`src/rag/infra/pg/AGENTS.md`, `tests/AGENTS.md`.

## Contributing

1. Read `AGENTS.md` (root) — type annotations, import order, RAGError rules.
2. Read the relevant sub-`AGENTS.md` for the layer you're touching.
3. Make changes inside `src/rag/` or `tests/`, then run `make lint && make test`.
4. Update `AGENTS.md` (root or sub) if you introduce a new convention that other agents
   need to follow.
