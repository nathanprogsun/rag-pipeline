"""RAG 全局异常。

约定:
- `code`: 稳定业务码, 格式 `{area}.{detail}`, 见 `error_codes.py`
- `message`: 人类可读原因, 可含路径/URL/底层异常文本
- 链式原因: `raise RAGError(...) from e`, 不存 `cause` 属性
"""

from __future__ import annotations

from rag.error_codes import ErrorCode


class RAGError(Exception):
    """RAG 业务异常。`code` 表示异常类型, `message` 表示具体原因。

    Args:
        code: 业务码, 取值见 `rag.error_codes`。
        message: 本次异常的人类可读描述。
    """

    def __init__(self, *, code: ErrorCode | str, message: str) -> None:
        self.code = str(code)
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        """序列化为 API 响应可用的字典。

        Returns:
            含 `code` 与 `message` 两个键的字典。
        """
        return {"code": self.code, "message": self.message}
