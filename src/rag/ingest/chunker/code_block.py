"""Code block 检测与保护: 用 marker 占位避免内部 \n 被切碎。"""

from __future__ import annotations

import re

_CODE_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_NL_MARKER = "__CB_NL__"


def is_code_block(text: str) -> bool:
    s = text.strip()
    return bool(re.fullmatch(r"```[\s\S]*?```|~~~[\s\S]*?~~~", s))


def protect_code_block(text: str) -> str:
    """将代码块内的 \n 替换为 marker, 后续 chunk 边界不会再切到。"""
    return _CODE_RE.sub(lambda m: m.group(0).replace("\n", _NL_MARKER), text)
