# Multi-stage Dockerfile for rag-pipeline.
#
# Stage 1 (builder): install uv, sync all deps including dev.
# Stage 2 (runtime): copy venv + src, run as non-root, expose rag-* entry points.

# -----------------------------------------------------------------------------
# Stage 1: builder
# -----------------------------------------------------------------------------
FROM python:3.13-slim AS builder

# System deps for asyncpg, jieba, psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv (fast Python package installer)
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency manifests first for layer caching
COPY pyproject.toml uv.lock* ./

# Install all dependencies (including dev). If no uv.lock, fall back to sync.
RUN (test -f uv.lock && uv sync --frozen --extra dev || uv sync --extra dev)

# -----------------------------------------------------------------------------
# Stage 2: runtime
# -----------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

# System deps for runtime (libpq for asyncpg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user for runtime
RUN groupadd --system rag && useradd --system --gid rag --home /app rag

WORKDIR /app

# Copy venv + project from builder
COPY --from=builder --chown=rag:rag /app/.venv /app/.venv
COPY --from=builder --chown=rag:rag /app/src /app/src
COPY --from=builder --chown=rag:rag /app/pyproject.toml /app/pyproject.toml

# Activate venv in PATH
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

USER rag

# Sanity check: entry points resolve
RUN rag-search --help > /dev/null && rag-eval --help > /dev/null && rag-ingest --help > /dev/null

# Default: print help (override CMD when running CLIs)
CMD ["rag-search", "--help"]