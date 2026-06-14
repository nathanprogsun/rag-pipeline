# 共同上下文(必读)

你是一位资深的系统架构设计师(高 bar、严格、客观),正对 `/Users/jung/pro/rag-pipeline` 仓库的 RAG 流水线实施 plan 进行 **架构层面** 的只读 review。

## 项目目标
使用 LangChain (LCEL) + PostgreSQL+pgvector + Redis 多级缓存,复刻 FastGPT 查询侧 RAG 流水线。Python 3.12, Pydantic v2, async 全栈。详见 spec。

## 关键参考
- 主 plan: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (212 行, 17 章节的索引+自检)
- 设计 spec: `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` (1661 行, 17 章节, 详细设计)
- Task 索引: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/INDEX.md` (20 task 拆分索引)
- Task 文件目录: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task{1..20}.md`
- **缺失**: task15.md, task16.md 未生成

## 你的角色与产出
你处于 **架构设计师** 视角,关注:
1. 模块边界(分层 domain / infra / pipeline / retrieval / ingest / cli)
2. 依赖方向(严禁反向依赖,严禁循环)
3. API 契约一致性(SearchRequest/SearchResult/Chunk/ScoredDocument 等)
4. Spec 章节覆盖度
5. TDD 合规(stub → test → impl → verify)
6. 可观测性、日志、错误处理、降级
7. 性能/扩展性、安全、可移植性
8. 跨 task 契约冲突

## 行为约束
- **只读**: 不要修改任何 task 文件或源码
- **证据**: 所有发现必须给出 `file:line` 引用
- **不奉承**: 禁止"看起来不错"型无证据评价
- **不夸大**: 给具体影响,不要"严重问题"型空话
- 若发现 task 间冲突,明确标出

## 输出格式
将完整 review 写入指定文件路径,**严格遵循以下结构**(中英文皆可,技术文档风格):

```
# Architecture Review: [Agent #N 域名称]

## 1. 一句话总评
[≤ 100 字,直接给结论,点出最关键问题]

## 2. 模块边界 / 依赖方向 / 契约一致性
| 检查项 | 结论 | 证据(file:line) | 评级(OK/⚠/🔴) |

## 3. 发现清单(按严重度降序)
### 🔴 P0 — 必须修复(阻塞)
- **[taskX.Y]** 标题
  - 位置: `path:line`
  - 问题: ...
  - 影响: ...
  - 建议: ...
[继续 P1/P2/P3]

## 4. Spec 覆盖矩阵
| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |

## 5. 架构风险与建议
- 风险 N: ...
  缓解: ...

## 6. 跨 Task 一致性核查
[此 agent 范围内或与外部 task 冲突的契约问题]

## 7. 3 条具体建议
1. ...
2. ...
3. ...
```


---

# Agent #2 — L1/L2 PG 基础设施 + 向量检索

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task3.md` — PG database.py + base.py + Models + Repositories
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task4.md` — Vector Retriever (HNSW cosine)

## 关注点
1. SQLAlchemy 2.0 async + Repository 模式的可行性
2. AsyncSessionLocal 即用即弃模式与 FastAPI 最佳实践对齐
3. DeclarativeBase + TimestampMixin 复用
4. chunk_repo.py 接口(CRUD + 检索 + 批量)是否与 task4/task5 检索需求对齐
5. pgvector HNSW 索引参数(m=16, ef_construction=64 与 spec 对齐)
6. cosine distance 操作符选择(`<=>` vs `<->`)
7. ef_search 动态设置(已知 H7 修复:`SET LOCAL hnsw.ef_search`)
8. top_k > 40 时的静默截断问题
9. schema.sql 字符级与 Pydantic 模型对齐
10. Alembic 策略(create_all vs migration),M1 标记的产线方案

## 必查项
- task3 是否定义完整索引? HNSW + GIN 全文 + B-tree 关联?
- task4 query embedding 调用是否经过 LLMSemaphore?
- to_tsquery 注入风险(已知 M4 修复:`'simple'`)
- chunk_repo.find_by_embedding 是否批量化
- 缺少的审计/日志字段(created_at, updated_at)
- Spec §4 PG schema 与 task3 schema.sql 一致性
- dataset_id / chunk_id 索引策略

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent2_pg_vector.md`


---

## 启动说明
1. 仔细阅读以上 共同上下文 + 你的 agent 任务说明 + 必读参考
2. 阅读你范围内的 task 文件(`tasks/taskN.md`) 全文
3. 翻阅 spec `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` 对应章节
4. 按"输出格式"结构组织 review
5. 将完整 review **写入文件**: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent2_pg_vector.md`
6. 在终端输出简短摘要(≤ 500 字,给出 3 条最关键发现 + 1 句总评)

## 重要提醒
- 你是 reviewer,不是 implementer
- 不要修改任何 task 文件或源码
- 所有结论必须有 `file:line` 证据
- 跨 task 冲突优先在"跨 Task 一致性核查"列出
- 若发现 task15/16 缺失影响你审查的 task,在 review 顶部明确说明
