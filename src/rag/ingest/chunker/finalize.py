"""收尾合并与滑动窗口: 合并小块、贴近目标大小、强制不超上限。"""

from __future__ import annotations

from .utils import valid_len


def _join_chunk_pair(left: str, right: str) -> str:
    """拼接相邻 chunk, 交界处无换行时补一个换行以保留 Markdown/HTML 结构。"""
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
    """保证每块不超过 `max_size`, 超出部分走 `sliding_window` 切分。

    步骤:
        1. 遍历 chunks, valid_len <= max_size 的原样保留。
        2. 超出的块走 ``sliding_window`` 切分为多个 <= max_size 的子块。
        3. 拼接结果返回。
    """
    result: list[str] = []
    for chunk in chunks:
        if valid_len(chunk) <= max_size:
            result.append(chunk)
        else:
            result.extend(sliding_window(chunk, max_size, overlap_ratio))
    return result


def merge_small_chunks(chunks: list[str], min_size: int) -> list[str]:
    """将有效长度不足 `min_size` 的块合并到相邻块 (优先后, 末尾向前合并)。

    步骤:
        1. 复制 chunks 到 result (避免原列表 mutation)。
        2. 单次扫描, 对每个 len<min_size 的块:
           2a. 有后继 -> 拼到后继 (pop i)。
           2b. 无后继但有前驱 -> 拼到前驱 (pop i)。
           2c. 否则 (单块) -> 跳过 (i += 1)。
        3. 返回 result。
    """
    if not chunks:
        return chunks

    result = list(chunks)
    i = 0
    while i < len(result):
        # 2a. 小于阈值且有后继, 拼到下一块
        if valid_len(result[i]) < min_size and i + 1 < len(result):
            result[i + 1] = _join_chunk_pair(result[i], result[i + 1])
            result.pop(i)
            continue
        # 2b. 小于阈值且无后继但有前驱, 拼到上一块
        if valid_len(result[i]) < min_size and i > 0:
            result[i - 1] = _join_chunk_pair(result[i - 1], result[i])
            result.pop(i)
            continue
        # 2c. 既无后继也无前驱 (单块) 或 自身已够大
        i += 1
    return result


def merge_chunks_to_target(
    chunks: list[str],
    target_size: int,
    max_size: int,
) -> list[str]:
    """合并相邻块, 直到当前块有效长度接近 `target_size` 且不超 `max_size`。

    步骤:
        1. 边界检查: 空或 target_size<=0 -> 原样返回。
        2. 初始化 current = chunks[0]。
        3. 遍历后续 nxt:
           3a. 尝试拼接 current + nxt, 算 combined_len。
           3b. 若 current < target_size 且 combined_len <= max_size: 继续合并 (current = combined)。
           3c. 否则 flush current, 开启下一轮 (current = nxt)。
        4. 末尾追加残余 current。
    """
    if not chunks or target_size <= 0:
        return chunks

    result: list[str] = []
    current = chunks[0]
    for nxt in chunks[1:]:
        combined = _join_chunk_pair(current, nxt)
        combined_len = valid_len(combined)
        current_len = valid_len(current)
        # 3b. 当前块未达目标且合并后不超上限, 继续合并
        if current_len < target_size and combined_len <= max_size:
            current = combined
            continue
        # 3c. 否则 flush current, 开启下一轮
        result.append(current)
        current = nxt
    result.append(current)
    return result


def sliding_window(text: str, max_size: int, overlap_ratio: float) -> list[str]:
    """字符级滑动窗口切分, 步长 = `max_size * (1 - overlap_ratio)`。

    步骤:
        1. 边界检查: 文本 len <= max_size -> 单块返回。
        2. 算 step = max(1, int(max_size * (1 - overlap_ratio)))。
        3. 滑动切分: 每次取 ``[start, min(start+max_size, n)``。
        4. 防呆: 若 step 过短导致窗口不前进, 把 next_start 对齐到当前窗口末端。
    """
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
        # 4. 步长过短导致窗口无法前进时, 对齐到当前窗口末端
        next_start = start + step
        if next_start < end and end < n:
            next_start = end
        start = next_start
    return chunks
