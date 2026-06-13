import asyncio

import httpx
from langchain_openai import OpenAIEmbeddings
from openai import APIError, APITimeoutError, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from rag.config import settings

# 重试异常覆盖边界:
#  - APIError 是 openai SDK 所有异常的父类, 覆盖 APIConnectionError
#    (openai 把 httpx.ConnectError 包成 APIConnectionError, 继承 APIError),
#    AuthenticationError, BadRequestError 等。
#  - APITimeoutError: openai 把 httpx.ReadTimeout 包成 APITimeoutError,
#    继承 APIError, 但显式列出来便于阅读 + 防 openai 调整继承层级。
#  - RateLimitError: 429 / TPM 限流。
#  - httpx.HTTPError: 兜底, 防 openai SDK 漏包 (e.g. 旧版本或某些 transport
#    异常未被包装)。
#  - asyncio.TimeoutError: Python 3.11+ ``asyncio.wait_for`` 超时抛裸
#    ``TimeoutError`` (即 ``asyncio.TimeoutError``), openai 不会包装,
#    必须显式捕获, 否则会逃出 retry 边界。
_RETRIABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    APIError,  # 父类: 覆盖 APIConnectionError 等
    APITimeoutError,
    RateLimitError,
    httpx.HTTPError,  # 兜底, 防 openai 漏包
    asyncio.TimeoutError,  # 3.11+ 裸 TimeoutError (asyncio.wait_for)
)


class _RetryableEmbeddings(OpenAIEmbeddings):
    """LangChain OpenAIEmbeddings 之上叠加 tenacity 重试。"""

    @retry(
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
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
        retry=retry_if_exception_type(_RETRIABLE_EXCEPTIONS),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def aembed_query(self, text: str, **kwargs: object) -> list[float]:
        return await super().aembed_query(text, **kwargs)


def get_embed_model(model: str | None = None) -> OpenAIEmbeddings:
    """获取 embed model。调用方通过 llm_sem.run(\"embedding\", ...) 进入限流。"""
    return _RetryableEmbeddings(
        model=model or settings.openai_embedding_model,
        openai_api_key=settings.openai_embedding_api_key,
        openai_api_base=settings.openai_embedding_base_url,
        dimensions=settings.openai_embedding_dim,
        # DashScope compatible-mode 要求原始字符串输入，禁止 LangChain 预 tokenize
        check_embedding_ctx_length=False,
    )
