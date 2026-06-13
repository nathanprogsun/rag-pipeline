"""Chunker 收尾: enforce_max_size + merge_small + merge_to_target + sliding_window。"""

from __future__ import annotations

from .utils import valid_len


def _join_chunk_pair(left: str, right: str) -> str:
    """合并相邻 chunk, 交界处无换行时插入 ``\\n`` 保留 Markdown/HTML 结构。"""
    if not left:
        return right
    if not right:
        return left
    if left.endswith("\n") or right.startswith("\n"):
        return left + right
    return left + "\n" + right


def enforce_max_size(
    chunks: list[str],
    max_size: int,
    overlap_ratio: float,
) -> list[str]:
    """每块都不超过 max_size, 超出走 sliding_window。"""
    result: list[str] = []
    for chunk in chunks:
        if valid_len(chunk) <= max_size:
            result.append(chunk)
        else:
            result.extend(sliding_window(chunk, max_size, overlap_ratio))
    return result


def merge_small_chunks(chunks: list[str], min_size: int) -> list[str]:
    """把 < min_size 的块合并到相邻块 (优先后, 末尾优先前)。"""
    if not chunks:
        return chunks

    result = list(chunks)
    i = 0
    while i < len(result):
        # 小于 min_size 且有后继 → 拼到下一块
        if valid_len(result[i]) < min_size and i + 1 < len(result):
            result[i + 1] = _join_chunk_pair(result[i], result[i + 1])
            result.pop(i)
            continue
        # 小于 min_size 且无后继但有前驱 → 拼到上一块
        if valid_len(result[i]) < min_size and i > 0:
            result[i - 1] = _join_chunk_pair(result[i - 1], result[i])
            result.pop(i)
            continue
        i += 1
    return result


def merge_chunks_to_target(
    chunks: list[str],
    target_size: int,
    max_size: int,
) -> list[str]:
    """将相邻块合并直到 ``valid_len`` 接近 ``target_size`` (不超过 ``max_size``)。"""
    if not chunks or target_size <= 0:
        return chunks

    result: list[str] = []
    current = chunks[0]
    for nxt in chunks[1:]:
        combined = _join_chunk_pair(current, nxt)
        combined_len = valid_len(combined)
        current_len = valid_len(current)
        # 当前块未达目标且合并后不超 max → 继续合并
        if current_len < target_size and combined_len <= max_size:
            current = combined
            continue
        result.append(current)
        current = nxt
    result.append(current)
    return result


def sliding_window(text: str, max_size: int, overlap_ratio: float) -> list[str]:
    """字符级滑动窗口, 步长 = max_size * (1 - overlap_ratio)。"""
    n = len(text)
    if n <= max_size:
        return [text]
    step = max(1, int(max_size * (1 - overlap_ratio)))
    chunks: list[str] = []
    start = 0
    while start < n:
        end = min(start + max_size, n)
        chunks.append(text[start:end])
        if end >= n:
            break
        # 若步长跳过会越界, 直接对齐到 max_size 边界
        next_start = start + step
        if next_start < end and end < n:
            next_start = end
        start = next_start
    return chunks
