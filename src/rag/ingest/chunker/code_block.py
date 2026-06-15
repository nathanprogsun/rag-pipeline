"""代码块检测与保护: 用 marker 占位避免内部换行被切碎。"""

from __future__ import annotations

import re

_CODE_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
_NL_MARKER = "__CB_NL__"


def is_code_block(text: str) -> bool:
    """判断文本是否为完整的代码块。

    Args:
        text: 待检测文本。

    Returns:
        完整由反引号或波浪号围栏包裹时为 True。
    """
    s = text.strip()
    return bool(re.fullmatch(r"```[\s\S]*?```|~~~[\s\S]*?~~~", s))


def protect_code_block(text: str) -> str:
    """将代码块内的换行替换为 marker, 后续切分不会从内部断开。

    Args:
        text: 原始文本。

    Returns:
        代码块换行已被占位符替换的文本。
    """
    return _CODE_RE.sub(lambda m: m.group(0).replace("\n", _NL_MARKER), text)
