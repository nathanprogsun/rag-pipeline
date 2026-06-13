from rag.infra.cache.connection import Cache, cache, close_cache, init_cache
from rag.infra.cache.keys import (
    NAMESPACE,
    dataset_version_key,
    embedding_key,
    query_ext_key,
    rerank_key,
    search_key,
    search_key_pattern_for_dataset,
)
