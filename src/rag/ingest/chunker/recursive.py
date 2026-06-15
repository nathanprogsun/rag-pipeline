"""按规则层级递归切分, 并维护 `last_text` 累积与 heading 上下文。"""

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
    """按 `rules[step]` 递归切分, 累积 `last_text` 直至触发 flush。

    Args:
        text: 当前待切分文本。
        step: 当前规则层级的索引。
        last_text: 上一层累积的未满块, 透传到下层。
        parent_title: 父级 heading 标题, 在 heading 规则下累加。
        rules: 切分规则列表。
        chunk_size: 单块目标大小。
        max_size: 单块硬上限。
        overlap_len: overlap 目标有效字符数。
        paragraph_chunk_min_size: 段落累积 flush 阈值。

    Returns:
        切分得到的 chunk 列表。
    """
    # 递归终止: 规则用尽时做硬切兜底。
    if step >= len(rules):
        combined = last_text + text
        if valid_len(combined) < max_size:
            return [combined]
        return sliding_window(combined, max_size, overlap_len)

    rule = rules[step]
    segments = _apply_rule(text, rule)

    # 阈值必须夹在 chunk_size 之内, 否则末段累积永远不 flush, 会越界。
    flush_threshold = min(paragraph_chunk_min_size, chunk_size)

    chunks: list[str] = []

    for seg_text, seg_title in segments:
        # heading 规则: 强制下钻一层, 累加 parent_title。
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

        # 容量判断: 拼接后长度 vs 规则上限。
        new_text = (last_text + seg_text) if last_text else seg_text
        new_len = valid_len(new_text)

        if new_len > rule.max_len:
            # 略超规则上限, 直接成块并准备 overlap 尾。
            if new_len < int(rule.max_len * 1.2):
                chunks.append(new_text)
                last_text = get_overlap_tail(
                    new_text, step, chunk_size, overlap_len, int(chunk_size * 0.4)
                )
            else:
                # 远超上限, 递归下钻。
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
            # 在规则上限内: 按规则决定直接成块或继续累积。
            if rule.forbid_overlap:
                chunks.append(seg_text)
            else:
                last_text = new_text

    # 残余 last_text 收尾: 太小则并入上一块, 否则自成一块。
    if last_text:
        if chunks and valid_len(last_text) < flush_threshold:
            chunks[-1] = chunks[-1] + last_text
        else:
            chunks.append(last_text)

    return chunks


def _apply_rule(text: str, rule: Rule) -> list[tuple[str, str]]:
    """按 `rule.reg` 切分文本, 返回 `(text, title)` 列表。

    heading 规则的 match 段会被视作 title, 普通 split 规则则保留原文片段。

    Args:
        text: 待切分文本。
        rule: 切分规则。

    Returns:
        `(text, title)` 元组列表, 无匹配时返回单元素。
    """
    parts = re.split(rule.reg, text)
    if len(parts) <= 1:
        return [(text, "")]

    result: list[tuple[str, str]] = []
    for i, p in enumerate(parts):
        if not p.strip():
            continue
        if i % 2 == 1:
            # match 部分: heading 规则取为 title, 其他规则保留原文。
            if rule.reg.startswith(r"^(") and "#" in rule.reg[:6]:
                result.append(("", p.strip()))
            else:
                result.append((p, ""))
        else:
            result.append((p, ""))
    return result
