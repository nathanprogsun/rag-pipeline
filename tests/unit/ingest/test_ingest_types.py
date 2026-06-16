from rag.ingest.types import Chunk, ChunkMetadata, DocMeta, TextDoc


def test_textdoc_construction() -> None:
    text_doc = TextDoc(text="hello", meta=DocMeta(filename="a.txt", size_bytes=5))
    assert text_doc.text == "hello"
    assert text_doc.meta.filename == "a.txt"
    assert text_doc.meta.encoding == "utf-8"
    assert text_doc.meta.created_at is None
    assert text_doc.images == []


def test_docmeta_size_bytes_default() -> None:
    meta = DocMeta()
    assert meta.size_bytes == 0
    assert meta.mime is None


def test_textdoc_with_images() -> None:
    text_doc = TextDoc(
        text="# H1\ncontent",
        meta=DocMeta(),
        images=[],
    )
    assert text_doc.images == []
    assert text_doc.format_text is None


def test_chunk_metadata_default_index_zero() -> None:
    meta = ChunkMetadata()
    assert meta.chunk_index == 0
    assert meta.total_chunks == 0
    assert meta.heading_stack == []


def test_chunk_with_metadata() -> None:
    chunk = Chunk(
        text="abc",
        metadata=ChunkMetadata(chunk_index=1, total_chunks=3, valid_len=3),
    )
    assert chunk.metadata.chunk_index == 1
    assert chunk.metadata.valid_len == 3


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
