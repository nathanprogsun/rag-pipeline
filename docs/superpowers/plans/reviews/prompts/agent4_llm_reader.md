# Agent #4 — L2/L3 LLM 客户端 + Reader

## 审核范围
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task7.md` — LLM Clients + Semaphore (并发控制)
- `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/tasks/task8.md` — Reader + Document Structure

## 关注点
1. ChatOpenAI(base_url) + get_m3_chat_model() 单实例/工厂模式
2. LLMSemaphore (asyncio.Semaphore) 并发上限(MAX_CONCURRENT_LLM=16)
3. vlm.py 移除后多模态调用是否统一(已知决策:用 ChatOpenAI(M3) 替代)
4. with_structured_output method="function_calling" (已知 H3 修复,MiniMax M3 兼容)
5. Embedding 模型抽象(OpenAIEmbeddings)
6. Rerank 多 provider(Cohere 完整,BGE/Jina stub,已知 M1 标记二期)
7. Reader 文件格式支持(PDF/DOCX/MD/HTML)
8. Document Structure 解析(标题层级、列表、代码块)
9. Reader 异常处理(损坏文件、编码错误)
10. Reader 性能(同步 vs async,大文件)

## 必查项
- task7: LLM 调用重试/退避策略(指数退避?)
- task7: 超时设置(per-call / per-pipeline)
- task7: token 用量统计与限流
- task7: prompt 注入防护(system prompt 隔离)
- task7: OpenAI 兼容协议与 M3 API 差异
- task8: 文件类型检测(magic bytes vs extension)
- task8: OCR/PDF 表格提取(spec 是否提?)
- task8: 编码探测(chardet/charset-normalizer)
- task8: 文档结构 schema 与 chunker (task9) 输入对齐
- task8: 大文件流式读取

## 输出
写入: `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent4_llm_reader.md`
