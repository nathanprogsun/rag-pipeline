"""One-shot: 4 份 FakeEmbeddings 类替换为 ConstantEmbeddings import。

策略: 每文件用 sed 风格替换 (Edit 工具的多行替换), 保留原行为。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path("/Users/jung/pro/rag-pipeline")

# 每个文件一个替换: (file, old_block, new_block)
REPLACEMENTS = [
    # 1) test_pipeline_full.py: _FakeEmbeddings returns [0.0]*1536
    (
        "tests/unit/test_pipeline_full.py",
        '''class _FakeEmbeddings(Embeddings):
    """Minimal embedder for SearchPipelineDeps validation."""

    async def aembed_query(self, text: str) -> list[float]:
        return [0.0] * 1536

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * 1536

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]''',
        '''_FAKE_EMB_VECTOR = [0.0] * 1536
_FakeEmbeddings = ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)''',
    ),
    # 2) test_vector_store.py: FakeEmbeddings returns [0.1]*1536
    (
        "tests/unit/core/test_vector_store.py",
        '''class FakeEmbeddings(Embeddings):
    async def aembed_query(self, text: str) -> list[float]:
        return [0.1] * EMBED_DIM

    def embed_query(self, text: str) -> list[float]:
        return [0.1] * EMBED_DIM

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * EMBED_DIM for _ in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * EMBED_DIM for _ in texts]''',
        '''_FAKE_EMB_VECTOR = [0.1] * EMBED_DIM
FakeEmbeddings = ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)''',
    ),
    # 3) test_subgraph_live.py: FakeEmbeddings returns _unit_vector(0)
    (
        "tests/integration/test_subgraph_live.py",
        '''class FakeEmbeddings(Embeddings):
    """Real embedder interface. Returns unit vector (index 0)."""

    async def aembed_query(self, text: str) -> list[float]:
        return _unit_vector(0)

    def embed_query(self, text: str) -> list[float]:
        return _unit_vector(0)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(0) for _ in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(0) for _ in texts]''',
        '''_FAKE_EMB_VECTOR = _unit_vector(0)
FakeEmbeddings = ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)''',
    ),
    # 4) test_vector_retrieval.py: FakeEmbeddings returns _unit_vector(0)
    (
        "tests/integration/test_vector_retrieval.py",
        '''class FakeEmbeddings(Embeddings):
    async def aembed_query(self, text: str) -> list[float]:
        return _unit_vector(0)

    def embed_query(self, text: str) -> list[float]:
        return _unit_vector(0)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(0) for _ in texts]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_unit_vector(0) for _ in texts]''',
        '''_FAKE_EMB_VECTOR = _unit_vector(0)
FakeEmbeddings = ConstantEmbeddings(vector=_FAKE_EMB_VECTOR)''',
    ),
]


def main() -> None:
    for rel, old, new in REPLACEMENTS:
        p = REPO / rel
        text = p.read_text(encoding="utf-8")
        if old not in text:
            print(f"{rel}: pattern NOT FOUND, skip")
            continue
        text = text.replace(old, new)

        # 加 import (如果还没有)
        if "from tests._fakes import" not in text:
            m = re.search(r"^from tests\.integration\._db_helpers[^\n]*\n", text, re.MULTILINE)
            if m:
                text = (
                    text[: m.end()]
                    + "from tests._fakes import ConstantEmbeddings\n"
                    + text[m.end() :]
                )
            else:
                # fallback: 找 `from __future__` 之后
                m = re.search(
                    r"^(from __future__ import annotations\n)", text, re.MULTILINE
                )
                if m:
                    text = (
                        text[: m.end()]
                        + "\nfrom tests._fakes import ConstantEmbeddings\n"
                        + text[m.end() :]
                    )

        p.write_text(text, encoding="utf-8")
        print(f"{rel}: replaced")


if __name__ == "__main__":
    main()
