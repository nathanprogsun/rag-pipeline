import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field

DEFAULT_PROMPT_TEMPLATE = """基于以下参考资料回答用户问题。

## 参考资料
{citations}

## 用户问题
{query}

## 回答"""

DEFAULT_SYSTEM_PROMPT = "你是一个基于参考资料回答问题的助手。请严格依据提供的参考资料,不要编造信息。如果参考资料不足以回答问题,请明确说明。"


class Dataset(BaseModel):
    """知识库配置, 一个 `Dataset` 等价于一个独立的 RAG 知识库。"""

    id: uuid.UUID
    name: str
    embed_model: str
    embed_dim: int
    chunk_size: int = 1000
    rerank_model: str | None = None
    rrf_k: int = 60
    query_select_alpha: float = 0.3  # submodular α (0=多样性, 1=相关性)
    vector_weight: float = 0.7
    fulltext_weight: float = 0.3
    prompt_template: str | None = None  # None 时回退到 `DEFAULT_PROMPT_TEMPLATE`
    system_prompt: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
