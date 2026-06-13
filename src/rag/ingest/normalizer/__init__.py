"""Normalizer 公开 API。

本层职责: LLM 段落改写 (业内 RAG 平台普遍提供显式启用的结构化重整步骤, 用于把无章节文本改写为带 markdown heading 的版本)。其它字段抽取类 (Json/Api/Url) 已下沉为
reader adapter 的一部分, 不再属于 Normalizer。
"""

from .base import Normalizer
from .no_op import NoOpNormalizer
from .structure import (
    ResultDocument,
    StructuredText,
    StructureMode,
    StructureNormalizer,
)
