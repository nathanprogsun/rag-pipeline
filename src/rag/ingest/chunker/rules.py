"""Chunker 分隔符 Rule 元数据表 (从 17 级收敛到 ~10 级)。

收敛策略 (业内常见做法):
  - 5 个标题级 H1-H5 保留 (默认 paragraph_chunk_deep=5)
  - 5 个标点规则合并为 1 个 (punct_merged), overlap 行为统一
  - 保留 HTML <table> rule (split_around, 注释 "HTML Table tag 尽可能保障完整")
  - 自定义 separator 走 _split_custom 旁路, 不进 build_steps 内部

固定 11 级 (按优先级粗到细):
  step 0:  CUSTOM_SPLIT_SIGN (默认占位)
  step 1-5: H1 / H2 / H3 / H4 / H5 (forbid_overlap=True)
  step 6:  code_block ```/~~~ (split_around=True, forbid_overlap=True)
  step 7:  html_table <table>...</table> (split_around=True, forbid_overlap=True)
  step 8:  md_table (split_around=True, forbid_overlap=True)
  step 9:  \\n\\n (段落, forbid_overlap=True)
  step 10: \\n   (单换行, forbid_overlap=True)
  step 11: punct_merged (中英标点合并, 允许 overlap)
"""

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
    """H1-H5 标题级。deep 上限 5 (业内常见默认值)。"""
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
    """HTML <table>...</table> 块, split_around 保留整块不被切碎。

    业内常见做法: 注释 "HTML Table tag 尽可能保障完整"。
    使用非贪婪 + DOTALL 匹配嵌套表格, 但容许任意属性/嵌套标签。
    """
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
    """5 种标点合并为 1 条: 中: 。！？；, + 英: ! ? ; , (后接空格)。

    合并理由: overlap ratio >= 0.15 时, overlap 会跨过细粒度标点,
    实战中合并不损失语义, 但 chunk 数更稳定。
    """
    return Rule(
        reg=r"([。！？；，]|[!?;,] )",
        max_len=chunk_size,
        forbid_overlap=False,  # 唯一允许 overlap 的级
    )


def build_steps(
    chunk_size: int,
    max_size: int,
    paragraph_chunk_deep: int = 5,
    custom_reg: list[str] | None = None,
) -> list[Rule]:
    """构造 Rule 列表。

    Args:
        custom_reg: 向后兼容参数, 旧 API 传入后作为前 N 条 custom rules 插入。

    Returns:
        list[Rule] 长度 = 12 (无 custom_reg) + len(custom_reg)
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
    """无 custom_reg 的默认 STEPS。"""
    return build_steps(chunk_size, max_size, paragraph_chunk_deep=5)


STEPS: list[Rule] = default_steps(chunk_size=1000, max_size=8000)
