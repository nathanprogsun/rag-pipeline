# Task 1: 项目脚手架 + Docker Compose + 验证

**Status**: OK (历史保留)

## 实际实现 (2026-06-13 同步)

> 任务当前形态。Task 1 范围只覆盖**脚手架层**: 项目元数据、依赖、Docker 基础设施、配置、全局异常、smoke test fixture。Ingest 管线 (Reader / Normalizer / Chunker) 的实际形态在 task 8/9/10 与 `src/rag/ingest/` 里, 本 task 不涉及。

**实际落盘清单** (与 `refactor/chunker-reader` HEAD 一致):

- `pyproject.toml`: name=`rag-pipeline`, version=`0.1.0`, requires-python=`>=3.13` (实际升 3.13, 原 plan 写 3.12 已过时)。
  - 核心依赖: `asyncpg` / `pgvector` / `sqlalchemy[asyncio]` / `redis` / `pydantic` / `pydantic-settings` / `httpx` / `jieba` / `typer` / `langchain>=0.3,<0.4` / `langchain-core` / `langchain-openai` / `langchain-text-splitters` / `tqdm` / `pyyaml`。
  - 文档 reader 阶段后续加: `beautifulsoup4` / `markdownify` / `mammoth` / `openpyxl` / `pypdf` / `python-docx` / `python-pptx` (Phase 5 引入, 不在原 plan 范围)。
  - dev 依赖: `pytest` / `pytest-asyncio` / `pytest-cov` / `pytest-xdist` / `mypy` / `ruff` / `pre-commit` / `ragas>=0.3,<0.4` / `datasets` / `types-PyYAML` / `types-openpyxl` / `types-tqdm`。
  - `[project.scripts]`: `rag-ingest = "rag.ingest.cli:main"` (Phase 6 引入, 不在原 plan)。
  - `[build-system]`: hatchling, wheel 打包 `src/rag`。
  - `[tool.pytest.ini_options]`: `asyncio_mode="auto"` + `pythonpath=["src"]` + `markers={asyncio, live_llm}`。
  - `[tool.ruff]`: target-version=`py313`, line-length=88, 启 ANN/B/UP/PLC0415/PGH003。
  - `[tool.mypy]`: python_version=`3.13`, mypy_path=`src`, 启 `disallow_untyped_defs` / `warn_unused_ignores`。
- `docker-compose.yml`: `pgvector/pgvector:pg16` (5432) + `redis:7-alpine` (6379, 256mb LRU), 命名卷 `pgdata` / `redisdata`。**与原 plan 一致, 未变。**
- `.env.example`: 实际变量集与原 plan 不同 —
  - 实际: `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` / `OPENAI_MAX_CONCURRENT` / `OPENAI_EMBEDDING_*` (base_url/api_key/model/dim/max_concurrent) / `OPENAI_RERANK_*` (base_url/api_key/model/max_concurrent) / `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` / `LANGSMITH_PROJECT` / `DATABASE_URL=postgresql+asyncpg://...` / `REDIS_URL`。
  - 原 plan 写: `M3_BASE_URL` / `M3_API_KEY` / `M3_MODEL` / `EMBEDDING_MODEL` / `EMBEDDING_DIM` / `MAX_CONCURRENT_LLM` — **这些键名已废弃, 被统一为 `OPENAI_*` 命名空间 + LangSmith 块**。
- `Makefile`: 实际目标 `up` / `lint` (`ruff check .` + `ruff format --check .` + `mypy src tests`) / `test-unit` / `test-integration` / `test-cache` / `test`。**原 plan 的 `dev` / `eval` 目标已删** (CLI 走 `rag-ingest`, eval 走 `pytest tests/eval/...`)。
- `src/rag/exception.py` (单数): `RAGError(code: ErrorCode|str, message: str)` 基类, 配 `to_dict()`; 错误码从 `rag.error_codes` 导入。**原 plan 的 `exceptions.py` (复数) 路径已废弃, 且原 plan 只写 3 个子异常, 实际全部走 `error_codes` 集中枚举** (Reader/Chunker/Normalizer/Config 分组)。
- `src/rag/config.py`: `Settings(BaseSettings)` (env_file=`.env`), 含 `LLMConcurrencySettings` / `LaneSettings` / `CacheSettings` Pydantic BaseModel。**原 plan 的 `LLMSettings` `@dataclass` 与 provider 速率字典 (`rate_limit_rpm`) 已被替代**; 并发按 chat/embedding/rerank 三通道拆分, 而不是按 provider 拆分。
- `tests/conftest.py` (根目录): 仅 6 行, 暴露 `from rag.config import settings` + 一个 `test_settings_loads` smoke test。**原 plan 描述的 "暴露 fixture 路径 + sample files" 实际散落在 `tests/unit/conftest.py` 与 `tests/data/`, 不在根 conftest。**
- `src/rag/__init__.py` / `tests/__init__.py` / `tests/unit/__init__.py` / `tests/integration/__init__.py`: 均为空 package marker。

**Task 1 不覆盖** (避免误读):
- 文档 reader / normalizer / chunker 实现 — 见 `src/rag/ingest/` (task 8/9/10)。
- IngestPipeline / IngestSource / IngestResult — task 8。
- 测试 fixtures 详情 — `tests/unit/conftest.py` + `tests/data/`。
- RAGAS eval 脚本 — `tests/eval/`。

## 状态: 已完成 (2026-06-13 同步)

> **实际交付**(`refactor/chunker-reader` 分支):
>
> - `pyproject.toml` 落盘:langchain 0.3.x / langchain-openai / asyncpg / pgvector / redis / jieba / typer / pytest / testcontainers / RAGAS 全栈 dev deps
> - `docker-compose.yml`:pgvector/pgvector:pg16 + redis:7-alpine,端口 5432/6379,pgdata/redisdata 卷持久化
> - `.env.example`:OPENAI_API_KEY / M3_BASE_URL / DATABASE_URL / REDIS_URL / EMBEDDING_MODEL / EMBEDDING_DIM / MAX_CONCURRENT_LLM
> - `Makefile`:`dev / up / test / test-int / lint / eval`
> - `src/rag/exceptions.py` → **已迁到 `src/rag/exception.py`**(顶层单数模块,与 `RAGError` 基类统一,Phase 1 D7 偏差)
> - `src/rag/config.py`:`Settings(BaseSettings)` + `LLMSettings` dataclass
> - `tests/conftest.py`:暴露 fixture 路径 + sample files
>
> **后续 review/audit 影响 (2026-06-13 同步)**:
>
> - **PAudit-1**: docker-compose 内的 PG 实例改支持 `bindparams` 友好输出,集成测试 `chunk_repo` 改 `flush + transaction()`
> - **PAudit-3**: `Retry` 工具(`src/rag/infra/llm/retry.py`)覆盖 httpx + asyncio 双栈,本 task 的 httpx 依赖因此被显式纳入重试矩阵
> - **PAudit-5**: pytest 配置 `pyproject.toml [tool.pytest.ini_options]` 路径全用 upper,`tests/` 下文件命名规范化
> - 当前指标:373 unit passed / 19 integration passed (1 skip) / 0 mypy / 0 ruff
>
> **历史溯源**(本 task 原始描述):原 plan 写 7 个步骤创建脚手架,已全部完成且与当前 `refactor/chunker-reader` 分支一致。原描述保留在下方,作为阶段溯源依据。

**Files:**
- Create: `pyproject.toml`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `Makefile`
- Create: `src/rag/__init__.py`
- Create: `src/rag/exceptions.py`
- Create: `src/rag/config.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "rag-pipeline"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "langchain>=0.3,<0.4",
    "langchain-openai>=0.2,<1.0",
    "langchain-cohere>=0.3",
    "langchain-text-splitters>=0.3",
    "langchain-core>=0.3.0",
    "pydantic>=2.8,<3.0",
    "pydantic-settings>=2.5.0",
    "sqlalchemy[asyncio]>=2.0.30",
    "asyncpg>=0.30.0",                # SQLAlchemy async driver
    "pgvector>=0.3.0",                # pgvector SQLAlchemy extension
    "redis>=5.1.0",
    "jieba>=0.42.1",
    "typer>=0.12.0",
    "pyyaml>=6.0",
    "httpx>=0.27.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.0",
    "pytest-asyncio>=0.24.0",
    "pytest-cov>=5.0.0",
    "testcontainers[postgres,redis]>=4.8.0",
    "ruff>=0.6.0",
    "mypy>=1.11.0",
    "ragas>=0.3,<0.4",
    "datasets>=3.0.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: 写 docker-compose.yml**

```yaml
services:
  pg:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: rag
      POSTGRES_USER: rag
      POSTGRES_PASSWORD: rag
    ports: ["5432:5432"]
    volumes: ["pgdata:/var/lib/postgresql/data"]
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    command: ["redis-server", "--maxmemory", "256mb", "--maxmemory-policy", "allkeys-lru"]
    volumes: ["redisdata:/data"]
volumes:
  pgdata:
  redisdata:
```

- [ ] **Step 3: 写 .env.example + Makefile**

```bash
# .env.example
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
M3_BASE_URL=https://api.minimaxi.com/v1
M3_API_KEY=...
DATABASE_URL=postgresql://rag:rag@localhost:5432/rag
REDIS_URL=redis://localhost:6379/0
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIM=1536
MAX_CONCURRENT_LLM=16
```

```makefile
.PHONY: dev up test lint eval
dev: up
	uv run python -m rag.cli.main search "test"
up:
	docker compose up -d
test:
	uv run pytest tests/unit -v
test-int:
	uv run pytest tests/integration -v
lint:
	uv run ruff check src tests
eval:
	uv run python tests/eval/run_ragas.py
```

- [ ] **Step 4: 写 exceptions.py + config.py**

```python
# src/rag/exceptions.py
class RAGError(Exception): pass
class NoResultsError(RAGError): pass
class ConfigError(RAGError): pass
class RetrievalError(RAGError): pass
```

```python
# src/rag/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from dataclasses import dataclass, field
from typing import Optional
from collections import deque

class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    # H2 修正: env_file 从 model_config 移除。library 模式只读 os.environ;
    # CLI 入口通过 Settings(_env_file=".env") 显式加载 .env 文件。
    openai_api_key: str
    openai_base_url: str = "https://api.openai.com/v1"
    m3_base_url: str = "https://api.minimaxi.com/v1"
    m3_api_key: str = ""                                      # 若为空则复用 openai_api_key
    m3_model: str = "M3-multimodal"                           # Issue 3: M3 model 配置
    database_url: str
    redis_url: str = "redis://localhost:6379/0"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    max_concurrent_llm: int = 16

settings = Settings()  # type: ignore[call-arg]

@dataclass
class LLMSettings:
    """LLM 全局并发 + 速率配置"""
    max_concurrent: int = 16
    max_concurrent_per_provider: dict[str, int] = field(default_factory=lambda: {
        "openai": 16, "cohere": 8, "minimax": 16,
    })
    rate_limit_rpm: dict[str, int] = field(default_factory=lambda: {
        "openai": 3000, "cohere": 100, "minimax": 2000,
    })
```

- [ ] **Step 5: 安装依赖 + 起 docker**

```bash
cd /Users/jung/pro/rag-pipeline
uv venv
uv pip install -e ".[dev]"
docker compose up -d
sleep 3
docker compose ps  # 应显示 pg/redis healthy
```

- [ ] **Step 6: 写 smoke test**

```python
# tests/conftest.py
import pytest
from rag.config import settings

def test_settings_loads():
    assert settings.openai_api_key  # 从 .env 读出
```

- [ ] **Step 7: 跑测试 + commit**

```bash
uv run pytest tests/conftest.py -v
# 期望 PASS
git add .
git commit -m "chore: project scaffold + docker compose + config"
```
