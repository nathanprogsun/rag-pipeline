"""Search business logic: end-to-end RAG retrieval + answer generation.

Public surface:
- ``SearchPipeline`` — ``ainvoke(SearchRequest) -> SearchResult``
- Stage modules under ``extension/``, ``retrieve/``, ``post/``, ``generate/``
"""
