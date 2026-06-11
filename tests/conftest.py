from rag.config import settings


def test_settings_loads():
    assert settings.openai_api_key.get_secret_value() != ""
