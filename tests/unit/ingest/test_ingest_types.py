from rag.ingest.types import Chunk, ChunkMetadata, DocMeta, TextDoc


def test_textdoc_construction() -> None:
    text_doc = TextDoc(text="hello", meta=DocMeta(filename="a.txt"))
    assert text_doc.text == "hello"
    assert text_doc.meta.filename == "a.txt"
    assert text_doc.meta.page_count is None


def test_docmeta_defaults() -> None:
    meta = DocMeta()
    assert meta.mime is None
    assert meta.filename is None
    assert meta.page_count is None


def test_textdoc_with_format_text() -> None:
    text_doc = TextDoc(
        text="# H1\ncontent",
        meta=DocMeta(),
        format_text="| a | b |",
    )
    assert text_doc.format_text == "| a | b |"


def test_chunk_metadata_default_index_zero() -> None:
    meta = ChunkMetadata()
    assert meta.chunk_index == 0
    assert meta.heading_stack == []


def test_chunk_with_metadata() -> None:
    chunk = Chunk(
        text="abc",
        metadata=ChunkMetadata(chunk_index=1),
    )
    assert chunk.metadata.chunk_index == 1
    assert chunk.text == "abc"


def test_types_are_frozen() -> None:
    text_doc = TextDoc(text="x", meta=DocMeta())
    try:
        text_doc.text = "y"  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except Exception:
        pass


def test_ingest_outcome_has_errors_field() -> None:
    from rag.ingest.types import IngestOutcome

    outcome = IngestOutcome(items=[], warnings=[])
    assert outcome.errors == []
    assert isinstance(outcome.errors, list)
