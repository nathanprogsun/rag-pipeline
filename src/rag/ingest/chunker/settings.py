"""ChunkSettings: 17 级分块算法参数。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class ChunkSettings(BaseModel):
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
