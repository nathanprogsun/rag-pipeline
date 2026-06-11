from langchain_openai import OpenAIEmbeddings
from openai import APIError, APITimeoutError, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rag.config import settings


class _RetryableEmbeddings(OpenAIEmbeddings):
    """LangChain OpenAIEmbeddings 之上叠加 tenacity 重试。"""

    @retry(
        retry=retry_if_exception_type((APIError, APITimeoutError, RateLimitError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def aembed_documents(
        self,
        texts: list[str],
        chunk_size: int | None = None,
        **kwargs: object,
    ) -> list[list[float]]:
        return await super().aembed_documents(texts, chunk_size=chunk_size, **kwargs)

    @retry(
        retry=retry_if_exception_type((APIError, APITimeoutError, RateLimitError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def aembed_query(self, text: str, **kwargs: object) -> list[float]:
        return await super().aembed_query(text, **kwargs)


def get_embed_model(model: str | None = None) -> OpenAIEmbeddings:
    """获取 embed model。调用方通过 llm_sem.run("openai", ...) 进入限流。"""
    return _RetryableEmbeddings(
        model=model or settings.openai_embedding_model,
        openai_api_key=settings.openai_embedding_api_key,
        openai_api_base=settings.openai_embedding_base_url,
    )
