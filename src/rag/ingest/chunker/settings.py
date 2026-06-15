"""ChunkSettings: Chunker 算法的可调参数 (Pydantic frozen 模型)。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class ChunkSettings(BaseModel):
    """Chunker 算法参数 (不可变)。

    Args:
        chunk_size: 目标 chunk 长度。
        max_chunk_size: 硬上限 (例如代码块按 ``chunk_size * 4`` 放宽)。
        overlap_ratio: 相邻 chunk 重叠比例, 自动钳制到 ``[0, 0.5]``。
        paragraph_chunk_deep: 标题级深度上限, 默认 5。
        paragraph_chunk_min_size: 触发段落级切分的最小内容长度。
        min_chunk_size: chunk 下限, 必须为正整数。
        custom_separator: 用户自定义分隔符, None 表示不启用。
    """

    model_config = ConfigDict(frozen=True)

    chunk_size: int = 1000
    max_chunk_size: int = 8000
    overlap_ratio: float = 0.10
    paragraph_chunk_deep: int = 5
    paragraph_chunk_min_size: int = 200
    min_chunk_size: int = 256
    custom_separator: str | None = None

    @field_validator("overlap_ratio")
    @classmethod
    def _clamp_overlap(cls, v: float) -> float:
        return max(0.0, min(0.5, v))

    @field_validator("chunk_size", "max_chunk_size", "min_chunk_size")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("must be positive")
        return v
