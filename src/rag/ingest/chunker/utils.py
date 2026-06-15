"""Chunker 工具函数: 长度统计、文本规范化、代码块 marker 还原 (模块级预编译正则)。"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"[\s　 ]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_CN_INNER_SPACE_RE = re.compile(r"([一-龥])[ \t　]+([一-龥])")


def valid_len(text: str) -> int:
    """返回 ``text`` 去除全部空白字符 (含全角空格 ``U+3000``) 后的有效长度。"""
    return len(_WHITESPACE_RE.sub("", text))


def simple_text(text: str) -> str:
    """规范化 ``text``: 去除中文字符间空白 + 合并 3+ 换行 + 替换控制字符 + 首尾去空白。"""
    text = _CN_INNER_SPACE_RE.sub(r"\1\2", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    text = _CONTROL_RE.sub(" ", text)
    return text.strip()


def restore_code_block_marker(text: str) -> str:
    """将代码块占位 marker ``__CB_NL__`` 还原为换行符。"""
    return text.replace("__CB_NL__", "\n")
