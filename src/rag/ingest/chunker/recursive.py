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

    步骤:
        1. 终止条件: step >= len(rules) -> 合并 last_text 后硬切 (sliding_window)。
        2. 取 rule = rules[step], 切出 segments。
        3. flush_threshold = min(paragraph_chunk_min_size, chunk_size)。
        4. 遍历 segments:
            4a. heading 规则: 强制下钻, 累加 parent_title。
            4b. 拼接超长:
                - 略超 (1.2x 内): 成块 + 取 overlap tail 当作下个块的 last_text。
                - 远超: 下钻, 把 inner[:-1] 拼入, 末段按 flush_threshold 决定 flush / 保留。
            4c. 未超长:
                - forbid_overlap: 直接成块。
                - 否则: 累积到 last_text。
        5. 收尾: 残余 last_text 太小并入上一块, 否则自成一块。

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
    # 步骤 1: 递归终止 -> 硬切兜底
    if step >= len(rules):
        combined = last_text + text
        if valid_len(combined) < max_size:
            return [combined]
        return sliding_window(combined, max_size, overlap_len)

    rule = rules[step]
    segments = _apply_rule(text, rule)

    # 步骤 3: 阈值必须夹在 chunk_size 之内, 否则末段累积永远不 flush, 会越界
    flush_threshold = min(paragraph_chunk_min_size, chunk_size)

    chunks: list[str] = []

    for seg_text, seg_title in segments:
        # 步骤 4a: heading 规则 -> 强制下钻, 累加 parent_title
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

        # 步骤 4b/4c: 容量判断
        new_text = (last_text + seg_text) if last_text else seg_text
        new_len = valid_len(new_text)

        if new_len > rule.max_len:
            # 4b-i. 略超规则上限, 直接成块并准备 overlap 尾
            if new_len < int(rule.max_len * 1.2):
                chunks.append(new_text)
                last_text = get_overlap_tail(
                    new_text,
                    step,
                    rules,
                    chunk_size,
                    overlap_len,
                    int(chunk_size * 0.4),
                )
            else:
                # 4b-ii. 远超上限, 递归下钻
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
            # 4c. 在规则上限内: 按规则决定直接成块或继续累积
            if rule.forbid_overlap:
                chunks.append(seg_text)
            else:
                last_text = new_text

    # 步骤 5: 残余 last_text 收尾 -> 太小则并入上一块, 否则自成一块
    if last_text:
        if chunks and valid_len(last_text) < flush_threshold:
            chunks[-1] = chunks[-1] + last_text
        else:
            chunks.append(last_text)

    return chunks


def _apply_rule(text: str, rule: Rule) -> list[tuple[str, str]]:
    """按 `rule.reg` 切分文本, 返回 `(text, title)` 列表。

    heading 规则的 match 段会被视作 title, 普通 split 规则则保留原文片段。
    """
    parts = re.split(rule.reg, text)
    if len(parts) <= 1:
        return [(text, "")]

    result: list[tuple[str, str]] = []
    for i, p in enumerate(parts):
        if not p.strip():
            continue
        if i % 2 == 1:
            # match 部分: heading 规则取为 title, 其他规则保留原文
            if rule.reg.startswith(r"^(") and "#" in rule.reg[:6]:
                result.append(("", p.strip()))
            else:
                result.append((p, ""))
        else:
            result.append((p, ""))
    return result
