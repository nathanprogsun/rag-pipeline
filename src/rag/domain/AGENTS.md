# Domain 层规范（`src/rag/domain/`）

- 仅使用 **Pydantic v2** `BaseModel` — 无 ORM、无 I/O、无 infra import。
- 纯函数用 `Protocol` 表达 duck typing（如 `RerankModelSource`），避免无类型参数或 `Any`。
- 核心类型：`Dataset`、`Chunk`、`ChunkMetadata`、`ScoredDocument`、`SearchRequest`、`SearchResult`。
- 默认值定义在 domain 模型上（如 `vector_weight=0.7`）；空 `prompt_template` 在应用层回退到 `DEFAULT_PROMPT_TEMPLATE`。
- PG ↔ domain 映射放在 domain 之外（后续 service/mapper 层）— domain 文件保持纯净。
