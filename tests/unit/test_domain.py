import uuid

from rag.domain.dataset import Dataset
from rag.domain.document import Chunk, ChunkMetadata, ScoredDocument
from rag.domain.search import SearchRequest, SearchResult, resolve_rerank_model


def test_dataset_creation() -> None:
    ds = Dataset(
        id=uuid.uuid4(),
        name="test",
        embed_model="text-embedding-3-small",
        embed_dim=1536,
    )
    assert ds.vector_weight == 0.7  # default
    assert (
        ds.prompt_template == ""
    )  # empty default, app falls back to DEFAULT_PROMPT_TEMPLATE


def test_chunk_requires_dataset_id() -> None:
    meta = ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file")
    chunk = Chunk(
        id=uuid.uuid4(), dataset_id=meta.dataset_id, text="hello", metadata=meta
    )
    assert chunk.modality == "text"  # default
    assert chunk.image_path is None


def test_scored_document_has_image_path() -> None:
    meta = ChunkMetadata(dataset_id=uuid.uuid4(), datasource="file")
    doc = ScoredDocument(
        chunk_id=uuid.uuid4(),
        dataset_id=meta.dataset_id,
        text="x",
        score=0.5,
        rank=0,
        source="vector",
        metadata=meta,
        modality="image_caption",
        image_path="/img/1.png",
    )
    assert doc.image_path == "/img/1.png"


def test_search_request_minimum_fields() -> None:
    req = SearchRequest(query="q", dataset_ids=[uuid.uuid4()])
    assert req.top_k == 10
    assert req.use_rerank is True
    assert req.query_decomposition is False  # default off


def test_search_result_has_failure_signals() -> None:
    r = SearchResult(citations=[], prompt="")
    assert r.failed_dataset_ids == []
    assert r.warnings == []


def test_resolve_rerank_model() -> None:
    req = SearchRequest(query="q", dataset_ids=[uuid.uuid4()], rerank_model="cohere")

    ds = Dataset(
        id=uuid.uuid4(),
        name="test",
        embed_model="text-embedding-3-small",
        embed_dim=1536,
        rerank_model=None,
    )

    assert resolve_rerank_model(req, ds) == "cohere"
    req2 = SearchRequest(query="q", dataset_ids=[uuid.uuid4()], use_rerank=False)
    assert resolve_rerank_model(req2, ds) is None
