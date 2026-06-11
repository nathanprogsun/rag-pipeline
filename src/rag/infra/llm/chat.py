import logging

from langchain_core.runnables import Runnable
from langchain_core.utils.function_calling import convert_pydantic_to_openai_function
from langchain_openai import ChatOpenAI

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
    )


def get_structured_chat_model(
    schema: type,
    temperature: float = 0.1,
    timeout: float = _LLM_TIMEOUT_SECONDS,
    max_retries: int = 0,
    base_url: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> Runnable:
    """带结构化输出能力的 chat model (function_calling only, B3 兼容)。"""
    base = get_chat_model(
        model=model,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        base_url=base_url,
        api_key=api_key,
    )
    fn_schema = convert_pydantic_to_openai_function(schema)
    return base.bind(
        functions=[fn_schema],
        function_call={"name": fn_schema["name"]},
    )
