"""Chunker 工具函数: valid_len + simple_text (模块级预编译正则)。"""

from __future__ import annotations

import re

_WHITESPACE_RE = re.compile(r"[\s　 ]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
_CN_INNER_SPACE_RE = re.compile(r"([一-龥])[ \t　]+([一-龥])")


def valid_len(text: str) -> int:
    """有效长度: 去除全部空白字符 (含全角空格 U+3000)。"""
    return len(_WHITESPACE_RE.sub("", text))


def simple_text(text: str) -> str:
    """规范化文本: 去中文字符间空格 + 合并 3+ 换行 + 清控制字符。"""
    text = _CN_INNER_SPACE_RE.sub(r"\1\2", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    text = _CONTROL_RE.sub(" ", text)
    return text.strip()


def restore_code_block_marker(text: str) -> str:
    """还原代码块占位 marker 为 \n。"""
    return text.replace("__CB_NL__", "\n")
