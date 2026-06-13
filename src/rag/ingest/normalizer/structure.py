"""StructureNormalizer: 用 LLM 把非结构化文本重整为 markdown 章节。

三道闸门设计 (业内常见做法):
  1) mode == FORBID 或 chat_model is None → 跳过
  2) mode == AUTO 且 markdown 标题数 >= 2 → 跳过
  3) 调 LLM 改写,失败降级到 raw_text
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from enum import StrEnum

from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from rag.ingest.normalizer.base import Normalizer
from rag.ingest.types import DocMeta, TextDoc

logger = logging.getLogger(__name__)

# 截断 / 超时常量
MAX_INPUT_CHARS: int = 50_000
_LLM_TIMEOUT_SEC: float = 600.0

# 至少 2 个 markdown 标题才算"已结构化"
_HEADING_RE: re.Pattern[str] = re.compile(r"^#{1,5}\s+\S+", re.MULTILINE)


class StructureMode(StrEnum):
    """三态开关 (FORBID / AUTO / FORCE)。"""

    FORBID = "forbid"
    AUTO = "auto"
    FORCE = "force"


class StructuredText(BaseModel):
    """LLM 结构化输出 schema (with_structured_output method=function_calling)。"""

    result_text: str = Field(..., description="重整后的 Markdown 文本, 保持原文事实")
    summary: str = Field(default="", description="一句话摘要, 可为空")


@dataclass(frozen=True)
class ResultDocument:
    """Normalize 内部结果, 供测试断言 + 未来审计。

    Fields:
        result_text: 规范化后文本 (= raw_text 表示跳过或降级)
        raw_text: 原始输入
        input_tokens / output_tokens: 粗略估计 (text_len // 4)
        skipped: True ⇒ 命中闸门 1 或 2, 未调 LLM
        degraded: True ⇒ 调了 LLM 但失败, 已降级回 raw_text
    """

    result_text: str
    raw_text: str
    input_tokens: int
    output_tokens: int
    skipped: bool
    degraded: bool


_SYSTEM_PROMPT = """你是一名技术文档编辑。把用户提供的非结构化文本重整为层次清晰的 Markdown。

要求:
1. 使用 # / ## / ### 三级标题分章节, 保留原文事实, 不总结不删改。
2. 代码块、列表、表格保留原始语义, 无法识别时保持纯文本。
3. 只输出 Markdown 正文, 不要任何解释或前言。"""


_HUMAN_TEMPLATE = """请把下面的文本重整为 Markdown 章节。

---
【示例 1】

原文:
本指南介绍 RAG 平台的核心能力。平台支持向量检索与全文检索。检索后端使用 PGVector 与 tsvector 双栈。

输出:
# 平台概述

本指南介绍 RAG 平台的核心能力。

## 核心功能

### 检索能力

平台支持向量检索与全文检索, 后端使用 PGVector 与 tsvector 双栈。
---

【示例 2】

原文:
本指南介绍如何安装: 1. 安装 Docker; 2. 拉取镜像; 3. 启动容器。注意端口冲突。

输出:
# 安装指南

本指南介绍安装步骤。

## 步骤

1. 安装 Docker
2. 拉取镜像
3. 启动容器

> 注意: 端口冲突。
---

【待重整文本】
{text}
"""


class StructureNormalizer(Normalizer):
    """基于 LLM 的三道闸门结构化归一化器。"""

    def __init__(
        self,
        *,
        chat_model: Runnable | None = None,
        mode: StructureMode = StructureMode.AUTO,
        max_input_chars: int = MAX_INPUT_CHARS,
        llm_timeout_sec: float = _LLM_TIMEOUT_SEC,
    ) -> None:
        self._chat_model = chat_model
        self._mode = mode
        self._max_input_chars = max_input_chars
        self._llm_timeout_sec = llm_timeout_sec

    # ── 公共 API (async, 沿用 Normalizer 基类签名) ─────────────────────
    async def normalize(self, doc: TextDoc) -> TextDoc:
        filename = doc.meta.filename
        logger.info(
            "structure.start filename=%s mode=%s chars=%d",
            filename,
            self._mode,
            len(doc.text),
        )
        result = await self._run_pipeline(doc.text)
        self._report(result, meta=doc.meta, mode=self._mode)
        return TextDoc(
            text=result.result_text,
            meta=doc.meta,
            format_text=doc.format_text,
            images=list(doc.images),
        )

    # ── 测试入口: 暴露 ResultDocument (内部结果) ──────────────────────
    async def normalize_with_result(self, doc: TextDoc) -> ResultDocument:
        """返回 ResultDocument, 仅供测试断言 skipped / degraded / tokens。"""
        return await self._run_pipeline(doc.text)

    # ── 三道闸门主流程 ────────────────────────────────────────────────
    async def _run_pipeline(self, raw_text: str) -> ResultDocument:
        # 闸门 1: 显式禁用 或 没有 chat_model → 跳过
        if self._mode is StructureMode.FORBID or self._chat_model is None:
            return ResultDocument(
                result_text=raw_text,
                raw_text=raw_text,
                input_tokens=0,
                output_tokens=0,
                skipped=True,
                degraded=False,
            )

        # 闸门 2: AUTO + 已有 markdown 标题结构 → 跳过
        if self._mode is StructureMode.AUTO and self._looks_structured(raw_text):
            logger.info(
                "structure.skip_auto mode=%s reason=existing_headings",
                self._mode,
            )
            return ResultDocument(
                result_text=raw_text,
                raw_text=raw_text,
                input_tokens=0,
                output_tokens=0,
                skipped=True,
                degraded=False,
            )

        # 闸门 3: 调 LLM, 失败降级
        logger.info(
            "structure.llm_call mode=%s chars=%d timeout_sec=%.0f",
            self._mode,
            len(raw_text),
            self._llm_timeout_sec,
        )
        return await self._invoke_llm(raw_text)

    @staticmethod
    def _looks_structured(text: str) -> bool:
        """至少 2 个 markdown 标题才视为已结构化。"""
        return len(_HEADING_RE.findall(text)) > 1

    async def _invoke_llm(self, raw_text: str) -> ResultDocument:
        truncated = self._truncate(raw_text)
        prompt = self._build_prompt(truncated)
        try:
            parsed = await asyncio.wait_for(
                self._chat_model.ainvoke(prompt),  # type: ignore[union-attr]
                timeout=self._llm_timeout_sec,
            )
            return ResultDocument(
                result_text=parsed.result_text,
                raw_text=raw_text,
                input_tokens=len(truncated) // 4,
                output_tokens=len(parsed.result_text) // 4,
                skipped=False,
                degraded=False,
            )
        except Exception as exc:  # noqa: BLE001 — 降级路径吞所有异常
            logger.warning("StructureNormalizer LLM failed, degrading: %s", exc)
            return ResultDocument(
                result_text=raw_text,
                raw_text=raw_text,
                input_tokens=0,
                output_tokens=0,
                skipped=False,
                degraded=True,
            )

    def _truncate(self, text: str) -> str:
        if len(text) <= self._max_input_chars:
            return text
        logger.info(
            "StructureNormalizer truncating input: %d -> %d chars",
            len(text),
            self._max_input_chars,
        )
        return text[: self._max_input_chars]

    def _build_prompt(self, text: str) -> list[tuple[str, str]]:
        human = _HUMAN_TEMPLATE.format(text=text)
        return [("system", _SYSTEM_PROMPT), ("human", human)]

    @staticmethod
    def _report(
        result: ResultDocument,
        *,
        meta: DocMeta | None = None,
        mode: StructureMode | None = None,
    ) -> None:
        filename = meta.filename if meta else None
        mode_label = mode or "unknown"
        if result.degraded:
            logger.warning(
                "structure.degraded filename=%s mode=%s fallback=raw_text",
                filename,
                mode_label,
            )
        elif result.skipped:
            logger.info(
                "structure.skipped filename=%s mode=%s",
                filename,
                mode_label,
            )
        else:
            logger.info(
                "structure.applied filename=%s mode=%s in=%d out=%d",
                filename,
                mode_label,
                result.input_tokens,
                result.output_tokens,
            )
