"""Overlap 倒序累积。

策略:
  1. 末段文本按 step 规则 hit 处切分
  2. 倒序遍历切分结果, 累积至 ≤ overlap_len
  3. 硬上限 max_overlap_len (= chunk_size * 0.4)
  4. 若单片段超过 max, 切片到 overlap_len (无法再细切)
  5. 字符切片按 valid_len 反向定位, 避免 Unicode 边界切坏
"""

from __future__ import annotations

import re

from .rules import STEPS
from .utils import valid_len


def _split_by_step_rule(text: str, step: int) -> list[str]:
    """按 STEPS[step] 的正则切分, 过滤空段。"""
    if step >= len(STEPS):
        return [text]
    rule_reg = STEPS[step].reg
    parts = re.split(rule_reg, text)
    return [p for p in parts if p.strip()]


def _char_offset_from_valid(text: str, valid_budget: int) -> int:
    """从 text 末尾往前数 valid_budget 个有效字符, 返回对应 char offset。"""
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
    """返回 text 末尾的 overlap 片段, 长度按 valid_len 控制。"""
    if step >= len(STEPS) or overlap_len <= 0:
        return ""

    pieces = _split_by_step_rule(text, step)
    overlap_text = ""

    for piece in reversed(pieces):
        # 如果单片段本身就 > max, 直接切片到 overlap_len (无法细切)
        if valid_len(piece) > max_overlap_len:
            offset = _char_offset_from_valid(piece, overlap_len)
            return piece[offset:]

        candidate = piece + overlap_text
        cand_valid = valid_len(candidate)

        if cand_valid > overlap_len:
            # 已累积的 overlap_text 在范围内, 加上这块就超, 返回已累积
            return overlap_text

        overlap_text = candidate

    return overlap_text
