import uuid
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, PrivateAttr

from rag.domain.document import ScoredDocument


class RetrievalConfig(BaseModel):
    """检索侧配置: embedding / rerank / top_k。"""

    model_config = ConfigDict(frozen=True)

    top_k: int = 10
    score_threshold: float | None = None
    embedding_model: str | None = None
    use_rerank: bool = True
    rerank_model: str | None = None
    rerank_weight: float = 0.5  # RRF 混合权重, 向量侧与 rerank 侧各占 0.5


class GenerationConfig(BaseModel):
    """生成侧配置: 留给 LLM 阶段读取 (prompt/温度/token 上限)。"""

    model_config = ConfigDict(frozen=True)

    model: str = ""
    temperature: float = 0.1
    max_tokens: int = 4000


class ContextConfig(BaseModel):
    """上下文/查询改写配置: parent doc 扩展窗口 / query 扩展 / 子查询分解。"""

    model_config = ConfigDict(frozen=True)

    parent_doc_window: int = 0
    query_extension: bool = True
    max_query_variants: int = 3
    query_decomposition: bool = False


class HistoryConfig(BaseModel):
    """对话上下文: 多轮聊天背景。"""

    model_config = ConfigDict(frozen=True)

    chat_bg: str = ""  # 多轮对话背景
    histories: list[dict[str, str]] = []  # 对话历史 [{"role":"user","content":"..."}]


class SearchRequest(BaseModel):
    """用户搜索请求: 必填 query + 4 个子 config + 顶层标志位。

    子 config 按职责拆分, 顶层只留 query / dataset_ids / image_urls / audit / use_global_rerank。
    """

    model_config = ConfigDict(frozen=True)

    query: str
    dataset_ids: list[uuid.UUID]
    image_urls: list[str] = []
    use_global_rerank: bool = False
    audit: bool = False

    retrieval: RetrievalConfig = RetrievalConfig()
    generation: GenerationConfig = GenerationConfig()
    context: ContextConfig = ContextConfig()
    history: HistoryConfig = HistoryConfig()


class Citation(BaseModel):
    """返回给前端的引用条目 DTO。"""

    chunk_id: uuid.UUID
    dataset_id: uuid.UUID
    source_name: str
    content: str
    image_path: str | None = None
    score: float
    update_time: datetime | None = None


class SearchResult(BaseModel):
    """Search 接口完整响应。

    Per `.agents/design/2026-06-14-cross-task-contracts.md` Contract 4-6:
    - ``response``: LLM 的 *生成* 答案 (不是 prompt), RAGAS faithfulness 读这个。
                  可包含 ``[id](CITE)`` 内联引用标记 (见 Contract 5)。
    - ``citations``: 与 ``response`` 中 ``[id]`` 一一对应的引用列表,1-based index。
    - ``citation_format``: 渲染方式选择, ``"inline"`` (FastGPT 风格) 或 ``"prefix"`` (旧版块状)。
    - ``_intermediate_hits``: 召回 + 融合 + rerank 中间产物, 给 audit_tap / EvalRunner 用,
                              ``exclude=True`` 不出现在 ``model_dump_json()`` 输出里 (见 Contract 6)。
    """

    model_config = ConfigDict(frozen=True)

    response: str
    citations: list[Citation]
    citation_format: Literal["inline", "prefix"] = "inline"
    failed_dataset_ids: list[uuid.UUID] = []
    warnings: list[str] = []
    # PrivateAttr (Pydantic v2 idiomatic private state) — excluded from
    # .model_dump() / .model_dump_json() output. 详见 Contract 6。
    _intermediate_hits: list[ScoredDocument] = PrivateAttr(default_factory=list)


class RerankModelSource(Protocol):
    rerank_model: str | None


def resolve_rerank_model(req: SearchRequest, dataset: RerankModelSource) -> str | None:
    """解析 rerank 模型优先级: req.retrieval.rerank_model > dataset.rerank_model > None。"""
    if not req.retrieval.use_rerank:
        return None
    return req.retrieval.rerank_model or dataset.rerank_model
