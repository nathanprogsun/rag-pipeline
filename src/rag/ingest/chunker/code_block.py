"""代码块检测与保护: 用 marker 占位避免内部换行被切碎, 同时暴露 fence / HTML 预代码块正则供 core.py 复用。"""

from __future__ import annotations

import re

from .utils import __CB_NL__

# 完整代码块: ```...``` 或 ~~~...~~~, 非贪婪 + DOTALL, 容许内部任意字符。
CODE_BLOCK_RE: re.Pattern[str] = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")

# 任意 fence 起点 (用于 per-chunk has_code 检测, 不要求配对完整)。
CODE_FENCE_RE: re.Pattern[str] = re.compile(r"```|~~~")

# HTML <pre><code> 块, 用于 per-chunk has_code 检测。
HTML_PRE_CODE_RE: re.Pattern[str] = re.compile(r"<pre\b[\s\S]*?<code\b", re.IGNORECASE)


def is_code_block(text: str) -> bool:
    """判定 ``text`` 是否为完整的代码块 (```/~~~ 围栏完整包裹)。

    NOTE: not used in current production pipeline; kept for test coverage / 未来子模块复用。
    """
    s = text.strip()
    return bool(re.fullmatch(r"```[\s\S]*?```|~~~[\s\S]*?~~~", s))


def protect_code_block(text: str) -> str:
    """将代码块内的换行替换为 ``utils.__CB_NL__`` marker, 后续切分不会从内部断开。

    Args:
        text: 原始文本。

    Returns:
        代码块换行已被占位符替换的文本。
    """
    return CODE_BLOCK_RE.sub(lambda m: m.group(0).replace("\n", __CB_NL__), text)
