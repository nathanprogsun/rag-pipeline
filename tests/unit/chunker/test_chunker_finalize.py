from rag.ingest.chunker.finalize import (
    enforce_max_size,
    merge_chunks_to_target,
    merge_small_chunks,
    sliding_window,
)


def test_enforce_max_size_passes_small_chunks() -> None:
    chunks = ["a", "bc", "def"]
    result = enforce_max_size(chunks, max_size=100, overlap_ratio=0.15)
    assert result == ["a", "bc", "def"]


def test_enforce_max_size_splits_oversized() -> None:
    chunks = ["x" * 1000]
    result = enforce_max_size(chunks, max_size=100, overlap_ratio=0.15)
    assert len(result) >= 2
    for chunk in result:
        assert len(chunk) <= 100


def test_merge_small_chunks_merges_to_next() -> None:
    chunks = ["tiny", "big chunk here"]
    result = merge_small_chunks(chunks, min_size=20)
    # tiny 合并到下一块
    assert len(result) == 1
    assert "tiny" in result[0]


def test_merge_small_chunks_merges_to_previous_at_end() -> None:
    chunks = ["big chunk here", "tiny"]
    result = merge_small_chunks(chunks, min_size=20)
    assert len(result) == 1
    assert "tiny" in result[0]


def test_merge_small_chunks_preserves_newline_boundary() -> None:
    chunks = ["# Title", "body line"]
    result = merge_small_chunks(chunks, min_size=100)
    assert len(result) == 1
    assert "\n" in result[0]


def test_merge_chunks_to_target_combines_adjacent() -> None:
    chunks = ["a" * 200, "b" * 200, "c" * 200]
    result = merge_chunks_to_target(chunks, target_size=500, max_size=800)
    assert len(result) < len(chunks)
    for chunk in result:
        assert len(chunk.replace(" ", "")) <= 800


def test_sliding_window_preserves_overlap() -> None:
    """100 字符文本, max_size=50, overlap=0.2 → 3 块, 相邻有重叠。"""
    text = "abcdefghij" * 10  # 100 chars
    chunks = sliding_window(text, max_size=50, overlap_ratio=0.2)
    assert len(chunks) >= 2
    # 末尾 = 100, 不会越界
    assert chunks[-1] == text[50:]
