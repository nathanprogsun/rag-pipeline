from rag.infra.cache.keys import embedding_key, search_key

D1 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
D2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


class TestCacheKeys:
    def test_embedding_key_deterministic(self) -> None:
        k1 = embedding_key("text-embedding-3-small", "hello")
        k2 = embedding_key("text-embedding-3-small", "hello")
        assert k1 == k2

    def test_embedding_key_model_sensitive(self) -> None:
        k1 = embedding_key("text-embedding-3-small", "hello")
        k2 = embedding_key("text-embedding-3-large", "hello")
        assert k1 != k2

    def test_key_namespace_prefix(self) -> None:
        key = embedding_key("m", "q")
        assert key.startswith("rag:emb:m:")

    def test_search_key_includes_dataset_version(self) -> None:
        payload_a = {"query": "q", "dataset_ids": [D1], "dataset_versions": [1]}
        payload_b = {"query": "q", "dataset_ids": [D1], "dataset_versions": [2]}
        assert search_key(payload_a) != search_key(payload_b)
        key = search_key(payload_a)
        assert key.startswith(f"rag:search:{D1}:1:")

    def test_search_key_multi_dataset_versions_sorted(self) -> None:
        payload_1 = {
            "query": "q",
            "dataset_ids": [D1, D2],
            "dataset_versions": [2, 1],
        }
        payload_2 = {
            "query": "q",
            "dataset_ids": [D1, D2],
            "dataset_versions": [1, 2],
        }
        assert search_key(payload_1) == search_key(payload_2)
        key = search_key(payload_1)
        assert key.startswith(f"rag:search:{D1}-{D2}:1-2:")
