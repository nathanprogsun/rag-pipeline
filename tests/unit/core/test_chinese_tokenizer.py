"""ChineseTokenizer 单元测试 — class 形式，覆盖单例与分词行为。"""

from unittest.mock import patch

from rag.infra.pg.chinese_tokenizer import ChineseTokenizer


class TestChineseTokenizer:
    def setup_method(self) -> None:
        ChineseTokenizer._instance = None
        ChineseTokenizer._loaded = False

    def teardown_method(self) -> None:
        ChineseTokenizer._instance = None
        ChineseTokenizer._loaded = False

    def test_singleton_returns_same_instance(self) -> None:
        first = ChineseTokenizer()
        second = ChineseTokenizer()
        assert first is second

    def test_jieba_initialize_called_once(self) -> None:
        with patch("rag.infra.pg.chinese_tokenizer.jieba.initialize") as mock_init:
            a = ChineseTokenizer()
            b = ChineseTokenizer()
            a.tokenize("Python 教程")
            b.tokenize("数据 科学")
        mock_init.assert_called_once()

    def test_tokenize(self) -> None:
        tokens = ChineseTokenizer().tokenize("Python 是一种编程语言,常用于数据科学。")
        assert "Python" in tokens
        assert "编程" in tokens
        assert "数据" in tokens
        assert "语言" in tokens

    def test_build_tsvector(self) -> None:
        assert "Python" in ChineseTokenizer().build_tsvector("Python 教程 入门")
