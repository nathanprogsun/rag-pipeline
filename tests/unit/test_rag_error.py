from rag.error_codes import (
    ChunkerErrorCode,
    ConfigErrorCode,
    NormalizerErrorCode,
    ReaderErrorCode,
    RetrievalErrorCode,
)
from rag.exception import RAGError

_ALL_CODE_GROUPS = (
    ReaderErrorCode,
    ChunkerErrorCode,
    NormalizerErrorCode,
    ConfigErrorCode,
    RetrievalErrorCode,
)


def test_rag_error_code_message() -> None:
    err = RAGError(code=ReaderErrorCode.ENCODING, message="not utf-8")
    assert err.code == ReaderErrorCode.ENCODING
    assert err.message == "not utf-8"
    assert str(err) == "not utf-8"


def test_rag_error_to_dict() -> None:
    err = RAGError(code=ConfigErrorCode.MISSING_ENV, message="DATABASE_URL required")
    assert err.to_dict() == {
        "code": ConfigErrorCode.MISSING_ENV,
        "message": "DATABASE_URL required",
    }


def test_rag_error_chains_cause() -> None:
    cause = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
    try:
        raise RAGError(code=ReaderErrorCode.ENCODING, message="bad") from cause
    except RAGError as err:
        assert err.__cause__ is cause


def test_error_code_values_are_dotted() -> None:
    for group in _ALL_CODE_GROUPS:
        for member in group:
            assert "." in member, member


def test_rag_error_is_exception() -> None:
    assert issubclass(RAGError, Exception)
