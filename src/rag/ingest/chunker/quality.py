"""Chunk 质量指标: 供 CLI ``--chunk-stats`` 与回归测试复用。"""

from __future__ import annotations

from dataclasses import dataclass

from rag.ingest.types import Chunk

_BAD_BOUNDARY_START: frozenset[str] = frozenset("，。！？；、,.;!?")


@dataclass(frozen=True)
class ChunkQualityMetrics:
    chunk_count: int
    avg_valid_len: float
    median_valid_len: float
    min_valid_len: int
    max_valid_len: int
    heading_stack_coverage: float
    bad_boundary_rate: float
    size_in_target_band: float


def measure_chunks(chunks: list[Chunk], chunk_size: int) -> ChunkQualityMetrics:
    """从 ``Chunk`` 列表计算切分质量指标。"""
    if not chunks:
        return ChunkQualityMetrics(
            chunk_count=0,
            avg_valid_len=0.0,
            median_valid_len=0.0,
            min_valid_len=0,
            max_valid_len=0,
            heading_stack_coverage=0.0,
            bad_boundary_rate=0.0,
            size_in_target_band=0.0,
        )

    valid_lens = [c.metadata.valid_len for c in chunks]
    n = len(valid_lens)
    sorted_lens = sorted(valid_lens)
    mid = n // 2
    median = (
        sorted_lens[mid]
        if n % 2 == 1
        else (sorted_lens[mid - 1] + sorted_lens[mid]) / 2
    )

    with_heading = sum(1 for c in chunks if c.metadata.heading_stack)
    bad_start = sum(
        1 for c in chunks if c.text.strip() and c.text.strip()[0] in _BAD_BOUNDARY_START
    )

    low = int(chunk_size * 0.3)
    in_band = sum(1 for vl in valid_lens if low <= vl <= chunk_size)

    return ChunkQualityMetrics(
        chunk_count=n,
        avg_valid_len=sum(valid_lens) / n,
        median_valid_len=median,
        min_valid_len=min(valid_lens),
        max_valid_len=max(valid_lens),
        heading_stack_coverage=with_heading / n,
        bad_boundary_rate=bad_start / n,
        size_in_target_band=in_band / n,
    )


def format_chunk_stats(metrics: ChunkQualityMetrics, chunk_size: int) -> str:
    """人类可读的 stats 块 (多行)。"""
    return (
        f"chunk_stats: count={metrics.chunk_count} "
        f"avg_valid_len={metrics.avg_valid_len:.0f} "
        f"median={metrics.median_valid_len:.0f} "
        f"min={metrics.min_valid_len} max={metrics.max_valid_len}\n"
        f"heading_stack_coverage={metrics.heading_stack_coverage:.0%} "
        f"bad_boundary_rate={metrics.bad_boundary_rate:.0%} "
        f"size_in_band[{int(chunk_size * 0.3)}-{chunk_size}]="
        f"{metrics.size_in_target_band:.0%}"
    )
