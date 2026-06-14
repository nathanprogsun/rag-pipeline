"""MiniMax-M3 tokenizer loader + token counter。

Per design note: MiniMax-M3 是 1M context 的 BPE 模型 (vocab=200,060),
不能用 tiktoken 的 OpenAI 编码。本模块封装:
- 首次调用从 HuggingFace 拉 ``tokenizer.json`` (cache 到 ``~/.cache/huggingface/``)
- 模块级单例 + lru_cache,避免重复加载
- ``count_tokens(text, *, tokenizer=None)`` 供 filter / pipeline 等模块用
"""

from __future__ import annotations

from functools import lru_cache

from tokenizers import Tokenizer

# HuggingFace repo id
MINIMAX_M3_TOKENIZER_ID: str = "MiniMaxAI/MiniMax-M3"

# MiniMax-M3 官方 context window (1M tokens, 512K guaranteed floor)
MINIMAX_M3_CONTEXT_WINDOW: int = 1_000_000


@lru_cache(maxsize=1)
def load_minimax_m3_tokenizer() -> Tokenizer:
    """Load MiniMax-M3 tokenizer. Downloads to ``~/.cache/huggingface/`` on first call.

    Returns:
        tokenizers.Tokenizer: BPE tokenizer instance.

    Raises:
        RuntimeError: if download or load fails (network/credentials/repo).
    """
    try:
        return Tokenizer.from_pretrained(MINIMAX_M3_TOKENIZER_ID)
    except Exception as e:
        msg = (
            f"Failed to load MiniMax-M3 tokenizer from "
            f"HuggingFace repo '{MINIMAX_M3_TOKENIZER_ID}'. "
            f"Check network access and HF_TOKEN. Original error: {e!r}"
        )
        raise RuntimeError(msg) from e


def count_tokens(text: str, *, tokenizer: Tokenizer | None = None) -> int:
    """Count tokens in ``text`` using MiniMax-M3 tokenizer by default.

    Args:
        text: Text to count tokens for.
        tokenizer: Optional pre-loaded tokenizer (for testing or alternative models).
                  If None, loads the default MiniMax-M3 tokenizer (cached).

    Returns:
        Token count (excludes special tokens like BOS/EOS by default).
    """
    tok = tokenizer if tokenizer is not None else load_minimax_m3_tokenizer()
    return len(tok.encode(text).ids)
