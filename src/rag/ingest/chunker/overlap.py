"""按 step 规则倒序累积 overlap 片段。"""

from __future__ import annotations

import re

from .rules import STEPS
from .utils import valid_len


def _split_by_step_rule(text: str, step: int) -> list[str]:
    """按 `STEPS[step]` 的正则切分, 过滤空段。

    Args:
        text: 待切分文本。
        step: 规则索引, 越界则原样返回。

    Returns:
        非空片段列表。
    """
    if step >= len(STEPS):
        return [text]
    rule_reg = STEPS[step].reg
    parts = re.split(rule_reg, text)
    return [p for p in parts if p.strip()]


def _char_offset_from_valid(text: str, valid_budget: int) -> int:
    """从文本末尾反向定位, 找到包含 ``valid_budget`` 个有效字符的字符偏移。

    之所以按 `valid_len` 反向数, 是为了避免在 Unicode 边界切坏。

    Args:
        text: 原始文本。
        valid_budget: 目标有效字符数 (空白不计)。

    Returns:
        起始字符偏移。
    """
    if valid_budget <= 0:
        return len(text)
    count = 0
    i = len(text) - 1
    while i >= 0 and count < valid_budget:
        if not text[i].isspace() and text[i] != "　":
            count += 1
        i -= 1
    return i + 1


def get_overlap_tail(
    text: str,
    step: int,
    chunk_size: int,
    overlap_len: int,
    max_overlap_len: int,
) -> str:
    """从文本末尾提取 overlap 片段, 长度受 `valid_len` 控制。

    Args:
        text: 源文本。
        step: 当前 step, 用于选择切分正则。
        chunk_size: 配置的 chunk 大小。
        overlap_len: 目标 overlap 有效字符数。
        max_overlap_len: overlap 的硬上限 (通常为 ``chunk_size * 0.4``)。

    Returns:
        倒序累积得到的 overlap 字符串; 越界或 `overlap_len <= 0` 时返回空串。
    """
    if step >= len(STEPS) or overlap_len <= 0:
        return ""

    pieces = _split_by_step_rule(text, step)
    overlap_text = ""

    for piece in reversed(pieces):
        # 单片段超过上限, 无法再细切, 直接按有效字符数截断。
        if valid_len(piece) > max_overlap_len:
            offset = _char_offset_from_valid(piece, overlap_len)
            return piece[offset:]

        candidate = piece + overlap_text
        cand_valid = valid_len(candidate)

        if cand_valid > overlap_len:
            # 加上当前片段会超, 返回已累积部分。
            return overlap_text

        overlap_text = candidate

    return overlap_text
