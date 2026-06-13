from rag.ingest.chunker.recursive import common_split
from rag.ingest.chunker.rules import build_steps


def test_base_case_returns_combined_under_max() -> None:
    rules = build_steps(
        chunk_size=100, max_size=200, paragraph_chunk_deep=5, custom_reg=[]
    )
    # step 16 已超出, last_text + text < max_size
    result = common_split(
        text="abc",
        step=16,
        last_text="prefix",
        parent_title="",
        rules=rules,
        chunk_size=100,
        max_size=200,
        overlap_len=15,
    )
    assert result == ["prefixabc"]


def test_no_heading_text_uses_newline_split() -> None:
    """无标题纯文本 → 走 \\n\\n 级别, 正常按段切。"""
    rules = build_steps(
        chunk_size=50, max_size=200, paragraph_chunk_deep=5, custom_reg=[]
    )
    text = "段落一。\n\n段落二内容。\n\n段落三内容。"
    result = common_split(
        text=text,
        step=0,
        last_text="",
        parent_title="",
        rules=rules,
        chunk_size=50,
        max_size=200,
        overlap_len=15,
    )
    assert len(result) >= 1
    # 任意一段不应超过 max_size
    for chunk in result:
        assert len(chunk) <= 200


def test_oversized_triggers_recursion() -> None:
    """单段超 chunk_size → 走 step+1 递归下钻。"""
    rules = build_steps(
        chunk_size=20, max_size=100, paragraph_chunk_deep=5, custom_reg=[]
    )
    text = "。" + "x" * 200
    result = common_split(
        text=text,
        step=0,
        last_text="",
        parent_title="",
        rules=rules,
        chunk_size=20,
        max_size=100,
        overlap_len=15,
    )
    assert len(result) >= 2  # 至少切成 2 块


def test_recursion_terminates() -> None:
    """递归必须终止, 不死循环。"""
    rules = build_steps(
        chunk_size=10, max_size=50, paragraph_chunk_deep=5, custom_reg=[]
    )
    text = "内容" * 1000  # 中文 2000 chars
    result = common_split(
        text=text,
        step=0,
        last_text="",
        parent_title="",
        rules=rules,
        chunk_size=10,
        max_size=50,
        overlap_len=15,
    )
    assert len(result) > 0
    for chunk in result:
        assert len(chunk) <= 50


def test_heading_5_level_nested() -> None:
    """5 级 heading 嵌套 → 内容仍被正确切分。"""
    rules = build_steps(
        chunk_size=500, max_size=2000, paragraph_chunk_deep=5, custom_reg=[]
    )
    text = "# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n内容。\n\n## F\n\n更多。"
    result = common_split(
        text=text,
        step=0,
        last_text="",
        parent_title="",
        rules=rules,
        chunk_size=500,
        max_size=2000,
        overlap_len=15,
    )
    assert len(result) >= 1
