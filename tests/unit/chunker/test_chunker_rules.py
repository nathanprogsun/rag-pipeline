from rag.ingest.chunker.rules import (
    CUSTOM_SPLIT_SIGN,
    Rule,
    build_steps,
)

# 统一的默认 Rule 列表 (与原模块级 STEPS 等价, 用于断言结构)
DEFAULT_STEPS = build_steps(chunk_size=1000, max_size=8000, paragraph_chunk_deep=5)


def test_custom_split_sign_constant() -> None:
    assert CUSTOM_SPLIT_SIGN == "-----CUSTOM_SPLIT_SIGN-----"


def test_default_steps_count() -> None:
    """默认 deep=5 时: 1 sign + 5 heading + 1 code + 1 html_table + 1 md + 2 newline + 1 punct = 12。"""
    assert len(DEFAULT_STEPS) == 12


def test_default_steps_contains_required_levels() -> None:
    """验证默认 Rule 包含所有必须的分隔符级: H1-H5 / code / html_table / md / nl*2 / punct。"""
    # H1-H5 标题级 (forbid_overlap=True)
    heading_rules = [
        r for r in DEFAULT_STEPS if r.reg.startswith("^(#") and "H" not in r.reg
    ]
    assert len(heading_rules) == 5  # H1-H5

    # code block rule (split_around=True)
    code_rules = [r for r in DEFAULT_STEPS if r.split_around and "```" in r.reg]
    assert len(code_rules) == 1

    # html table rule (split_around=True, matches <table>)
    html_table_rules = [
        r for r in DEFAULT_STEPS if r.split_around and "<table" in r.reg
    ]
    assert len(html_table_rules) == 1

    # 至少 1 条 \n\n 和 1 条 \n
    has_double_nl = any(r.reg == r"\n{2,}" for r in DEFAULT_STEPS)
    has_single_nl = any(r.reg == r"\n" for r in DEFAULT_STEPS)
    assert has_double_nl and has_single_nl

    # 1 条合并的 punct rule (允许 overlap)
    punct_rules = [
        r for r in DEFAULT_STEPS if not r.forbid_overlap and r not in heading_rules
    ]
    assert any("。" in r.reg for r in punct_rules)  # 含中英标点合并

    # CUSTOM_SPLIT_SIGN 占位
    assert any(r.reg == CUSTOM_SPLIT_SIGN for r in DEFAULT_STEPS)


def test_rule_dataclass_immutable() -> None:
    rule = Rule(
        reg="abc", max_len=100, split_around=False, forbid_overlap=True, custom=False
    )
    try:
        rule.max_len = 200  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except Exception:
        pass


def test_build_steps_with_custom_reg() -> None:
    rules = build_steps(
        chunk_size=1000, max_size=8000, paragraph_chunk_deep=5, custom_reg=["==="]
    )
    assert len(rules) == len(DEFAULT_STEPS) + 1  # 多 1 条 custom


def test_build_steps_heading_count_scales_with_deep() -> None:
    rules_3 = build_steps(
        chunk_size=1000, max_size=8000, paragraph_chunk_deep=3, custom_reg=[]
    )
    rules_5 = build_steps(
        chunk_size=1000, max_size=8000, paragraph_chunk_deep=5, custom_reg=[]
    )
    assert len(rules_5) > len(rules_3)
    # 5 vs 3 deep → 多 2 条 heading
    assert len(rules_5) - len(rules_3) == 2


def test_rule_custom_flag() -> None:
    rules = build_steps(
        chunk_size=1000, max_size=8000, paragraph_chunk_deep=5, custom_reg=["<SEP>"]
    )
    assert rules[0].custom is True
    assert rules[0].reg == "<SEP>"


def test_rule_forbid_overlap_for_headings() -> None:
    rules = build_steps(
        chunk_size=1000, max_size=8000, paragraph_chunk_deep=5, custom_reg=[]
    )
    # heading steps (index 1-5) should have forbid_overlap=True
    for i in range(1, 6):
        assert rules[i].forbid_overlap is True, f"step {i} should forbid overlap"
