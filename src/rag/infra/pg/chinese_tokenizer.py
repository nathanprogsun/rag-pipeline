from __future__ import annotations

import jieba


class ChineseTokenizer:
    """应用层 jieba 分词器, 供 chunk 入库 ``tsvector`` 与检索 query 共用。

    单例: jieba 词典为进程级资源, 全局只保留一个 ``ChineseTokenizer`` 实例,
    ``jieba.initialize()`` 至多执行一次。独立于 ``FulltextRetriever``, 以便
    ingest 路径在尚无 retriever 时也能分词。
    """

    _instance: ChineseTokenizer | None = None
    _loaded: bool = False

    def __new__(cls) -> ChineseTokenizer:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_loaded(self) -> None:
        if not type(self)._loaded:
            jieba.initialize()
            type(self)._loaded = True

    def tokenize(self, text: str) -> list[str]:
        """分词结果, 供 ``tsvector`` 字面量或 ``tsquery`` 的 ``&`` 拼接使用。"""
        self._ensure_loaded()
        return [t for t in jieba.cut_for_search(text) if t.strip()]

    def build_tsvector(self, text: str) -> str:
        """把分词结果转为 ``to_tsvector('simple', ...)`` 所需的空格分隔字面量。"""
        return " ".join(self.tokenize(text))
