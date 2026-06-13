from rag.ingest.chunker.utils import simple_text, valid_len


def test_valid_len_strips_whitespace() -> None:
    assert valid_len("hello world") == 10  # 11 chars - 1 space = 10
    assert valid_len("中文 内容") == 4  # 5 chars - 1 space = 4


def test_valid_len_empty() -> None:
    assert valid_len("") == 0
    assert valid_len("   \n\n\t  ") == 0


def test_valid_len_fullwidth_space() -> None:
    """全角空格 U+3000 应被去除。"""
    assert valid_len("中　文") == 2


def test_simple_text_removes_chinese_inner_space() -> None:
    """中文间空格去除。"""
    result = simple_text("中 文")
    assert result == "中文"


def test_simple_text_collapses_3plus_newlines() -> None:
    result = simple_text("a\n\n\n\nb")
    assert result == "a\n\nb"


def test_simple_text_strips_control_chars() -> None:
    result = simple_text("hello\x00world")
    assert result == "hello world"
