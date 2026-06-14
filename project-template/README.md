# project-template

Skeleton for new projects built on top of `rag-pipeline`.

## Usage

```bash
# Option A: cookiecutter (planned, not yet implemented)
# pip install cookiecutter
# cookiecutter project-template/

# Option B: copy-paste (current)
cp -r project-template/* my-new-project/
cd my-new-project
# Replace {{PROJECT_NAME}} placeholders in pyproject.toml
# Adjust src/ layout to match your domain
```

## Structure

```
my-new-project/
├── pyproject.toml          # Depends on rag-pipeline from path/git
├── README.md
├── .env.example             # Copy to .env, fill in API keys
├── data/
│   └── eval.jsonl           # Optional eval dataset
├── src/
│   └── my_app/
│       ├── __init__.py
│       ├── config.py        # App-specific config
│       └── ingest.py        # Custom ingest pipeline
└── tests/
    ├── __init__.py
    └── test_smoke.py
```

## Wiring into rag-pipeline

```python
# src/my_app/main.py
from rag.pipeline.full import PipelineDeps, build_full_pipeline
from rag.infra.llm.embed import get_embed_model
from rag.infra.llm.chat import get_structured_chat_model
from rag.infra.llm.rerank import get_rerank_model


def make_pipeline():
    deps = PipelineDeps(
        embedder=get_embed_model(),
        llm=get_structured_chat_model(),
        rerank_client=get_rerank_model(),  # None if no key
        top_k=10,
    )
    return build_full_pipeline(deps)


async def search(query: str, dataset_ids: list[uuid.UUID]):
    pipeline = make_pipeline()
    result = await pipeline.ainvoke(SearchRequest(query=query, dataset_ids=dataset_ids))
    return result
```

## Conventions

- Mirror rag-pipeline's DDD layering: `domain/`, `infra/`, `pipeline/`
- Use `rag.eval.Runner` for offline eval against `data/eval.jsonl`
- Use `rag.retrieval.audit.AuditTap` for per-request NDJSON logging
- Tests: 80% coverage minimum (matches rag-pipeline CI gate)