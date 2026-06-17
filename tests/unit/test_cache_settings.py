from rag.config import CacheSettings


class TestCacheSettings:
    def test_default_l1_ttl(self) -> None:
        settings = CacheSettings()
        assert settings.l1_ttl == 86400
