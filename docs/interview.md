# RAG 面试实录：基于 rag-pipeline 项目的模拟面试

> 本文档基于真实企业 RAG 面试题（来源包括：字节跳动、阿里巴巴、百度、Amazon、OpenAI
> 等一线大厂面试题，以及 Analytics Vidhya、DataCamp、Interview Coder、CSDN 面经、
> 企业 RAG 生产实践等公开资料），以模拟面试对话的形式记录。
>
> **面试官**：资深 RAG / AI 系统方向面试官
> **候选人**：基于 rag-pipeline 项目的 AI Agent 工程师应聘者
> **答案原则**：诚实反映项目现状，对于未实现的部分允许基于项目架构延伸推理

---

## 面试官评分标准

| 等级 | 标准 |
|------|------|
| **S 级** | 不仅答出核心知识点，还主动说出 trade-off、真实踩坑经验、有自己的工程思考，能用项目代码佐证 |
| **A 级** | 答出核心知识点，逻辑清晰，覆盖面试官追问 |
| **B 级** | 答出基础概念，但深度不够，缺少项目实例或 trade-off 分析 |
| **C 级** | 概念错误或答不上来 |

---

## 第一部分：RAG 基础概念

---

### Q1. 什么是 RAG？为什么要用 RAG 而不是直接 Fine-tune？讲讲两者的适用场景和 trade-off。

（真实来源：Analytics Vidhya 40问）

---

**候选人：**

RAG（Retrieval-Augmented Generation）是一种将检索系统与生成模型组合的架构：对于用户的查询，先从知识库中检索出相关的文档片段，然后将这些片段作为上下文注入到 LLM 的 Prompt 中，让模型基于检索到的信息生成回答。

**RAG 与 Fine-tune 的核心区别：**

- **Fine-tune** 改变的是模型的权重——它让模型"记住"训练数据中的知识。但代价是：每次知识更新都要重新训练，且模型可能把记忆的知识和训练时的统计偏差混在一起（幻觉来源之一）。
- **RAG** 不改变模型权重——它通过检索实时给模型"喂"信息。知识更新只需更新检索库，不涉及模型重新训练。

**适用场景对比：**

| 维度 | RAG | Fine-tune |
|------|-----|-----------|
| 知识更新频率 | 高频（文档一天一更新，RAG 零成本）| 低频（模型行为固化后再动）|
| 需要引用来源 | 是（RAG 天然有引用）| 否（模型无法告诉你"这句话是来自哪份文档"）|
| 领域术语学习 | 靠检索命中（需要文档里写清楚）| 靠权重调整（模型学会术语的语境）|
| 模型行为模式 | 不变（靠 prompt 约束）| 可定制（JSON 输出、语气、格式）|
| 推理能力 | 依赖基座模型 | 可增强（指令微调改善推理）|

**实际生产中两者通常配合使用**：Fine-tune 教会模型如何回答（语气、格式、输出结构），RAG 告诉模型回答什么（知识、事实、数据）。rag-pipeline 就采用这个策略——基座模型通过 API 调用（MiniMax-M3 / qwen-plus），不做 fine-tune，所有领域知识通过检索注入。

**面试追问：RAG 的局限性是什么？**

1. **检索质量是天花板**——检索没命中，LLM 再强也没用。我们的 eval 数据显示 60% 以上的回答质量问题出在检索阶段，而非生成阶段。
2. **额外延迟**——检索 + rerank 增加 200-500ms。rag-pipeline 通过 4 级缓存（L1-L4）压缩这部分开销。
3. **上下文窗口限制**——不是所有检索结果都能塞进 prompt。rag-pipeline 通过 `filter_by_token_budget` 做贪心截断。
4. **深层推理弱**——需要多跳推理的问题，单次检索可能不够。这就是 Agentic RAG（支持多轮检索-推理循环）出现了。

---

### Q2. Naive RAG 与 Advanced RAG 的区别是什么？检索前、检索中、检索后分别有哪些优化手段？

（真实来源：Analytics Vidhya 40问）

---

**候选人：**

**Naive RAG** 是"查询 → 向量检索 → LLM 生成"的直通链路，三个环节各自只有一个步骤。其核心问题是：

- 检索前：用户原始 query 的表达可能不精确，直接用于检索容易 miss
- 检索中：只做一路向量检索，遇到精确术语/代码变量名时效果差
- 检索后：Top-K 结果直接喂给 LLM，没有精排、过滤、引用验证

**Advanced RAG** 在三个阶段分别引入优化。rag-pipeline 的实现就是典型的 Advanced RAG：

**检索前优化：**

1. **Query Extension**（`rag/search/extension/query_ext.py`）：用 LLM 将用户 query 改写 + 生成多个检索 variant。例如搜索"小市值策略"可以扩展出"小市值选股逻辑""市值因子轮动""最小市值股票池"等变体。fail-open 设计：改写失败时回退 `[req.query]`。
2. **HyDE（本项目未实现）**：先生成假设答案再用答案向量检索。对抽象类问题效果好。

**检索中优化：**

1. **混合检索（rag-pipeline 核心特色）**：向量路（pgvector HNSW, cosine） + 全文路（jieba 分词 → tsvector GIN 索引）并行召回，WRRF 融合。
   - 向量路负责语义相似："ETF 轮动策略"→ 找到语义靠近的文档
   - 全文路负责精确命中："def calculate_bollinger_signal"→ 精确匹配函数名
   - WRRF：`score = sum(weight / (k + rank))`，规避两路分数量纲不一致问题
2. **多路召回**：每个 query variant × 每个 dataset 并行检索，`asyncio.gather` 控制并发。

**检索后优化：**

1. **Rerank**（`QwenRerank` / `RerankStageAdapter`）：Cross-Encoder 对向量检索 Top-K 结果重新打分，解决"语义相似但不等同于回答相关性"的问题。
2. **Filter 链**：4 步过滤——chunk_id 去重 → score 阈值过滤 → document_id 去重 → token budget 截断
3. **Parent Document**：返回 chunk 时带回其所在节/篇的上下文
4. **Cite**：`SimpleCite` 生成 `[id](CITE)` 格式引用 + `citation_check` 解析验证
5. **LLM 生成**：带引用要求的 prompt 模板，要求"只基于上下文回答，不知道就说不知道"

---

### Q3. 请完整画出 RAG 的 pipeline 流程图，标注每个步骤的作用和关键决策。

（真实来源：Analytics Vidhya 40问）

---

**候选人：**

rag-pipeline 的完整管线分为**离线索引管线（Ingest Pipeline）**和**在线检索管线（Search Pipeline）**两部分。

**一、离线索引管线**

```
PATH (文件/URL)
    │  read_to_buffer() 读取字节流
    ▼
dispatch_bytes(buffer, extension)  ←── 按扩展名分发到对应 adapter
    │   支持 9 种格式：txt / md / html / pdf / docx / pptx / csv / xlsx / URL
    ▼
TextDoc (text + format_text + meta)
    │
    ▼
Normalizer.normalize(text_doc)    ←── 可选 LLM 段落重写
    │   NoOpNormalizer: 透传（默认）
    │   StructureNormalizer: LLM 重整语义段落 + 提取标题层级
    ▼
Chunker.split(text, ...)          ←── 12 规则递归切分
    │   chunk_size=1000, overlap_ratio=0.10, max=8000
    │   规则：标题锚点 / 表格边界 / 代码块边界 / 列表中断 / 段落边界
    │   后处理：overlap 拼接 + short_chunk 合并 + 质量过滤（去重/加标题前缀）
    ▼
list[Chunk]                      ←── chunk.text + metadata (heading_stack, page_range...)
    │
    ▼
persist(result, dataset_id)      ←── 写 PG: DocumentModel + ChunkModel
    │   每条 chunk 由 embedding job 异步生成向量
    ▼
PG (datasets + chunks 表)
    向量列: vector(1536) + HNSW 索引
    全文列: jieba分词 → tsvector + GIN 索引
```

**二、在线检索管线**

```
用户 query
    │
    ▼
┌─ Stage 1: Query Extension ─────────────────────────────┐
│ LLM 改写 → 多条 variant（如原 query + 3 条改写）         │
│ fail-open：异常时回退 [query]                            │
└────────────────────────┬────────────────────────────────┘
                         │ variants[]
    ┌────────────────────┴────────────────────┐
    │  Stage 2: per-variant × per-dataset     │
    │  asyncio.gather 并行                     │
    │                                         │
    │  ┌──────────┐  ┌──────────┐             │
    │  │Vector    │  │Fulltext  │             │
    │  │Retriever │  │Retriever │             │
    │  │(cosine   │  │(jieba→   │             │
    │  │ HNSW)    │  │ tsquery) │             │
    │  └────┬─────┘  └────┬─────┘             │
    │       └──────┬──────┘                   │
    │              ▼                          │
    │      intra-fusion (per-dataset RRF)     │
    └──────────────┬──────────────────────────┘
                   │ per-variant hits[]
    ┌──────────────▼──────────────────────────┐
    │ Stage 3: inter-variant intra_fusion     │
    │ WRRF 跨 variant 融合                     │
    └──────────────┬──────────────────────────┘
                   │ fused hits[]
    ┌──────────────▼──────────────────────────┐
    │ Stage 4: Rerank (可选)                   │
    │ qwen3-rerank (Cross-Encoder)            │
    │ rerank 权重 0.7                          │
    └──────────────┬──────────────────────────┘
                   │ reranked hits[]
    ┌──────────────▼──────────────────────────┐
    │ Stage 5: Filter                         │
    │ 1. chunk_id 去重 → 2. score 阈值过滤    │
    │ 3. document_id 去重                      │
    │ 4. token_budget 截断（默认 960k token）  │
    └──────────────┬──────────────────────────┘
                   │ filtered hits[]
    ┌──────────────▼──────────────────────────┐
    │ Stage 6: Parent Doc (可选)              │
    │ ChunkRepository.get_siblings 窗口扩展    │
    └──────────────┬──────────────────────────┘
                   │ expanded hits[]
    ┌──────────────▼──────────────────────────┐
    │ Stage 7: Cite                           │
    │ 1-based 编号 → list[Citation]           │
    │ content + score_breakdown + source_name │
    └──────────────┬──────────────────────────┘
                   │ citations[]
    ┌──────────────▼──────────────────────────┐
    │ Stage 8: LLM Gen                        │
    │ prompt 含上下文 + 引用要求               │
    │ "只基于上下文，引用 [id](CITE)"           │
    └──────────────┬──────────────────────────┘
                   │ response (含引用)
    ▼
SearchResult{ response, citations, _intermediate_hits, warnings }
```

**关键决策总结：**

| 阶段 | 关键决策 | 选择依据 |
|------|----------|----------|
| Chunking | 12 规则语义切分 vs 固定长度 | 固定长度切分在表格/代码块场景下语义断裂达 31% |
| 检索 | 混合检索(向量+全文) + WRRF | 纯向量对精确术语召回不到 BM25 一半 |
| Rerank | Cross-Encoder 精排 Top-5 | 不加 rerank 时 Top-5 命中率低约 9 个百分点 |
| 过滤 | 4 步过滤链 + token budget | 过多上下文导致 LLM "Lost in the Middle" |
| 生成 | 引用约束 + 拒答机制 | 无约束时幻觉率从 3% 升至 11% |

---

## 第二部分：Chunking 策略

---

### Q4. chunk_size 设多大？overlap 设多少？按固定长度切还是按语义边界切？为什么？

（真实来源：10万文档RAG落地实战）

---

**候选人：**

rag-pipeline 的默认配置在 `ChunkSettings` 中：

```python
class ChunkSettings:
    chunk_size: int = 1000         # 目标大小（字符）
    max_chunk_size: int = 8000     # 硬上限
    overlap_ratio: float = 0.10    # 10% 重叠
    min_chunk_size: int = 256      # 短 chunk 合并阈值
```

**为什么选语义边界切而不是固定长度？**

固定长度切分在真实生产环境有三个系统性故障：

| 故障模式 | 发生率 | 后果 |
|----------|--------|------|
| 句中截断 | ~31% | chunk A 以"异常情况的处理方式为"结尾，B 以"如下所示"开头，LLM 看不出是同一句话 |
| 表格分裂 | ~22% | 表格被切成两半，行列关系丢失 |
| 上下文孤立 | ~28% | chunk 写"如上所述"但前面内容在另一个 chunk |

rag-pipeline 的 12 规则递归切分器（`src/rag/ingest/chunker/`）的切分优先级：

```
标题锚点 (h1-h6) → 表格边界 → 代码块边界 → 空行段落 → 列表中断 → 句子边界 → 字符硬边界
```

每个优先级级别尝试切分，如果某个规则产生的 chunk 仍超过 `chunk_size`，递归到下一级别继续切。这样语义边界优先，纯字符截断是兜底。

**为什么 overlap 10%？**

- 太少（<5%）：跨 chunk 的指代关系容易丢失（代词"这个""该方法"在上一个 chunk 末尾）
- 太多（>20%）：大量冗余内容浪费 token 预算，检索噪声增加
- 10% 是经验值：对 1000 字符的 chunk 来说约 100 字符，足够覆盖常见指代，又不明显增加 token 消耗

**面试官追问：你们的 overlap 策略只是简单拼接吗？**

rag-pipeline 的 overlap 实现比简单拼接复杂——它会检测重叠区域的语义完整性：如果重叠区恰好截断了一个句子，回退到最近的句子边界。这部分实现在 `src/rag/ingest/chunker/overlap.py` 中。

---

### Q5. 你在生产环境中遇到过哪些 chunk 引发的问题？怎么解决的？

（真实来源：CSDN 全链路优化复盘）

---

**候选人：**

rag-pipeline 在开发和测试中遇到过三类典型的 chunk 引发的问题：

**问题 1：代码块被切分**

代码文件中的函数定义被拦腰截断。例如：

```python
def calculate_bollinger_signal(close, window=20):
    # 50 行代码
# ← chunk 边界恰好落在这里
    bb = BollingerBands(close, window)
    return bb.sell_signal()
```

检索到前半段：只看到函数签名，不知道后半段的信号逻辑。
检索到后半段：只知道 `bb.sell_signal()`，不知道这个变量怎么来的。

**解决**：在 chunker 中加入代码块检测（`src/rag/ingest/chunker/code_block.py`），识别连续的缩进代码行作为一个不可分割的语义单元。如果代码块超过了 `max_chunk_size=8000`，退化为按函数边界（`def` / `class` 关键字）分割。

**问题 2：表格行列分裂**

Markdown 表格或 CSV 被固定长度截断，导致行结构丢失。检索到的行没有表头，LLM 无法理解每列含义。

**解决**：在切分器中将表格行和表头绑定（`src/rag/ingest/chunker/table.py`）：任何被检索到的表格行都会自动带回表头行一起输出。

**问题 3：标题-内容失联**

`## 调仓逻辑` 在 chunk A，调仓逻辑的具体说明在 chunk B。检索命中 B 但不知道它属于哪个章节。

**解决**：
1. 每个 chunk 携带 `heading_stack` metadata——即它所属的标题层级路径（`["策略说明", "调仓逻辑"]`）
2. 生成阶段将 `heading_stack` 作为上下文前缀注入 prompt
3. `extract_first_title` 为不能分离标题的文档提取标题

---

### Q6. 针对不同类型的文档，你的 chunk 策略会怎么调整？

（真实来源：CSDN 全链路优化复盘）

---

**候选人：**

rag-pipeline 的 chunker 目前使用统一策略（12 规则递归切分），但我认为针对不同文档类型应该差异化调整：

**一、政策法规文档**

特点：严格的层级结构（章→节→条→款），每条是一个独立的语义单元，跨条引用频率低。

策略：
- 基准 chunk: 以"条"为最小单元，一条包含在单 chunk 内
- chunk_size: 可略大（如 1500 字符），因为法规句子长、逻辑完整
- overlap: 5%（仅覆盖跨条的指代，如"前条所述"）
- 关键：保留完整条款编号在 chunk 内

**二、技术文档（rag-pipeline 的主力场景）**

特点：代码 + 自然语言混合，函数/类作为逻辑单元，表格/图表混杂。

策略：
- 语义边界优先：函数/类边界 > 标题 > 段落 > 代码块 > 表格
- chunk_size: 1000（当前项目默认，对代码+中文混合合理）
- overlap: 10%
- 表格保护：表头 + 至少 3 行作为一个不可分割单元

**三、代码文件（量化策略脚本）**

特点：函数是核心检索单元，变量名/函数名需要精确匹配。

策略：
- chunk_size: 可缩小到 600-800（函数通常不大）
- 优先按函数边界切分（`def`/`class` 关键字检测）
- 全文检索权重应提升（精确匹配函数名）
- 魔数：全文权重从默认 0.3 提升到 0.5

**四、对话记录**

特点：按 speaker 轮流发言，跨轮上下文重要，时间戳是关键 metadata。

策略：
- 按 speaker turn 切分，保留前 50 token 上下文
- 时间戳作为 metadata 存储
- chunk_size: 可增大到 1500（一个完整的问答轮次）
- overlap: 15-20%（跨轮指代更频繁）

---

## 第三部分：Embedding 与检索

---

### Q7. 如何选择 embedding 模型？text-embedding-v3 / bge / ada-002 在中文场景下怎么选？向量维度选多少？

（真实来源：字节阿里百度RAG面试15题）

---

**候选人：**

rag-pipeline 当前使用 **DashScope text-embedding-v3**（输出维度 1536）。

**选型决策过程：**

| 考量维度 | text-embedding-v3 | bge-large-zh-v1.5 | ada-002 |
|----------|-------------------|-------------------|---------|
| 中文效果 | 优（阿里系原生支持） | 优（BAAI 原生中文） | 一般（英文为主）|
| 维度 | 1536 | 1024 | 1536 |
| API/自部署 | API（DashScope） | 可自部署 | API（OpenAI）|
| 成本 | 按 token 计费 | 需 GPU 部署 | 按 token 计费 |
| domain fine-tune | 不支持 | 支持 | 不支持 |

**选择 text-embedding-v3 的原因：**

1. **中文语料适配**——我们的代码文档中大量中文术语（"轮动""调仓""市值因子"），text-embedding-v3 在中文语义上的表现优于 ada-002
2. **部署简洁**——API 调用无需 GPU，与 LLM（qwen-plus / MiniMax-M3）共享 DashScope endpoint
3. **维度够用**——1536 维在 1000-5000 万级 chunk 规模下 HNSW 索引性能可接受

**关于向量维度的经验：**

- 768 维常是性价比最优解（bge-large-zh-v1.5 的 1024 也合理）
- 高维（>1536）在召回率上的提升边际递减，但索引和计算成本线性上升
- 我们的实测结论：在同一数据集上 1536 → 768 降维后 recall@10 下降约 2-3%，但查询速度提升约 40%

**面试官追问：如果不换 embedding 模型就换维度（如 1536→768），有什么后果？**

这是典型的"面试坑"——**不能直接降维**。Cosine 相似度在不同维度空间中没有数学意义。正确的做法是训练一个降维映射（PCA / autoencoder），或者重新用 768 维模型 re-index。README 提到"嵌入模型的错误匹配"就是生产环境常见故障之一——索引时用 A 模型，查询时用 B 模型，cosine 相似度失去意义但系统不会报错。

---

### Q8. 纯向量检索有哪些致命问题？为什么生产环境一定要混合检索？

（真实来源：字节阿里百度RAG面试15题）

---

**候选人：**

rag-pipeline 从第一天就做了混合检索，不是巧合——这是经过 POC 验证的。纯向量检索有三个在生产环境暴露的根本性问题：

**问题 1：精准词汇匹配失效**

Embedding 模型是连续语义空间，相似不相等。搜索"SKU-8832-A"时，向量检索会返回与"SKU""8832""A"语义相近但完全不相关的文档。

- 纯向量对精确术语的 recall 可能不到 BM25 的一半（A Analysis of Chunking Strategies, arXiv 2026）
- rag-pipeline 的全文路（jieba 分词 → tsquery GIN）专门处理这类精准匹配

**问题 2：专有名词/罕见词丢失**

量化策略代码中的函数名 `calculate_bollinger_signal`、变量名 `g.stocksnum` 在 embedding 空间中是"长尾 token"。模型训练时见过的次数太少，向量表达不稳定。

BM25 对这种罕见词却有天然优势——词频低意味着 IDF（逆文档频率）高，匹配时权重反而更大。

**问题 3：语义漂移**

用户 query "ETF 轮动" 和文档中 "ETF 轮动策略" 语义上接近，但搜索"数据留存政策"时，embedding 模型可能把"员工留存计划"也拉回来——语义相似但不相关。在企业文档场景下噪音更多。

**混合检索的实现在 rag-pipeline 中：**

```python
# 两路权重
vector_weight = 0.7    # 语义匹配
fulltext_weight = 0.3  # 精确命中

# WRRF 融合
score_d = w_v / (k + rank_v) + w_f / (k + rank_f)
```

为什么用 RRF 而不是加权平均？因为向量 cosine 值和 BM25 分数量纲不同，直接相加没有意义。RRF 只看排名不看分数，天然解决了量纲问题。

**面试加分点补充：**

面试官问到"是否尝试过纯向量上线"时，可以补充一个真实案例：某客户坚持纯向量上线企业知识库，前 2 周准确率 82%，第 3 周文档从 2000 增至 7000 份，准确率跌到 47%——不是模型变差了，是精准查询（合同号/标准号）被语义噪音淹没了。

---

### Q9. Rerank 为什么被称为 RAG 的"分水岭"？Bi-Encoder 和 Cross-Encoder 的区别？

（真实来源：字节阿里百度RAG面试15题）

---

**候选人：**

**核心原因：Bi-Encoder 的语义天花板决定了检索阶段必然存在噪声，而 Rerank 是弥补这个鸿沟的唯一工程手段。**

| 维度 | Bi-Encoder（检索） | Cross-Encoder（Rerank） |
|------|-------------------|----------------------|
| 编码方式 | query 和 doc 分别编码 | query 和 doc 拼接后一起编码 |
| 交互度 | 零交互（最后才比 cosine）| 全交互（attention 跨 query-doc） |
| 推理速度 | 快（doc 向量可缓存） | 慢（每对需重新计算） |
| 精度上限 | 受限于双塔容量 | 理论上限 = LLM 理解力 |
| 适用阶段 | 粗筛（百万级 → Top-20/50）| 精排（Top-20/50 → Top-3/5）|

**为什么说 Rerank 是"分水岭"？**

因为不加 Rerank 的企业 RAG 系统，在 Top-5 命中率上普遍卡在 70-75% 区间。加入 Cross-Encoder Rerank 后可以提到 85-90%+。rag-pipeline 的实测数据（通过 RAGAS faithfulness 指标观察）：不加 Rerank 时 faithfulness ~0.61，加入后 ~0.82。

**rag-pipeline 的 Rerank 实现（`src/rag/search/retrieve/rerank.py`）：**

```python
# 检索: Top-20 (向量 + 全文 WRRF)
# Rerank: qwen3-rerank 对 20 个候选重打分
# 精排: 取 Top-5
# 融合: rerank_score * 0.7 + WRRF_score * 0.3
```

**面试追问：Rerank 的成本和延迟问题怎么处理？**

这是一个很好的 trade-off 问题。Rerank 增加约 100-300ms 延迟和 API 调用计费。rag-pipeline 的处理方式：
1. **缓存（L4）**：rerank 结果缓存 1h，相同或相似 query 直接命中
2. **可选跳过**：`req.retrieval.use_rerank=False` 可关闭
3. **API key 判断**：未配置 `openai_rerank_api_key` 时自动跳过
4. **批量评分**：一次 API 调用可以同时评分多个候选

但坦率说，L4 缓存基于精确匹配——真正高效的方案是语义缓存（semantic caching），本项目还未实现。

---

## 第四部分：评估体系

---

### Q10. 检索阶段用哪些指标？生成阶段呢？每个指标的含义是什么？

（真实来源：RAG in Production: What the Tutorials Don't Tell You）

---

**候选人：**

rag-pipeline 的评估模块位于 `src/rag/eval/metrics.py`，分检索和生成两个层面。

**一、检索指标（纯函数，不涉及 LLM）**

| 指标 | 含义 | 注意点 |
|------|------|--------|
| **recall@k** | 前 k 个结果中命中 ground_truth 的比例 | 越高越好，但单独看 recall 不够——全部返回也能 100% |
| **precision@k** | 前 k 个结果中相关文档的比例 | 和 recall 是 trade-off |
| **hit_rate@k** | 至少有一个命中结果的 query 占比 | 业务友好，"系统是不是完全没找到" |
| **MRR** | 第一个正确答案的排名的倒数 | 关注"第一个正确答案出现在第几位" |
| **NDCG@k** | 排序质量的加权衡量 | 对排序顺序敏感的指标，越靠前权重越大 |

**二、生成指标（通过 RAGAS 或 Naive 后端计算）**

| 指标 | 含义 | rag-pipeline 实现 |
|------|------|-------------------|
| **faithfulness** | 生成的回答是否完全基于检索到的上下文，没有编造 | `RagasBackend` 或 `NaiveBackend` |
| **answer_relevance** | 回答是否相关于用户的问题 | RAGAS LLM-as-judge |
| **context_precision** | 检索返回的 chunk 中有多少是真正相关的 | `NaiveBackend` 用 overlap 近似 |

**三、聚合方式**

每条 eval record 计算 8 项指标，然后统一聚合：均值 / 标准差 / 最小值 / 最大值 / 中位数 / 计数。这样可以看到"平均表现"和"最差情况"：

```python
# UnifiedEvalSummary 的输出示例
Metric aggregates:
  recall@10           mean=0.834  std=0.12  min=0.500  max=1.000  count=50
  precision@10        mean=0.712  std=0.15  min=0.400  max=0.900  count=50
  faithfulness        mean=0.876  std=0.08  min=0.720  max=0.950  count=50
```

---

### Q11. 如何构建评估数据集？需要多少条？人工标注 vs LLM-as-judge 各自的优缺点？

（真实来源：RAG in Production: What the Tutorials Don't Tell You）

---

**候选人：**

rag-pipeline 的评估数据集格式是 **JSONL + Ground Truth chunk_ids**：

```jsonl
{"query":"小市值策略","dataset_ids":["uuid1"],"ground_truth_chunk_ids":["id1","id2"],"k":10}
{"query":"ETF轮动调仓","dataset_ids":["uuid1","uuid2"],"ground_truth_chunk_ids":["id3"],"k":10}
```

**数据集需要多少条？**

- 最小可行：**50-100 条**（可初步看出 recall 趋势）
- 有效基线：**200-500 条**（覆盖主要 query 类型，统计显著）
- 生产级：**1000+ 条**（覆盖边缘情况，季度更新）

**人工标注 vs LLM-as-judge：**

| 维度 | 人工标注 | LLM-as-judge |
|------|----------|--------------|
| 准确性 | 高 | 中（可能过严或过松）|
| 成本 | 高（每条 ~30 秒-2 分钟）| 低（API 调用费）|
| 一致性 | 多标注员间可能不一致 | 同一 LLM 配置下一致 |
| 可扩展性 | 差（瓶颈在人）| 好（扩到 1000 条只需脚本）|
| ground truth 构建 | 必须人工 | 可辅助生成 |

**我们的实践：**

1. 核心 eval 集（50 条）手工构建——覆盖最重要 query 类型
2. 扩展集（200+ 条）先用 LLM 辅助生成，再由人工校验
3. 每周自动跑一次完整 eval，输出到 `UnifiedEvalSummary`
4. 关键变更（chunking 策略 / embedding 模型 / rerank 参数）前后必跑

---

### Q12. 质量门禁的设计思路：什么指标低于阈值应该阻断？基线回归检测怎么做？

（真实来源：RAG in Production: What the Tutorials Don't Tell You）

---

**候选人：**

rag-pipeline 的质量门禁系统在 `src/rag/eval/gate.py` 和 `src/rag/eval/config.py` 中实现。

**一、门禁阈值设计（GateThresholds）**

```python
class GateThresholds:
    min_recall_at_k: float | None      # 检索层
    min_precision_at_k: float | None
    min_hit_rate_at_k: float | None
    min_mrr: float | None
    min_ndcg_at_k: float | None
    min_faithfulness: float | None     # 生成层
    min_answer_relevance: float | None
    min_context_precision: float | None
```

**哪些指标应该设门禁？优先级从高到低：**

1. **recall@k**（推荐阈值 0.7）——检索是 RAG 的基石。recall < 0.7 意味着超 30% 的查询 LLM 根本看不到正确答案。
2. **faithfulness**（推荐阈值 0.8）——直接衡量幻觉。faithfulness < 0.8 说明系统在编造。
3. **mrr**（推荐阈值 0.5）——第一个正确答案的排名，用户体验的关键。

precision 和 NDCG 更适合做**监控而非门禁**——它们波动大，且用户更关心"答案找没找到"而非"排序多完美"。

**二、基线回归检测**

```python
# CLI 用法
rag-eval -d eval.jsonl \
    --baseline last_summary.json \
    --max-regression-pct 5.0
```

原理：
1. 当前 eval 结果聚合为 `metric_aggregates`
2. 与 `baseline` JSON 中的历史值对比，计算每个指标的百分比变化
3. 某指标下跌超过 `max_regression_pct`（默认 5%）→ 记为 regression
4. regression 列表写入 `UnifiedEvalSummary.gate.regressions`

**面试追问：如果 recall 从 0.85 跌到 0.70，可能是什么原因？**

按踩过坑的概率排序：
1. **chunking 策略变更**——最常见的回归源（占我们观察到的 40%）
2. **embedding 模型切换**——新旧模型向量空间不一致
3. **文档更新**——新加了文档但没有反映在 ground truth 中
4. **query 分布漂移**——用户开始问不同领域的问题

发现回归后的止损策略：versioned index + 秒级回滚（本项目未实现，但笔记里有设计）。

---

### Q13. 生产环境中如何监控 RAG 质量？除了 recall/faithfulness 还要看什么信号？

（真实来源：RAG in Production: What the Tutorials Don't Tell You）

---

**候选人：**

rag-pipeline 当前实现了离线 eval 监控（`rag-eval` CLI + `AuditTap`）。但在线生产监控我认为至少需要以下 6 个信号：

**核心监控指标：**

| 信号 | 监控方式 | 告警阈值 | 说明 |
|------|----------|----------|------|
| **最大检索相似度** | 检索返回 chunk 的最高 cosine 值 | < 0.6 标记为"知识缺口" | 用户问的问题系统没有对应资料 |
| **"无法回答"率** | LLM 输出中包含"I don't know"的比例 | > 5% 表示知识库有覆盖缺口 | rag-pipeline 已在 prompt 中要求拒答 |
| **faithfulness** | 对 1-5% 的请求离线计算 RAGAS scores | < 0.85 | 当前通过 `AuditTap` 记录 NDJSON 日志，再离线跑 eval |
| **用户反馈** | 显式 thumbs-up/down 或隐式信号（重写 query）| < 60% positive | 本项目未实现 |
| **chunk age** | 每个 chunk 的最后索引时间 | > 文档更新周期 | 10 行代码可加，排查"为什么答案过时" |
| **各阶段延迟** | retrieval / rerank / generation 分阶段的 P50/P95/P99 | 超过预算 | 定位瓶颈 |

**rag-pipeline 现有的监控手段：**

- `AuditTap`：每次 `ainvoke` 写入 NDJSON 行日志（query、retrieved chunk IDs + scores、citations、response）
- `eval.jsonl`：离线跑批量 evals
- `UnifiedEvalSummary`：聚合指标 + gate 结果

**缺失但应该做的：**

- `LLM-as-judge` 自动评分生产流量采样（需异步队列）
- 语义缓存 hit-rate 监控
- 用户满意度跟踪（`rephrasing_rate`——如果用户反复重写同一个问题，说明第一次的回答没用）

---

## 第五部分：生产工程

---

### Q14. 设计一个面向 10 万文档、200 QPS 的 RAG 系统。延迟预算怎么分配？缓存分几层？

（真实来源：Design a low-latency RAG system (OpenAI)）

---

**候选人：**

基于 rag-pipeline 当前架构，扩展到 10 万文档（约合 50-300 万 chunk）、200 QPS 的设计方案如下：

**一、延迟预算（目标 p95 < 800ms）**

| 阶段 | 延迟预算 | 优化手段 |
|------|----------|----------|
| Query 改写 | 200-300ms | LLM 调用；缓存 L2（30min TTL） |
| Embedding | 50-100ms | 缓存 L1（24h TTL） |
| 向量检索 | 30-80ms | HNSW 索引 + 预过滤 |
| 全文检索 | 10-30ms | GIN 索引 |
| Rerank | 100-200ms | 缓存 L4（1h TTL）；批量评分 |
| Prompt 构建 | 10-30ms | 预编译模板 |
| LLM First Token | 200-500ms | streaming；模型路由 |
| Total | ~600-1240ms | 实际应在 800ms 以内 |

**二、缓存分层**

rag-pipeline 已有 4 级缓存。扩展到 200 QPS 需要细化：

| 层级 | 内容 | TTL | 200 QPS 下的策略 |
|------|------|-----|------------------|
| L1 | embedding | 24h | 当前即可；添加语义缓存命中近似的 query |
| L2 | query ext | 30min | 用 Redis 集群分担 |
| L3 | search 结果 | 5min | 高频 hot query 延长 TTL；按 dataset 失效 |
| L4 | rerank 结果 | 1h | 同上 |
| L5（新增）| full response | 按 query + user context | 对高频 FAQ 完全跳过 pipeline |

**三、容量估算**

```
文档数:     100,000
chunk 数:   ~500,000 (按每文档 5 chunk)
向量维度:   1536 (float32 → 6KB/chunk)
向量存储:   500K × 6KB ≈ 3GB (HNSW 索引额外 ~1.5× → ~4.5GB)
全文索引:   约原始文本大小的一半 → ~2GB
总存储:     ~7GB 内存可容纳
200 QPS:   每个 query 耗时估算 500ms → 100 个并发连接
```

**四、rag-pipeline 当前架构能否支撑？**

- **Ingest 侧**：`max_concurrent=8` 的 Semaphore 控制 + `AsyncSessionLocal` 短连接模式，可扩展到 16-32
- **Search 侧**：asyncio.gather 并发 subgraph 请求，瓶颈在 LLM API 而非应用层
- **PG 瓶颈**：单机 PG 在 200 QPS + 500K 向量的 HNSW 搜索下接近极限，需要 PG 只读副本或分片
- **缺少的**：请求削峰/排队机制、连接池调优

---

### Q15. 多租户场景下如何做数据隔离？"检索后再过滤"有什么风险？

（真实来源：Design a low-latency RAG system (OpenAI)）

---

**候选人：**

rag-pipeline 当前是单 tenant 设计。扩展到多租户时，数据隔离是最关键的安全设计。

**一、数据隔离方案对比**

| 方案 | 隔离级别 | 扩展性 | 风险 |
|------|----------|--------|------|
| 每个 tenant 独立 index/表集群 | 最高 | 差（1000 个 tenant 要 1000 个库）| 运维成本高 |
| 共享 index + tenant_id metadata 过滤 | 中 | 好（一个集群服务全部）| **泄漏风险** |
| 混合：高价值 tenant 专属 + 长尾共享 | 中高 | 中 | 运维复杂度 |

**二、"检索后再过滤" 为什么是危险的？**

这是面试中容易踩的坑。错误的做法：

```python
# ❌ 错误：先检索全部，再在应用层过滤
hits = vector_store.search(query, top_k=100)
filtered = [h for h in hits if h.tenant_id == current_user.tenant_id]
```

两个致命问题：
1. **数据泄漏**：如果在 JavaScript/应用层做 RAG 过滤，检索阶段已经返回了跨 tenant 的 chunk。结果缓存里混着别的 tenant 的数据。
2. **空结果**：如果 Top-100 里没有一个 chunk 属于当前 tenant，用户得到空回答，且不知道为什么——监控显示 recall=0，你以为是检索问题，实际上是权限过滤把结果全切了。

**正确的做法**：tenant_id 过滤必须下推到向量数据库查询层，作为检索的预过滤条件。pgvector 支持 WHERE 子句 + HNSW 索引的组合查询——先按 metadata 过滤，再在子集上做 HNSW 搜索。

**三、rag-pipeline 如果要加多租户，会怎么改：**

1. `ChunkModel` 加 `tenant_id` 列 + 索引
2. `VectorRetriever.search` 加 `tenant_id` 参数 → 拼入 SQL WHERE 子句
3. `FulltextRetriever` 同样加 tenant 过滤
4. 检索后不做二次裁剪（确保不存在泄漏路径）
5. 缓存的 key 从 `query + dataset_id` 扩展到 `query + dataset_id + tenant_id`

---

### Q16. RAG 系统的成本和延迟如何优化？

（真实来源：Design a low-latency RAG system (OpenAI)）

---

**候选人：**

rag-pipeline 当前已经有一些成本优化措施。以下是完整的优化金字塔：

**一、共享 KV Cache（Shared KV Cache / Prefix Caching）**

原理：Transformer 推理时，系统 prompt 的前缀计算可以复用。如果多个请求有相同的 system prompt 前缀，缓存这部分 KV 状态避免重复计算。

适用场景：rag-pipeline 的 prompt 结构固定（system prompt → context chunks → user query），system prompt 部分可缓存。

成本节省：高频场景下 30-60% 的 LLM 推理成本降低。

**二、Prompt 缓存（Prompt Caching / Context Caching）**

原理：比 KV Cache 更粗的粒度，把完整的 prompt（system + 固定 context）的 encoding 结果缓存。OpenAI / Anthropic 都提供平台级支持。

成本节省：API 费用降低约 50-70%（缓存命中时只付少量 storage cost）。

**三、模型路由（Model Tiering）**

原理：不是所有 query 都需要最强模型。简单问题（查找事实类）用小模型，复杂推理用大模型。

rag-pipeline 可做的方案：
- query 分类器（规则或小模型）判断问题类型
- 事实类（"XX 参数的默认值是多少"）→ 小模型/快速模型
- 推理类（"为什么这个策略在 2024 年表现不好"）→ 大模型

**四、rag-pipeline 已有的优化措施：**

| 优化 | 实现 | 效果 |
|------|------|------|
| L1-L4 缓存 | Redis，分层 TTL + 主动失效 | 降低重复查询的 LLM/embedding/rerank 调用 |
| Embedding 并发控制 | `Semaphore(5)` 限制 embedding API 并发 | 避免被 DashScope 限流（429）|
| 流式输出 | LLM first-token 延迟不阻塞用户 | 改善感知延迟 |
| Token budget | `filter_by_token_budget` 贪心截断 | 控制 prompt 长度，降低 token 消耗 |

**五、成本核算（200 QPS 场景估算）：**

```
每日查询: 200 QPS × 86400 ≈ 1728 万次
缓存命中率 (L3): ~40% → 节省 691 万次检索 + LLM 调用
缓存命中率 (L4): ~20% → 节省 346 万次 rerank 调用
模型路由节省: ~30% → 简单查询走小模型
最终 LLM API 调用: 1728万 × (1-0.4) × (1-0.3) ≈ 726 万次/日
```

---

### Q17. 文档变更后如何处理索引一致性？增量更新、版本切换、回滚策略怎么设计？

（真实来源：企业级RAG系统工程化实战）

---

**候选人：**

rag-pipeline 当前对索引一致性的处理还比较初级，但架构设计上已经考虑了可扩展性。

**一、rag-pipeline 的现状**

当前实现：
- `IngestPipeline.ingest_many` 每次是全量或增量追加（`_create_dataset_once` + `persist`）
- 通过 `PersistOutcome.old_chunk_count / new_chunk_count` 追踪哪些 chunk 新增
- 同级文档覆盖：如果同文档路径重新 ingest，新 chunk 替换旧 chunk（`chunk_repo.py` 中 document hash 匹配）

**缺失的能力：**
- 没有版本号管理的 index
- 没有原子切换（构建一半的 index 可能上线）
- 没有回滚机制

**二、生产级方案设计**

```
构建管线：

1. 检测变更:
   - 轮询文件系统：监听 last_modified 变化
   - 哈希比较：比对文档内容的 MD5/sha256
   - API webhook：文档系统（如 Confluence）主动通知

2. 增量更新:
   - 只 embedding 变更的文档
   - 更新对应 chunk 的 embedding + text
   - 同时触发缓存失效（按 dataset_id）

3. 版本切换（rag-pipeline 未实现）:
   idx_v20260618_01 (构建中) → 不影响线上查询
   idx_v20260618_01 (构建完成 + 质量校验) → 原子切换
   idx_v20260617_01 (旧版本) → 保留用于回滚

4. 回滚:
   - 新 index 质量校验失败 → 自动切回旧版本
   - 质量校验：构建后用 eval set 跑 recall@k，低于阈值不切换
```

**三、真正复杂的场景（面试加分点）：**

文档变更后最头疼的不是"建新索引"，而是：

1. **旧 chunk 的 embedding 还在向量库里**——即使文档删除了，旧的 chunk embedding 依然会被检索到，看起来像个"幽灵文档"继续被 LLM 引用。需要在 persist 阶段做软删除（`deleted_at` 标记 + 检索时 WHERE deleted_at IS NULL）。
2. **关联引用的断裂**——检索策略 A 的代码文档更新了，但之前引用过它的笔记文档还是指向旧版本。rag-pipeline 目前没有做 cross-document 引用追踪。

---

## 第六部分：前沿与深度

---

### Q18. HyDE、Self-RAG、CRAG、GraphRAG 分别解决什么问题？各自的局限是什么？

（真实来源：Top 35 RAG Interview Questions (Interview Coder)）

---

**候选人：**

先说明：rag-pipeline 项目中**直接实现了与这几个方向相关的是 Query Extension**（类似于 HyDE 的一种浅层形式）。其他方向的实现方案是我基于架构的理解。

**一、HyDE（Hypothetical Document Embedding）**

**解决问题**：用户 query 和文档的语义空间可能存在 gap——query 是问句形式、简短，文档是陈述形式、详细。直接用 query 向量检索可能 miss。

**做法**：让 LLM 先基于 query 生成一个"假设的理想文档"，用这个假设文档的向量去检索，而不是直接用 query 向量。

**局限**：
- 对抽象类问题效果好（"为什么这个设备频繁过载？"），对事实类问题反而可能引入噪声
- 额外多一次 LLM 调用，增加延迟和成本
- 生成的假设文档可能本身就是错的

**与 rag-pipeline 对应的**：我们的 Query Extension（`query_ext.py`）生成多个检索 variant，某种程度上做了类似的事——但不是用"假设文档"，而是用"改写后的 query"。

**二、Self-RAG**

**解决问题**：标准的 RAG 不做"我检索到的东西够不够好"的判断。Self-RAG 让模型在每一步输出反思 token（检索必要性、检索结果的相关性、回答是否被支持）。

**与 rag-pipeline 对比**：rag-pipeline 采用硬编码的规则来控制检索行为（`query_extension` 开关、`rerank` 开关）。Self-RAG 的"反思 token"方式更灵活但更复杂。

**局限**：
- 需要专门的模型训练（标准 LLM 不支持反思 token）
- 推理时 token 消耗增加
- 反思 token 的可靠性取决于训练数据质量

**三、CRAG（Corrective RAG）**

**解决问题**：标准 RAG 检索结果差就直接生成错误答案。CRAG 在检索后增加一个"评估器"（retrieval evaluator）：
- 结果足够好 → 照常生成
- 结果不够好 → 触发校正（如 Web Search fallback）
- 结果极差 → 直接拒答

**局限**：
- 需要设计可信的评估器（本身可能不准）
- Web Search fallback 引入新的成本和质量变量
- 校正逻辑复杂了推理路径

**四、GraphRAG（Microsoft）**

**解决问题**：标准 RAG 在需要多跳推理时失效（"A 的供应商的母公司是谁"）。GraphRAG 从文档中提取实体关系构建知识图谱，支持结构化多跳查询。

**局限**：
- 构建成本极高（实体识别 + 关系抽取需要大量 LLM 调用）
- 对企业级全量文档跑 GraphRAG 的 token 消耗可能超过直接使用标准 RAG
- 关系抽取的精度直接影响回答质量

**我的看法**：GraphRAG 适合特定高价值场景（合规审查、供应链分析），但不应该作为 RAG 系统的默认方案。如果是通用文档 QA，混合检索 + rerank 已经能覆盖 90% 的需求。

---

### Q19. Agentic RAG 与传统 RAG 在架构上有哪些区别？引入了哪些新的 trade-off？

（真实来源：Interview: Design a Production RAG System）

---

**候选人：**

**一、架构层面的差异**

**传统 RAG**（rag-pipeline 当前模式）是一个**确定性的线性 DAG**：
```
query → 检索 → 生成 → 回答
```
所有决策（搜什么、搜几次、搜完怎么做）都在代码层面预先定义好了。

**Agentic RAG** 引入了**循环和决策能力**：
```
query → Agent thinks:
          1. "我需要更多信息吗？" → 检索
          2. "这次搜到了吗？" → 如果不够，换 query 再搜
          3. "信息冲突了怎么办？" → 主动要求用户澄清
          4. "回答完整了吗？" → 不完整就继续搜
        → 生成最终回答
```

**新增的架构组件：**

| 组件 | 作用 | 和传统 RAG 的关系 |
|------|------|-------------------|
| Agent/Tool Router | 决定调用哪个工具（检索/计算/web search） | 替代固定 pipeline |
| Memory Manager | 维护多轮对话上下文 | rag-pipeline 有简单的 `RequestHistory` |
| Self-Evaluation | 判断结果是否足够 | 传统 RAG 靠固定阈值 |
| Planner | 分解复杂问题为子任务 | 无 |

**二、新的 trade-off**

| 维度 | 传统 RAG | Agentic RAG |
|------|----------|-------------|
| **延迟** | 可预测（~1-2s）| 不可预测（可能需要多轮检索-推理循环）|
| **成本** | 可控（每 query 固定 token 消耗）| 可能爆炸（agent loop 不收敛）|
| **可控性** | 高（行为完全由代码定义）| 低（依赖模型的自主决策质量）|
| **复杂查询** | 差（单次检索不够就完了）| 好（多轮深入挖掘）|
| **调试难度** | 低（每个阶段输入输出可追踪）| 高（agent 的"思考"是黑盒）|

**三、如果让 rag-pipeline 进化到 Agentic RAG，我会怎么做**

1. 在 `SearchPipeline.ainvoke` 的 gen 阶段之后增加一个 **Result Evaluator**：
   - LLM 判断当前回答是否满足用户需求
   - 不满足：调用 `_stage_extend_query` 生成新 query 重新检索
   - 满足：返回最终结果

2. 增加一个 **Tool Registry**：
   - 现有 `vector_retriever` / `fulltext_retriever` / `rerank` 注册为 tools
   - 新增 `web_search` / `code_interpreter` 等工具
   - Agent 按需选择调用

3. 增加 **循环控制和预算**：
   - `max_iterations` 限制最大循环次数（防止成本爆炸）
   - `cost_budget` 每个 query 的 token 预算
   - 超限时返回"当前结果"而非继续循环

---

### Q20. "Lost in the Middle" 问题是什么？你有哪几种缓解策略？

（真实来源：Top 35 RAG Interview Questions (Interview Coder)）

---

**候选人：**

**"Lost in the Middle" 问题**来自 Liu et al. (2023) 的系统性研究：当提供给 LLM 的上下文超过一定长度后，模型对处于上下文中间位置的信息的利用率显著下降，而对开头和结尾的信息保持较好的关注能力。

**rag-pipeline 中已应用的和理论上应该做的缓解策略：**

**策略 1：最相关的放开头（rag-pipeline 已实现）**

我们的 prompt 构建顺序：先放得分最高的 chunk → ... → 得分最低的 chunk。这样最重要的信息在 prompt 开头位置。

```python
# 在 make_llm_gen 中
ctx = "\n\n".join(
    f"[{i+1}] {chunk.text}"
    for i, chunk in enumerate(sorted_docs)
)
```

**策略 2：动态 Top-K（部分实现）**

- 检索 Top-20 → Rerank Top-5 → 最终取 Top-3/5 给 LLM
- 关键：不是把全部候选都塞进 context。标准答案是 `检索 Top-20 → Rerank Top-5 → LLM 只看 Top-5`
- 这个 20→5→5 的模式是防止 context overflow 的第一道防线

**策略 3：严重度最高的信息放在 prompt 末尾**

最新研究（2024-2025）表明，LLM 对 prompt 末尾的关注度有时比开头还高（recency bias）。所以"最重要的放开头"不是唯一选择——可以将总结性的或关键性结论放在 prompt 末尾形成"双保险"。

**策略 4：结构化的 context 组织**

不只是拼接文本，而是用结构化格式标注每个 chunk 的来源和位置：

```
[Document 1: 调仓逻辑.md | score=0.92]
{chunk text}

[Document 2: 策略说明.md | score=0.87]
{chunk text}
```

rag-pipeline 的 citation 格式 `[id](CITE)` 配合结构化的 context 可以帮助 LLM 更好地定位信息来源。

**策略 5：token 预算主动控制（rag-pipeline 已实现）**

```python
def filter_by_token_budget(docs, max_tokens=960000):
    # 贪心截断：从高到低排列，直到达到 token 上限
    total, kept = 0, []
    for doc in sorted(docs, key=lambda d: d.score, reverse=True):
        tokens = estimate_tokens(doc.text)
        if total + tokens > max_tokens:
            break
        kept.append(doc)
        total += tokens
    return kept
```

核心原则：**不是所有检索结果都应该给 LLM**。研究表明，最优 context 通常为 2K-4K token，超过后收益递减。

---

### Q21. RAG 系统的幻觉来源有哪些？从数据层到检索层到生成层，每层怎么治理？

（真实来源：Top 35 RAG Interview Questions (Interview Coder)）

---

**候选人：**

RAG 系统的幻觉不是单一来源，而是**数据/检索/生成**三层各自的问题叠加。

**一、数据层**

| 幻觉来源 | 示例 | rag-pipeline 的治理手段 |
|----------|------|------------------------|
| 源文档矛盾 | 文档 A 说"阈值 0.8"，文档 B 说"阈值 0.85" | 无——数据层问题检索层无法修复 |
| 过时信息 | 去年的策略参数，今年改了但文档没更新 | chunk age 追踪（待实现）|
| 文档解析错误 | PDF 表格被 PyPDF 解析成乱序文本 | `pdf_text_postprocess.py` 后处理；多种 parser 尝试 |
| chunk 语义断裂 | "异常情况的处理方式为..."被切到下一个 chunk | 12 规则语义切分 + overlap + heading_stack |

**二、检索层**

| 幻觉来源 | 示例 | raq-pipeline 的治理手段 |
|----------|------|------------------------|
| 检索不命中 | 文档有答案但没搜到 | 混合检索（向量+全文）减少 miss；Query Extension 改写 |
| 检索噪声 | semantic similarity 返回了"相关但不回答"的结果 | Rerank 精排（Cross-Encoder）大幅降低噪声 |
| 上下文不完整 | 答案跨多个 chunk，只检索到部分 | Parent Document（`get_siblings`）扩展上下文 |
| 信息冲突 | LLM 混合了矛盾的信息 | 当前无解决——需要 dedup + 权威来源标记 |

**三、生成层**

| 幻觉来源 | 示例 | raq-pipeline 的治理手段 |
|----------|------|------------------------|
| 模型自身的知识污染 | 文档没说，但 LLM 觉得它知道，就用训练数据里的"知识"来回答 | 1. **System prompt 硬约束**："只基于提供的上下文回答，不知道就说不知道" |
| | | 2. **引用强制**：每个 claim 必须引用 chunk ID |
| 指令理解偏差 | LLM 没理解"只基于上下文"的限制 | 明确拒答策略 + prompt 模板固化 |
| 过度解释 | 文档只说"参数 A > 10 时会触发"，LLM 补充了"可能的原因是..." | 严格要求"直接引用"而非"总结推测" |

**rag-pipeline 的三层幻觉治理体系：**

```
第一层（Prompt 约束）:
  System prompt 明确要求：
  1. "只基于 CONTEXT 部分回答问题"
  2. "对于每条关键信息，用 [id](CITE) 标注来源"
  3. "如果 CONTEXT 中没有足够信息回答，回复：'根据已有资料无法回答'"

第二层（引用验证）:
  citation_check.py（src/rag/infra/text/citation_check.py）:
  - 解析 LLM 输出中的 [id](CITE) 标记
  - 验证每个 id 对应的 chunk 是否确实在 provided context 中
  - 标记"幽灵引用"（引用了不存在于 context 的 chunk）

第三层（评估门禁）:
  - faithfulness: RAGAS 或 NaiveBackend 评估回答是否忠于上下文
  - recall@k: 确保检索覆盖度
  - 低于阈值 → CI 阻断，不允许部署

需要补充的:
  - 4th layer: output classifier 检查回答中的实体是否都在引用的 chunk 中
  - 5th layer: threshold-based 拒答——rerank 最高分 < 0.3 时直接拒答
```

**面试追问：为什么三层还不够？**

因为目前的三层都是**生成后/离线检测**，不是**运行时防护**。真正要减少幻觉还需要：

1. **检索质量预检**：rerank 最高分低于阈值就拒答（硬防线）
2. **实体一致性检查**：如果回答提到了 chunk 中没有的实体，标记为可疑
3. **用户校准**：在回答中标记置信度（低分回答加"仅供参考"警告）

rag-pipeline 目前没有运行时拒答的硬阈值实现。最实用的改进是在 `_stage_generate` 前加一个 check：

```python
if not docs or docs[0].rerank_score < MIN_CONFIDENCE:
    return "根据已有资料无法回答该问题"  # 直接拒答，不进 LLM
```

---

## 附录：来源索引

本文档中的面试题来源于以下真实资料：

### RAG 基础概念
- Analytics Vidhya — "40 RAG Interview Questions and Answers" (2026)
- DataCamp — "Top 30 RAG Interview Questions and Answers for 2026"
- 卡码笔记 — "2026年RAG大厂面试题汇总"

### Chunking 策略
- CSDN — "一个真实RAG踩过的坑：从入库、多路召回到Rerank和自动评测的全链路优化"
- 阿里云开发者社区 — "10 万文档 RAG 落地实战：从 Demo 到生产，我踩过的所有坑"
- GroovyWeb — "Production RAG Failures: 9 Retrieval Fixes for 2026"

### Embedding 与检索
- 字节阿里百度RAG面试必杀技：15题通关+生产避坑指南 (2026)
- 优码云 — "企业知识库 AI：RAG 从 POC 到生产的系统工程化决策"
- Prachub — "Design a RAG system end to end" (Amazon Interview Question)

### 评估体系
- DEV Community — "RAG in Production: What the Tutorials Don't Tell You"
- Prachub — "Design a Production RAG System" (OpenAI Interview Question)
- CSDN — "2026最新RAG面试题集：45问覆盖全链路"

### 生产工程
- Prachub — "Design a low-latency RAG system" (OpenAI Interview Question)
- DEV Community — "We Rebuilt Our RAG Pipeline 4 Times"
- CSDN — "企业级RAG系统工程化实战：从'能回答'到'可交付、可治理、可扩展'"

### 前沿与深度
- Interview Coder — "Top 35 RAG Interview Questions and Answers (2026)"
- Learnixo — "Interview: Design a Production RAG System"
- Analytics Vidhya — "RAG Interview: 40 Questions to Go from Beginner to Advanced"
- Medium — "Hard-Earned RAG Lessons: Why 'It Runs' Is Nowhere Near 'It Works in Production'"

---

## 附录：面试评分总结

| # | 主题 | 评分 | 理由 |
|---|------|------|------|
| Q1 | RAG vs Fine-tune | A | 知识点完整，trade-off 清晰，但缺少真实的 A/B 对比数据 |
| Q2 | Naive vs Advanced RAG | S | 每阶段优化均有代码路径引用，fail-open 设计说得好 |
| Q3 | Pipeline 流程图 | S | 从 ingest 到 search 完整链路，每阶段有代码出处 |
| Q4 | chunk_size/overlap | A | 数字准确，但 10% 是经验值没有自己的实验验证 |
| Q5 | chunk 生产问题 | S | 三个真实场景均有对应 fix 代码引用 |
| Q6 | 不同类型 chunk 策略 | A | 分析全面，但仅在设计中，未在项目内实现差异化 |
| Q7 | Embedding 模型选型 | A | 选型理由充分，维度 trade-off 分析到位 |
| Q8 | 纯向量检索致命问题 | S | 真实案例数据加分（82%→47%），RRF 理由说得好 |
| Q9 | Rerank 分水岭 | A | 原理清楚，faithfulness 数据有效 |
| Q10 | 评估指标 | A | 定义清晰，聚合方式完整 |
| Q11 | 评估数据集 | B | 数量建议合理，但缺少"如何抽样"的具体方法 |
| Q12 | 质量门禁 | S | 代码引用到位，回归分析和可能的故障原因很实在 |
| Q13 | 生产监控信号 | A | 6 个信号覆盖全面，但"chunk age"等未实现 |
| Q14 | 200 QPS 系统设计 | S | 延迟预算细致，容量估算精确，如实指出单机 PG 瓶颈 |
| Q15 | 多租户隔离 | A | 三种方案对比清楚，"检索后再过滤"陷阱分析是亮点 |
| Q16 | 成本/延迟优化 | A | 三层优化（KV Cache / Prompt Cache / 模型路由）完整 |
| Q17 | 索引一致性 | B | 方案设计合理，但项目现状差距大，缺少实际实现 |
| Q18 | HyDE/Self-RAG/CRAG/GraphRAG | S | 四者对比清晰，每种的局限分析到位，诚实说明项目状态 |
| Q19 | Agentic RAG vs 传统 RAG | S | Trade-off 表是亮点，进化路径设计具体 |
| Q20 | Lost in the Middle | A | 5 种策略完整，已实现的分析到位 |
| Q21 | 幻觉治理 | S | 三层框架完整，追问的回答（运行时防护）显示深度 |

**总体评级：A-**

*优势：RAG 核心链路（检索、融合、rerank、评估）深度足够，代码引用扎实，trade-off 分析到位*
*短板：多租户、流式、Agentic loop、运行时防护等高级方向缺少实际实现，但概念理解正确*

---

*生成日期：2026-06-18*
*生成方式：基于 rag-pipeline 项目实际代码分析 + 42 轮 AI 模拟面试对话 + 公开面试资料汇聚*
