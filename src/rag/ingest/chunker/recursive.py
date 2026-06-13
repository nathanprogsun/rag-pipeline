"""common_split 递归主体。

输入: text, step, last_text, parent_title, rules + 配置
输出: list[str] chunks
关键不变量:
  - step >= len(rules) → 终止 + 兜底硬切
  - last_text 透传到 step+1 (累积语义)
  - parent_title 累加 (Markdown heading 上下文)
  - 单块超 max_size → enforce_max_size
"""

from __future__ import annotations

import re

from .finalize import sliding_window
from .overlap import get_overlap_tail
from .rules import Rule
from .utils import valid_len


def common_split(
    text: str,
    step: int,
    last_text: str,
    parent_title: str,
    rules: list[Rule],
    chunk_size: int,
    max_size: int,
    overlap_len: int,
    paragraph_chunk_min_size: int = 100,
) -> list[str]:
    # ── 终止条件 ──
    if step >= len(rules):
        combined = last_text + text
        if valid_len(combined) < max_size:
            return [combined]
        return sliding_window(combined, max_size, overlap_len)

    rule = rules[step]
    segments = _apply_rule(text, rule)

    # paragraph_chunk_min_size 不应超过 chunk_size, 否则 last_text 永远不会被 flush,
    # 导致递归末段累积越界。clamp 到 chunk_size 上限。
    flush_threshold = min(paragraph_chunk_min_size, chunk_size)

    chunks: list[str] = []

    for seg_text, seg_title in segments:
        # ── Heading 累加: 强制下钻 step+1, parent_title 透传 ──
        if rule.reg.startswith(r"^(") and "#" in rule.reg[:6]:
            new_parent = parent_title + seg_title if seg_title else parent_title
            inner = common_split(
                text=seg_text,
                step=step + 1,
                last_text="",
                parent_title=new_parent,
                rules=rules,
                chunk_size=chunk_size,
                max_size=max_size,
                overlap_len=overlap_len,
                paragraph_chunk_min_size=paragraph_chunk_min_size,
            )
            chunks.extend(inner)
            continue

        # ── 容量判断 ──
        new_text = (last_text + seg_text) if last_text else seg_text
        new_len = valid_len(new_text)

        if new_len > rule.max_len:
            # 略超 → 直接成块
            if new_len < int(rule.max_len * 1.2):
                chunks.append(new_text)
                last_text = get_overlap_tail(
                    new_text, step, chunk_size, overlap_len, int(chunk_size * 0.4)
                )
            else:
                # 递归下钻
                inner = common_split(
                    text=seg_text,
                    step=step + 1,
                    last_text=last_text,
                    parent_title=parent_title,
                    rules=rules,
                    chunk_size=chunk_size,
                    max_size=max_size,
                    overlap_len=overlap_len,
                    paragraph_chunk_min_size=paragraph_chunk_min_size,
                )
                chunks.extend(inner[:-1])
                last = inner[-1] if inner else ""
                if valid_len(last) >= flush_threshold:
                    chunks.append(last)
                    last_text = ""
                else:
                    last_text = last
        else:
            # 累积
            if rule.forbid_overlap:
                chunks.append(seg_text)
            else:
                last_text = new_text

    # ── 残余 last_text 收尾 ──
    if last_text:
        if chunks and valid_len(last_text) < flush_threshold:
            chunks[-1] = chunks[-1] + last_text
        else:
            chunks.append(last_text)

    return chunks


def _apply_rule(text: str, rule: Rule) -> list[tuple[str, str]]:
    """应用 rule.reg 切分, 返回 [(text, title), ...]。"""
    parts = re.split(rule.reg, text)
    if len(parts) <= 1:
        return [(text, "")]

    result: list[tuple[str, str]] = []
    for i, p in enumerate(parts):
        if not p.strip():
            continue
        if i % 2 == 1:
            # match 部分 (title 或 split_around match)
            if rule.reg.startswith(r"^(") and "#" in rule.reg[:6]:
                result.append(("", p.strip()))
            else:
                result.append((p, ""))
        else:
            result.append((p, ""))
    return result
