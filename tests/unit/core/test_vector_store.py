import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rag.infra.pg.vector_store import VectorRetriever
from tests._fakes import ConstantEmbeddings

EMBED_DIM = 1536


_FAKE_EMB_VECTOR = [0.1] * EMBED_DIM
FakeEmbeddings = ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)


@pytest.mark.asyncio
async def test_vector_retriever_uses_repository() -> None:
    dataset_id = uuid.uuid4()
    mock_session = AsyncMock()
    mock_session.__aenter__.return_value = mock_session
    mock_session.__aexit__.return_value = None
    mock_factory = MagicMock(return_value=mock_session)

    with (
        patch("rag.infra.pg.vector_store.AsyncSessionLocal", mock_factory),
        patch(
            "rag.infra.pg.vector_store.ChunkRepository.search_by_vector",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_search,
    ):
        retriever = VectorRetriever(dataset_id=dataset_id, embed_model=FakeEmbeddings)
        hits = await retriever.search("test", top_k=5)

    assert isinstance(hits, list)
    mock_search.assert_awaited_once()
    call_args = mock_search.await_args
    assert call_args is not None
    assert len(call_args.args[0]) == EMBED_DIM
    assert call_args.args[1] == dataset_id
    assert call_args.args[2] == 5
