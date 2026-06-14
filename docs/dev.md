# dev.md — Local dev workflow

Quick reference for working on rag-pipeline. The full project overview
lives in [`README.md`](../README.md); this file covers day-to-day commands.

## Setup

```bash
# Clone + enter
git clone <repo-url> && cd rag-pipeline

# Python 3.13 (uv manages virtualenv automatically)
uv sync --extra dev

# Bring up local services (postgres + redis)
make up                  # or: docker compose up -d

# Copy env template and fill in keys
cp .env.example .env
# Edit .env: at minimum OPENAI_EMBEDDING_API_KEY

# Run unit tests
uv run pytest tests/unit -q

# Run integration tests (requires running postgres)
uv run pytest tests/integration -q
```

## Common tasks

### Run a single test

```bash
# Single file
uv run pytest tests/unit/test_fusion.py -v

# Single test by name
uv run pytest tests/unit/test_fusion.py::test_intra_per_group_weight_applied -v
```

### Run CLI tools

```bash
uv run rag-ingest --help
uv run rag-search -q "test" --dataset-id <UUID>
uv run rag-eval -d data/eval.jsonl
```

### Format + lint

```bash
uv run ruff check src tests        # lint
uv run ruff format src tests        # format
```

### Coverage

```bash
uv run pytest tests/unit tests/integration \
    --cov=rag --cov-report=term-missing
```

## Project layout

```
src/rag/
├── domain/              # Pydantic models (Chunk, SearchRequest, etc.)
├── infra/               # DB drivers, LLM clients, embedders
├── pipeline/            # 5a-5f + 5g: orchestrator, filters, rerank, cite, CLI
├── retrieval/           # 5e: AuditTap, CitationChecker
├── eval/                # 5h-5i + 5g: metrics, runners, CLI
└── ingest/              # Pre-existing: file/url parsers, normalizer, chunker

tests/
├── unit/                # ~261 unit tests
├── integration/         # ~50 integration tests (real PG + real DashScope)
└── data/                # Test fixtures

project-template/        # 5k: skeleton for new projects
deploy/                  # (deprecated, see Dockerfile + docker-compose.yml at root)
docs/                    # This file
.github/workflows/       # CI (5j)
```

## Conventions

- **DDD layering**: `domain/` is pure Pydantic, `infra/` is I/O, `pipeline/` orchestrates
- **async everywhere**: All I/O is async; sync entry points use `asyncio.run` at the top
- **frozen Pydantic models**: Avoid mutation; use `.model_copy(update=...)`
- **Type hints mandatory**: ruff ANN enforces them (mypy in CI)
- **No `__all__`**: re-exports done explicitly via `from X import Y`
- **Type-only imports**: Use `from x import Y as Y` for type-only
- **No `print`**: Use `logger` (stdlib logging) for runtime output
- **Tests mirror code**: each `src/rag/X/` should have `tests/unit/test_X_*.py`

## See also

- [`README.md`](../README.md) — full project overview
- [`AGENTS.md`](../AGENTS.md) — Claude/agent instructions for this repo
- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) — CI pipeline (5j)
- [`project-template/`](../project-template/) — skeleton for new projects (5k)