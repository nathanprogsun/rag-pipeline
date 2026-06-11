import uuid
from datetime import datetime

from pydantic import BaseModel


class SearchRequest(BaseModel):
    """用户搜索请求: 必填 3 个 + 默认配置。"""

    query: str
    image_urls: list[str] = []
    dataset_ids: list[uuid.UUID]
    top_k: int = 10
    score_threshold: float | None = None
    use_rerank: bool = True
    rerank_model: str | None = None
    rerank_weight: float = 0.5  # RRF 混合权重, 向量侧与 rerank 侧各占 0.5
    query_extension: bool = True
    max_query_variants: int = 3
    max_tokens: int = 4000
    embedding_model: str | None = None
    temperature: float = 0.1
    query_decomposition: bool = False
    parent_doc_window: int = 0
    use_global_rerank: bool = False
    audit: bool = False
    chat_bg: str = ""  # 多轮对话背景 
    histories: list[dict] = []  # 对话历史 [{"role":"user","content":"..."}]


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
    """Search 接口完整响应。"""

    citations: list[Citation]
    prompt: str
    failed_dataset_ids: list[uuid.UUID] = []
    warnings: list[str] = []


def resolve_rerank_model(req: SearchRequest, dataset) -> str | None:
    """解析 rerank 模型优先级: req.rerank_model > dataset.rerank_model > None。"""
    if not req.use_rerank:
        return None
    return req.rerank_model or getattr(dataset, "rerank_model", None)
