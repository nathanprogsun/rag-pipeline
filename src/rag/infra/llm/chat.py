import logging

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from rag.config import settings

logger = logging.getLogger(__name__)

_LLM_TIMEOUT_SECONDS = 30.0


def get_chat_model(
    model: str | None = None,
    temperature: float = 0.1,
    timeout: float = _LLM_TIMEOUT_SECONDS,
    max_retries: int = 0,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """标准 chat model, OpenAI 协议。

    默认 temperature=0.1; timeout=30s (FastGPT 默认); max_retries=0 因已有 LLMSemaphore。
    """
    return ChatOpenAI(
        model=model or settings.openai_model,
        temperature=temperature,
        api_key=api_key or settings.openai_api_key.get_secret_value(),
        base_url=base_url or settings.openai_base_url,
        timeout=timeout,
        max_retries=max_retries,
        # 设置 reasoning_split=True 将思考内容分离到 reasoning_details 字段
        extra_body={"reasoning_split": True},
    )


def get_structured_chat_model(
    schema: type[BaseModel],
    temperature: float = 0.1,
    timeout: float = _LLM_TIMEOUT_SECONDS,
    max_retries: int = 0,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> Runnable:
    """带结构化输出能力的 chat model（LangChain tools + function_calling）。"""
    base = get_chat_model(
        model=model,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        base_url=base_url,
        api_key=api_key,
    )
    return base.with_structured_output(schema, method="function_calling")
