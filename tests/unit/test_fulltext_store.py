from rag.infra.pg.fulltext_store import tokenize_chinese


def test_tokenize_chinese() -> None:
    tokens = tokenize_chinese("Python 是一种编程语言,常用于数据科学。")
    assert "Python" in tokens
    assert "编程" in tokens
    assert "数据" in tokens
    assert "语言" in tokens
