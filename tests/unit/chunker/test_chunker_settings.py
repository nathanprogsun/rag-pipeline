from rag.ingest.chunker.settings import ChunkSettings


def test_default_settings() -> None:
    s = ChunkSettings()
    assert s.chunk_size == 1000
    assert s.max_chunk_size == 8000
    assert s.overlap_ratio == 0.10
    assert s.paragraph_chunk_deep == 5
    assert s.paragraph_chunk_min_size == 200
    assert s.min_chunk_size == 256
    assert s.custom_separator is None


def test_overlap_ratio_clamped() -> None:
    """overlap_ratio 应被限制在 [0, 0.5]。"""
    s = ChunkSettings(overlap_ratio=2.0)
    assert 0 <= s.overlap_ratio <= 0.5


def test_custom_separator_is_regex_str() -> None:
    s = ChunkSettings(custom_separator=r"---")
    assert s.custom_separator == r"---"
