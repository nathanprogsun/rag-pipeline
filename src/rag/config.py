from pydantic import BaseModel, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CacheSettings(BaseModel):
    query_ext_enabled: bool = False
    l1_ttl: int = 86400  # L1 embedding：text→vector，默认 24h；模型切换时主动失效
    l2_ttl: int = 1800  # L2 query extension：LLM 查询扩展，默认 30min
    l3_ttl: int = 300  # L3 search：检索结果，默认 5min；chunk 变更时按 dataset 失效
    l4_ttl: int = 3600  # L4 rerank：重排结果，默认 1h


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
    database_url: PostgresDsn = PostgresDsn(
        "postgresql+asyncpg://rag:rag@localhost:5432/rag"
    )
    redis_url: RedisDsn = RedisDsn("redis://localhost:6379/0")

    # Embedding
    openai_embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    openai_embedding_api_key: SecretStr = SecretStr("")
    openai_embedding_model: str = "text-embedding-v3"
    openai_embedding_dim: int = 1536

    cache: CacheSettings = CacheSettings()


settings = Settings()
