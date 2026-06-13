import logging

from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from rag.config import settings

logger = logging.getLogger(__name__)

_LLM_TIMEOUT_SECONDS = 30.0

# 支持 reasoning_split 的模型前缀。reasoning_split 是 OpenAI 兼容层 (DeepSeek 等)
# 把 ``reasoning_content`` 分离到独立字段的开关, 只有 reasoning 模型才需要打开;
# 对普通 chat 模型传这个 extra_body 会污染请求体 / 触发上游 4xx。
_REASONING_MODEL_PREFIXES: tuple[str, ...] = ("o1", "o3", "deepseek-reasoner")


def _is_reasoning_model(model: str) -> bool:
    return model.startswith(_REASONING_MODEL_PREFIXES)


def get_chat_model(
    model: str | None = None,
    temperature: float = 0.1,
    timeout: float = _LLM_TIMEOUT_SECONDS,
    max_retries: int = 0,
    base_url: str | None = None,
    api_key: str | None = None,
) -> ChatOpenAI:
    """标准 chat model, OpenAI 协议。

    默认 temperature=0.1; timeout=30s; max_retries=0 因已有 LLMSemaphore。
    """
    resolved_model = model or settings.openai_model
    if _is_reasoning_model(resolved_model):
        # reasoning_split 仅 reasoning 模型需要: 把思考内容分离到 reasoning_details 字段
        return ChatOpenAI(
            model=resolved_model,
            temperature=temperature,
            api_key=api_key or settings.openai_api_key.get_secret_value(),
            base_url=base_url or settings.openai_base_url,
            timeout=timeout,
            max_retries=max_retries,
            extra_body={"reasoning_split": True},
        )
    return ChatOpenAI(
        model=resolved_model,
        temperature=temperature,
        api_key=api_key or settings.openai_api_key.get_secret_value(),
        base_url=base_url or settings.openai_base_url,
        timeout=timeout,
        max_retries=max_retries,
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
