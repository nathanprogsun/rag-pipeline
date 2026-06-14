# Agent #1 — L0/L1 基础脚手架 + Domain 层 (Foundation & Domain)

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task1.md` — 项目脚手架 + Docker Compose + 验证
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task2.md` — Domain Models (Pydantic v2)

## 关注点
1. 脚手架质量(pyproject.toml 依赖锁定、Python 版本、Makefile 完备性、Docker Compose 端口冲突)
2. 域模型完整性(SearchRequest 16 字段、Chunk/ScoredDocument/Dataset/Citation/SearchResult 与 spec §2 对齐)
3. Pydantic v2 用法(model_config、discriminated_union、validator、field_validator)
4. 异常体系(exceptions.py 自定义异常与 spec §8 对齐)
5. .env.example 与 config.py (pydantic-settings) 一致性
6. Library / CLI 双模式边界(env_file 是否硬编码,已知 H2 修复)

## 必查项
- pyproject 依赖版本范围是否合理(LangChain 0.3.x、pgvector、SQLAlchemy 2.0)
- Pydantic v2 与 v1 API 混用风险
- DEFAULT_PROMPT_TEMPLATE 与 spec §7 字符级对齐
- 16 字段的 SearchRequest 字段命名在 plan/task/spec 中是否一致
- SearchResult.failed_dataset_ids、warnings 与 orchestrator.py 计划中的引用
- LLMSettings 在 config.py 中的单一定义(已知 H5 修复)
- ScoredDocument.image_path 字段在 cite.py 是否一致(已知 H2 修复)
- query_decomposition / parent_doc_window 字段在 build_full_pipeline 引用(已知 H3 修复)

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent1_foundation_domain.md`
