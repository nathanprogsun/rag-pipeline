"""RAG 业务错误码, 业务码格式 ``{area}.{detail}``。

`ErrorCode` 是所有分组的联合类型, 兼容旧 `code: ErrorCode` 注解;
新代码优先引用具体分组类型 (如 `ReaderErrorCode.PARSE`), 表达力更强。
"""

from enum import StrEnum


class ReaderErrorCode(StrEnum):
    """Reader 阶段错误码 (本地文件 / URL / Office 解析)。"""

    NOT_FOUND = "reader.not_found"
    PERMISSION = "reader.permission"
    ENCODING = "reader.encoding"
    PARSE = "reader.parse"
    UNSUPPORTED = "reader.unsupported"
    TOO_LARGE = "reader.too_large"


class ChunkerErrorCode(StrEnum):
    """Chunker 阶段错误码。"""

    INVALID = "chunker.invalid"


class NormalizerErrorCode(StrEnum):
    """Normalizer 阶段错误码。"""

    INVALID_JSON = "normalizer.invalid_json"


class ConfigErrorCode(StrEnum):
    """配置 / 环境变量错误码。"""

    MISSING_ENV = "config.missing_env"
    INVALID_VALUE = "config.invalid_value"


class RetrievalErrorCode(StrEnum):
    """检索阶段错误码。"""

    STORE_UNAVAILABLE = "retrieval.store_unavailable"
    NO_RESULTS = "retrieval.no_results"


# 兼容旧注解 `code: ErrorCode`; 新代码优先引用具体 `*ErrorCode`。
ErrorCode = (
    ReaderErrorCode
    | ChunkerErrorCode
    | NormalizerErrorCode
    | ConfigErrorCode
    | RetrievalErrorCode
)
