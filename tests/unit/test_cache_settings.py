from rag.config import CacheSettings


class TestCacheSettings:
    def test_default_query_extension_off(self) -> None:
        settings = CacheSettings()
        assert settings.query_ext_enabled is False
