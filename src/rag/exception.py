"""RAG 全局异常。

约定:
- code: 稳定业务码，格式 {area}.{detail}，见 error_codes.py
- message: 本次人类可读原因，可含路径/URL/底层异常文本
- 链式原因: raise RAGError(...) from e，不存 cause 属性
"""

from __future__ import annotations

from rag.error_codes import ErrorCode


class RAGError(Exception):
    """RAG 业务异常。code 表示异常类型，message 表示具体原因。"""

    def __init__(self, *, code: ErrorCode | str, message: str) -> None:
        self.code = str(code)
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}
