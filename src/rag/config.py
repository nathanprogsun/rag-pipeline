import os
from typing import Any

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

    # Rerank (DashScope compatible-api)
    openai_rerank_base_url: str = "https://dashscope.aliyuncs.com/compatible-api/v1"
    openai_rerank_api_key: SecretStr = SecretStr("")
    openai_rerank_model: str = "qwen3-rerank"

    @property
    def llm_settings(self) -> dict[str, Any]:
        """LLM 并发与 RPM 限流；max_concurrent 默认对齐 openai_max_concurrent。"""
        return {
            "max_concurrent": self.openai_max_concurrent,
            "max_concurrent_per_provider": {"openai": 16, "dashscope": 8},
            "rate_limit_rpm": {"openai": 1000, "dashscope": 500},
        }


def sync_langsmith_env(app_settings: Settings) -> None:
    """将 Settings 中的 LangSmith 字段写入 os.environ，供 LangChain/LangSmith SDK 读取。"""
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
