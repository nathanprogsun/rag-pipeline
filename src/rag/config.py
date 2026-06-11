from pydantic import PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # LLM
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.minimaxi.com/v1"
    openai_model: str = "MiniMax-M3"
    openai_max_concurrent: int = 8

    # Database
    database_url: PostgresDsn = PostgresDsn("postgresql://rag:rag@localhost:5432/rag")
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    # Embedding
    openai_embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    openai_embedding_api_key: SecretStr = SecretStr("")
    openai_embedding_model: str = "text-embedding-v3"
    openai_embedding_dim: int = 1536


settings = Settings()  # type: ignore[call-arg]
