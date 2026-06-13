from rag.error_codes import (
    ChunkerErrorCode,
    NormalizerErrorCode,
    ReaderErrorCode,
)
from rag.exception import RAGError


def test_reader_encoding_error() -> None:
    err = RAGError(
        code=ReaderErrorCode.ENCODING,
        message="/tmp/a.txt: not utf-8",
    )
    assert err.code == ReaderErrorCode.ENCODING
    assert "/tmp/a.txt" in err.message


def test_reader_parse_error() -> None:
    err = RAGError(
        code=ReaderErrorCode.PARSE,
        message="https://example.com: boom",
    )
    assert err.code == ReaderErrorCode.PARSE
    assert "example.com" in err.message


def test_normalizer_invalid_json() -> None:
    err = RAGError(
        code=NormalizerErrorCode.INVALID_JSON,
        message="missing content field",
    )
    assert err.code == NormalizerErrorCode.INVALID_JSON
    assert "missing content field" in err.message


def test_chunker_invalid() -> None:
    err = RAGError(
        code=ChunkerErrorCode.INVALID,
        message="invalid step index",
    )
    assert err.code == ChunkerErrorCode.INVALID
    assert "invalid step index" in err.message
