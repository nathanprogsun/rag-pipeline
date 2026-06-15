"""MiniMax-M3 tokenizer 加载与 token 计数。

MiniMax-M3 是 1M context 的 BPE 模型 (vocab=200,060), 不能用 tiktoken 的 OpenAI 编码。
本模块封装:
- 首次调用从 HuggingFace 拉 `tokenizer.json` (cache 到 `~/.cache/huggingface/`)
- 模块级单例 + lru_cache, 避免重复加载
- `count_tokens(text, *, tokenizer=None)` 供 filter / pipeline 等模块使用
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
    """加载 MiniMax-M3 tokenizer, 首次调用会从 HuggingFace 下载。

    Returns:
        BPE tokenizer 实例。

    Raises:
        RuntimeError: 下载或加载失败 (网络 / 凭据 / repo 不可用)。
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
    """统计 `text` 中的 token 数, 默认使用 MiniMax-M3 tokenizer。

    Args:
        text: 待统计的文本。
        tokenizer: 已加载的 tokenizer; 为 None 时使用缓存的默认实例。

    Returns:
        token 数量 (默认不包含 BOS / EOS 等特殊 token)。
    """
    tok = tokenizer if tokenizer is not None else load_minimax_m3_tokenizer()
    return len(tok.encode(text).ids)
