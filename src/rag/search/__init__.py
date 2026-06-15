"""Search business logic: end-to-end RAG retrieval + answer generation.

Public surface:
- ``SearchPipeline`` (orchestrator, per-request)
- ``build_search_pipeline(deps)`` (factory returning a long-lived ``Pipeline``)
- ``SearchPipelineDeps`` (typed Pydantic deps)
- Stage modules under ``extension/``, ``retrieve/``, ``post/``, ``generate/``
"""
