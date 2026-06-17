"""按 step 规则倒序累积 overlap 片段。

注意:  ``get_overlap_tail`` 接 ``rules: list[Rule]`` 形参而非模块级常量, 避免与 Chunker
实际 settings 隐式耦合 (历史: 引用 ``rules.STEPS = default_steps(1000, 8000)``, regex
虽与 chunk_size 无关, 但语义上仍绑死在硬编码值上)。
"""

from __future__ import annotations

import re

from .rules import Rule
from .utils import valid_len


def _split_by_step_rule(text: str, step: int, rules: list[Rule]) -> list[str]:
    """按 ``rules[step]`` 的正则切分, 过滤空段。

    Args:
        text: 待切分文本。
        step: 规则索引, 越界则原样返回。
        rules: Chunker 构造的 Rule 列表 (与切分时同源, 才能对齐断点)。

    Returns:
        非空片段列表。
    """
    if step >= len(rules):
        return [text]
    rule_reg = rules[step].reg
    parts = re.split(rule_reg, text)
    return [p for p in parts if p.strip()]


def _char_offset_from_valid(text: str, valid_budget: int) -> int:
    """从文本末尾反向定位, 找到包含 ``valid_budget`` 个有效字符的字符偏移。

    之所以按 `valid_len` 反向数, 是为了避免在 Unicode 边界切坏。
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
    rules: list[Rule],
    chunk_size: int,
    overlap_len: int,
    max_overlap_len: int,
) -> str:
    """从文本末尾提取 overlap 片段, 长度受 `valid_len` 控制。

    步骤:
        1. 边界检查: 越界 / overlap_len<=0 -> ""。
        2. 按 ``rules[step]`` regex 切出 pieces (与切分时同一组 rule, 才能对齐断点)。
        3. 倒序累积:
           3a. 单片 > max_overlap_len: 按 valid_len 反向定位直接截断。
           3b. 累积后超 overlap_len: 返回当前 overlap_text。
           3c. 否则 piece 接入 overlap_text 继续。
        4. 返回 overlap_text。

    Args:
        text: 源文本。
        step: 当前 step, 用于选择切分正则。
        rules: Chunker 构造的 Rule 列表。
        chunk_size: 配置的 chunk 大小 (保留以备未来扩展)。
        overlap_len: 目标 overlap 有效字符数。
        max_overlap_len: overlap 的硬上限 (通常为 ``chunk_size * 0.4``)。

    Returns:
        倒序累积得到的 overlap 字符串。
    """
    _ = chunk_size  # 保留形参以备未来扩展, 当前不参与逻辑
    if step >= len(rules) or overlap_len <= 0:
        return ""

    pieces = _split_by_step_rule(text, step, rules)
    overlap_text = ""

    for piece in reversed(pieces):
        # 3a. 单片段超过上限, 无法再细切, 直接按有效字符数截断。
        if valid_len(piece) > max_overlap_len:
            offset = _char_offset_from_valid(piece, overlap_len)
            return piece[offset:]

        candidate = piece + overlap_text
        cand_valid = valid_len(candidate)

        # 3b. 加上当前片段会超, 返回已累积部分。
        if cand_valid > overlap_len:
            return overlap_text

        # 3c. 接入继续累积。
        overlap_text = candidate

    return overlap_text
