"""Chunker 分隔符 Rule 元数据表: 按粗到细排序的固定 ~12 级, 标题/代码块/表格/换行/标点逐级收敛。"""

from __future__ import annotations

from dataclasses import dataclass

CUSTOM_SPLIT_SIGN = "-----CUSTOM_SPLIT_SIGN-----"


@dataclass(frozen=True)
class Rule:
    reg: str
    max_len: int
    split_around: bool = False
    forbid_overlap: bool = False
    custom: bool = False


def _heading_rules(chunk_size: int, deep: int) -> list[Rule]:
    """生成 H1 到 H(deep) 的标题级 Rule。deep 上限截断为 5, 避免标题级过深导致规则冗余。"""
    max_deep = min(deep, 5)
    return [
        Rule(
            reg=r"^(" + "#" * i + r"\s+[^\n]+\n)",
            max_len=chunk_size,
            forbid_overlap=True,
        )
        for i in range(1, max_deep + 1)
    ]


def _code_block_rule(code_block_max_len: int) -> Rule:
    return Rule(
        reg=r"(```[\s\S]*?```|~~~[\s\S]*?~~~)",
        max_len=code_block_max_len,
        split_around=True,
        forbid_overlap=True,
    )


def _html_table_rule(chunk_size: int) -> Rule:
    """HTML `<table>...</table>` 块 Rule。使用非贪婪 + DOTALL 匹配, 容许任意属性/嵌套标签, ``split_around=True`` 保留整块不被切碎。"""
    return Rule(
        reg=r"(<table\b[\s\S]*?</table>)",
        max_len=chunk_size,
        split_around=True,
        forbid_overlap=True,
    )


def _md_table_rule(chunk_size: int) -> Rule:
    return Rule(
        reg=r"((?:^|\n)(?:\|[^\n]*\|\n)+)",
        max_len=chunk_size,
        split_around=True,
        forbid_overlap=True,
    )


def _newline_rules(chunk_size: int) -> list[Rule]:
    return [
        Rule(reg=r"\n{2,}", max_len=chunk_size, forbid_overlap=True),
        Rule(reg=r"\n", max_len=chunk_size, forbid_overlap=True),
    ]


def _punct_merged_rule(chunk_size: int) -> Rule:
    """中英细粒度标点合并 Rule: 中文 ``。！？；,`` + 英文 ``! ? ; ,`` (后接空格)。合并后 ``forbid_overlap=False``, 是唯一允许 overlap 的级别。"""
    return Rule(
        reg=r"([。！？；，]|[!?;,] )",
        max_len=chunk_size,
        forbid_overlap=False,  # 唯一允许 overlap 的级别
    )


def build_steps(
    chunk_size: int,
    max_size: int,
    paragraph_chunk_deep: int = 5,
    custom_reg: list[str] | None = None,
) -> list[Rule]:
    """构造按优先级排序的 Rule 列表。

    Args:
        chunk_size: 常规 chunk 长度上限。
        max_size: 硬上限 (用于代码块与 custom rule)。
        paragraph_chunk_deep: 标题级深度, 默认 5。
        custom_reg: 自定义分隔符正则列表, 作为前 N 条 custom rule 插入; 传入空/None 时仅追加默认占位。

    Returns:
        ``list[Rule]`` 长度 = 12 (无 custom_reg) + ``len(custom_reg)``。
    """
    code_block_max_len = min(max_size, chunk_size * 4)

    custom_rules: list[Rule] = [
        Rule(reg=reg.replace("\\n", "\n"), max_len=max_size, custom=True)
        for reg in (custom_reg or [])
    ]

    return [
        *custom_rules,
        Rule(reg=CUSTOM_SPLIT_SIGN, max_len=max_size, custom=True),
        *_heading_rules(chunk_size, paragraph_chunk_deep),
        _code_block_rule(code_block_max_len),
        _html_table_rule(chunk_size),
        _md_table_rule(chunk_size),
        *_newline_rules(chunk_size),
        _punct_merged_rule(chunk_size),
    ]


def default_steps(chunk_size: int, max_size: int) -> list[Rule]:
    """``paragraph_chunk_deep=5`` 的默认 STEPS 列表。"""
    return build_steps(chunk_size, max_size, paragraph_chunk_deep=5)


STEPS: list[Rule] = default_steps(chunk_size=1000, max_size=8000)
