import os

from pydantic import BaseModel, PostgresDsn, RedisDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CacheSettings(BaseModel):
    l1_ttl: int = 86400  # L1 embedding: text→vector, 默认 24h; 模型切换时主动失效
    l2_ttl: int = 1800  # L2 query extension: LLM 查询扩展, 默认 30min
    l3_ttl: int = 300  # L3 search: 检索结果, 默认 5min; chunk 变更时按 dataset 失效
    l4_ttl: int = 3600  # L4 rerank: 重排结果, 默认 1h


class LaneSettings(BaseModel):
    max_concurrent: int = 3
    enabled: bool = True


class LLMConcurrencySettings(BaseModel):
    """按能力通道 (chat / embedding / rerank) 独立并发; rerank 未配置时为 None。"""

    chat: LaneSettings = LaneSettings(max_concurrent=4)
    embedding: LaneSettings = LaneSettings(max_concurrent=5)
    rerank: LaneSettings | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    # LLM (chat)
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.minimaxi.com/v1"
    openai_model: str = "MiniMax-M3"
    openai_max_concurrent: int = 4

    # LangSmith
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr = SecretStr("")
    langsmith_project: str = "rag-pipeline"
    langsmith_endpoint: str = ""
    langsmith_workspace_id: str = ""

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
    openai_embedding_max_concurrent: int = 5

    # Rerank (DashScope compatible-api)
    openai_rerank_base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    openai_rerank_api_key: SecretStr = SecretStr("")
    openai_rerank_model: str = "qwen3-rerank"
    openai_rerank_max_concurrent: int = 4

    # Cache
    cache: CacheSettings = CacheSettings()

    @property
    def llm_concurrency(self) -> LLMConcurrencySettings:
        """按能力通道构建并发配置; `openai_max_concurrent` 仅控制 chat。"""
        rerank_key = self.openai_rerank_api_key.get_secret_value()
        rerank_lane: LaneSettings | None = None
        if rerank_key:
            rerank_lane = LaneSettings(
                max_concurrent=self.openai_rerank_max_concurrent,
            )
        return LLMConcurrencySettings(
            chat=LaneSettings(max_concurrent=self.openai_max_concurrent),
            embedding=LaneSettings(
                max_concurrent=self.openai_embedding_max_concurrent,
            ),
            rerank=rerank_lane,
        )


def sync_langsmith_env(app_settings: Settings) -> None:
    """将 `Settings` 中的 LangSmith 字段写入 `os.environ`, 供 LangChain/LangSmith SDK 读取。"""
    os.environ["LANGSMITH_TRACING"] = (
        "true" if app_settings.langsmith_tracing else "false"
    )
    api_key = app_settings.langsmith_api_key.get_secret_value()
    if api_key:
        os.environ["LANGSMITH_API_KEY"] = api_key
    os.environ["LANGSMITH_PROJECT"] = app_settings.langsmith_project
    if app_settings.langsmith_endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = app_settings.langsmith_endpoint
    if app_settings.langsmith_workspace_id:
        os.environ["LANGSMITH_WORKSPACE_ID"] = app_settings.langsmith_workspace_id


settings = Settings()
sync_langsmith_env(settings)
