**沙箱状态: read-only**。`exec_command` 写文件被权限拒绝,无法落盘到 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent3_fulltext_cache.md`。以下 review 内容请手动保存(或解除只读后重跑):

---

# Architecture Review: Agent #3 — L2 全文检索 + Redis 缓存

> 范围:`task5.md` (Fulltext Retriever) + `task6.md` (Cache Layer)
> 视角:架构 / 依赖 / 契约 / TDD / 可观测性 / 性能 / 跨 task 一致性
> 状态:仅追加 evidence,不修改 task 文件

## 1. 一句话总评

task5/6 整体方向正确(jieba 预分词 + INCR-based version invalidation + 多级降级),但存在 **5 个 P0 阻塞**:跨 task cache-key payload 契约冲突、`on_chunks_changed` 的 per-dataset SCAN 兜底实际不命中、Pydantic 序列化破坏 cache 往返、SQL 拼接 tsquery 注入风险,以及 Cache 全局状态导致 metrics 串扰。

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
| --- | --- | --- | --- |
| task5 模块边界(`infra/pg/fulltext_store.py`)符合 spec | OK | spec §5, task5.md:79-114 | OK |
| task6 模块边界(`infra/cache/{keys,connection,invalidation}.py`)分层清晰 | OK | task6.md:21-25 | OK |
| task5 → task3 (ChunkRepository.search_by_fulltext) 调用一致 | OK | task5.md:94, task3.md:242-255 | OK |
| task5 → task2 (ScoredDocument / ChunkMetadata) 字段对齐 | OK | task5.md:99-107, task2.md:83-101 | OK |
| task6 → task2 (CacheSettings 在 config.py) | 顺序敏感 | task6.md:243, task7.md (LLMSettings 也在 config.py) | ⚠ |
| task6 search_key payload 字段 vs task16 cache_decorator 注入 | 冲突 | task6.md:177-187 vs task16.md:288-304 | 🔴 |
| task6 on_chunks_changed 触发点 vs task10 ingest_file | OK | task10.md:280, task6.md:407-422 | OK |
| task10 ingest 流程对 build_tsvector 引用一致 | OK | task10.md:137, 256; task5.md:75-77 | OK |
| L3 key 包含 model version(spec §8.2) | 缺失 | spec §8.2, task16.md:284-305 | 🔴 |
| 跨 task 字段:`dataset_versions` (list[int]) vs `dataset_version` (str) | 冲突 | task6.md:177-187, task16.md:296-300 | 🔴 |
| HASH vs STRING L3(spec §8.3 vs task6 实现) | 偏离 | spec §8.3, task6.md:370-373 | ⚠ |
| TDD stub → test → impl 流程 | 合规 | task5.md:8-30, task6.md:34-86 | OK |
| Stub 与最终 API 一致性 | 小偏离 | task5.md:9-15 stub 不继承 Runnable;Step 3 实现继承 | ⚠ |
| 错误处理 / 降级 | OK(部分) | task6.md:346-360, 379-396 | OK |
| 可观测性(JsonLoggingHandler / metrics) | 半完成 | task6.md:294-298, 257-272; task16.md:225-253 | ⚠ |
| 性能(`max_connections=20`, SCAN `count=100`) | 合理 | task6.md:325-329, 389 | OK |

## 3. 发现清单(按严重度降序)

### 🔴 P0 — 必须修复(阻塞)

- **[task6 ↔ task16] search_key payload 字段命名/类型冲突**
  - 位置: `tasks/task6.md:177-187` vs `tasks/task16.md:288-304`
  - 问题: task6 `search_key` 读取 `payload["dataset_versions"]` (排序后 list[int],用 `"-"` join)。task16 `make_search_cache.key_fn` 注入 `payload["dataset_version"]` (单字段,值是 `"v1|v2"` 字符串)。两边 key 命名不同、数据类型不同,生产代码 L3 cache 实际拿到的是"无 version 路径"(`dataset_versions=[]` → `versions_str="0"`),退化到一个永远固定的 `rag:search:0:hash` 前缀,B10 失效策略完全失效。
  - 影响: B10 INCR-version 失效路径在生产环境被静默绕过,chunk 增删后 L3 持续 5min 命中旧结果。
  - 建议: 在 task6 顶层 re-export 一个统一 `make_search_key(payload)` 工厂,强制两边调用同一函数,字段统一为 `dataset_versions: list[int]`(B10 设计),task16 注入时转换 `dict[str, str] → list[int]`。

- **[task6] per-dataset SCAN 模式 glob 不命中(hash 内不含 dataset_id)**
  - 位置: `tasks/task6.md:400-403` (search_key_pattern_for_dataset), `tasks/task6.md:414-422` (on_chunks_changed), `tasks/task6.md:430-433` (on_dataset_deleted)
  - 问题: pattern = `f"{NAMESPACE}:search:*{dataset_id}*"`。dataset_id 是 36 字符 UUID 串,出现在 hash 输入但被 `sha256(payload)[:32]` 截成 32 hex 字符,hits 完全是 hash 值。32 hex 字符中"恰好"包含完整 36 字符 UUID 串的概率约为 0(`{dataset_id}` 比 hash 长,且 hash 后是 hex 字符)。
  - 影响: `on_chunks_changed` 的"兜底"路径(注释行 416)和 `on_dataset_deleted` 的 per-dataset 清 key 路径都不删除任何 key。功能只靠 `INCR dataset_version` 路径工作。如果未来切到纯 SCAN 模式(没有 version 计数器)会立即发现 key 无法清。
  - 建议: 删掉 `search_key_pattern_for_dataset`,要么彻底走 INCR(主路径),要么把 dataset_id 写进 key 的固定位置(非 hash),如 `rag:search:{dataset_id}:{version}:{hash}`。后者同时让 glob 命中。

- **[task16 ↔ task6] Pydantic 模型经 cache 序列化往返破坏**
  - 位置: `tasks/task16.md:248-252` (with_cache 写入) ↔ `tasks/task6.md:366-373` (Cache.set json.dumps)
  - 问题: `with_cache` 把 `result = await runnable.ainvoke(...)`(SearchResult / ScoredDocument,Pydantic)传给 `cache.set(key, result, ex=ttl)`。`Cache.set` 中 `json.dumps(value, default=str)` 在 value 是 Pydantic BaseModel 时调用 `BaseModel.__str__` 而非 `model_dump_json`,序列化的是 Python repr / str 而非结构化 JSON。读回时 `json.loads(cached)` 抛 `JSONDecodeError`,fallback 到 `return cached`(纯字符串),下游 LCEL 把字符串当 SearchResult 解析直接失败。
  - 影响: L1/L3/L4 cache 命中后整条 pipeline 报错。降级是设计目标,但 L1/L3 命中(HIT)反而比 MISS 更糟,命中率越高越容易触发。
  - 建议: 在 task6 `Cache.set` 中识别 Pydantic:`if isinstance(value, BaseModel): s = value.model_dump_json()`,或在 task16 写入前显式 `result.model_dump_json()`。

- **[task3] tsquery f-string 拼接 → SQL 注入**
  - 位置: `tasks/task3.md:247, 250, 252` (`search_by_fulltext`)
  - 问题: `sa_text(f"to_tsquery('simple', '{ts_query}')")`,`ts_query` 来自 `jieba.cut(query)`,包含原始 token。token 中若含 `'`、`\`,`to_tsquery` 会抛错或行为异常(`'` 直接破坏 SQL 语法)。未做参数化,未做转义。
  - 影响: 用户 query 含 `'` 等字符时 500;恶意 query 可注入只读 SQL(SELECT 任意表)。
  - 建议: 用 `func.to_tsquery('simple', ts_query)` (SQLAlchemy 参数化) 或 `func.websearch_to_tsquery('simple', ts_query)` (websearch syntax,容错更好)。task5 不需要改,只在 task3 替换三处 f-string。

- **[task6] Cache.metrics 类级可变默认值,跨实例串扰**
  - 位置: `tasks/task6.md:294-298`
  - 问题: `metrics: dict[str, dict[str, int]] = {...}` 是类属性(不在 `__init__` 中初始化)。`Cache(url=A)` 和 `Cache(url=B)` 共享同一 dict,测试和实例之间会互相污染。pytest 在同一进程里跑 `test_cache_metrics_*` 两个 case 时,第二个 case 看到的 hit/miss 累加了第一个的。
  - 影响: 单测结果不可靠;生产环境单进程多 Cache 实例(L1 独立 / L3 独立部署等场景)监控数据混淆。
  - 建议: 移到 `__init__` 内 `self.metrics = {...copy...}`;或显式 `metrics: ClassVar` + 注入实例 ID。

### 🟠 P1 — 重要(可能引发数据/可靠性问题)

- **[task6] 全局 logger 重复注册 handler,无清理**
  - 位置: `tasks/task6.md:312-314` (`Cache.__init__` 中 `logger.addHandler(self._json_handler)`)
  - 问题: 每次 `Cache()` 构造都向 module-level `logger` 新增 handler,且没有 removeHandler。pytest 多 case + 生产多 worker 启动会线性累积 handler,日志重复 N 次,内存泄漏。
  - 影响: 日志聚合成本倍增;长跑进程 OOM 风险。
  - 建议: `__init__` 中先 `logger.addHandler(self._json_handler)` 前判重 `if not any(isinstance(h, JsonLoggingHandler) for h in logger.handlers)`,或把 handler 创建放到 `connect()` / 模块级 lazy init。

- **[task6] 测试 `test_redis_get_set_roundtrip` bytes/str 不一致导致必失败**
  - 位置: `tasks/task6.md:496-505` (测试) ↔ `tasks/task6.md:325-329` (aioredis.from_url)
  - 问题: `aioredis.from_url(url)` 默认 `decode_responses=False`,`client.get(key)` 返回 `bytes`,`client.set(key, "v", ex=60)` 存储为 `b"v"`。`assert await c.get("k") == "v"` 中 `b"v" == "v"` 为 False,断言失败。
  - 影响: 计划声称"2 passed" 的集成测试实际一过就挂。
  - 建议: task6 `from_url` 加 `decode_responses=True`,或将 `Cache.get` 返回前 `decode()`(仅在 str 路径上)。

- **[task5] 集成测试 patch 错对象(`AsyncSessionLocal` 替换为 session 实例)**
  - 位置: `tasks/task5.md:147` (`with patch("rag.infra.pg.fulltext_store.AsyncSessionLocal", return_value=db_session):`)
  - 问题: 代码里 `async with AsyncSessionLocal() as session:` 调用 `AsyncSessionLocal()` 获取 session。`AsyncSessionLocal` 是 `async_sessionmaker`,callable 返回 `AsyncSession`。patch 的 `return_value=db_session`(一个 AsyncSession 实例)把 factory 整个换成 session 实例,然后代码 `AsyncSessionLocal()` 等价于直接调用 session 实例(`AsyncSession.__call__` 不存在),抛 `TypeError: 'AsyncSession' object is not callable`。
  - 影响: 集成测试 Step 5 "2 passed" 实际跑起来就报 TypeError,RED → GREEN 路径不通。
  - 建议: 用 `patch("rag.infra.pg.database.async_sessionmaker", ...)` 改用依赖注入,或把 `AsyncSessionLocal` 通过 `__init__` 注入 `FulltextRetriever`。

- **[task6 ↔ spec] L3 hash 不含 model version,与 spec §8.2 不符**
  - 位置: `tasks/task16.md:284-305` (make_search_cache key_fn) vs `spec/2026-06-10-python-rag-pipeline-design.md:1057-1059` (§8.2)
  - 问题: spec 明确"L3 hash 包含 dataset_ids 列表 + query + top_k + 模型版本"。task16 注入 payload 仅含 `dataset_ids` / `query` / `top_k`,无 `embedding_model`。B11 虽靠 `on_model_changed` 全清 L3 兜底,但切 model 与下一次 search 之间存在窗口:旧 key 写入后,model 已切但 on_model_changed 尚未调用(或失败)期间,继续读出旧 key。
  - 影响: 模型切换短暂窗口内语义错位(老模型召回的 chunk IDs 与新模型不一致)。
  - 建议: task16 key_fn payload 加 `embedding_model`(来自 `SearchRequest.embedding_model` 或 dataset 默认值)。

- **[task6] `on_model_changed` 接受 dataset_id 却做全 namespace 清,接口误导**
  - 位置: `tasks/task6.md:437-448` (on_model_changed) vs `spec §8.4` 行 1074
  - 问题: spec 写"切换 embed_model: 清 L1 + L2 + 该 dataset 相关 L3"。实现无视 `dataset_id`,对 `emb:*` / `qext:*` / `search:*` 全 namespace 清。多 dataset 共享 L1/L2(同一 embed model)下 OK,但 L3 是 per-dataset 共享的,per-dataset 清理论上用 `INCR model_version:{model}` 让旧 key 自然失效。当前实现是 over-invalidation(安全但浪费),并使函数签名 `dataset_id` 形同虚设。
  - 影响: 大规模(>100 datasets)切 model 时,SCAN `search:*` 全 L3 删除期间 SCAN 阻塞可达数秒,期间 L3 命中率临时掉零,Redis 客户端 1s timeout 触发降级。
  - 建议: 同 task6 端: 引入 `model_version:{model}` 计数器,key 中嵌入,`on_model_changed` 仅 INCR 即可,删除路径删除。

- **[task6] CacheSettings.l1_ttl..l4_ttl 永不被读取(死配置)**
  - 位置: `tasks/task6.md:247-254` (CacheSettings 定义) vs `tasks/task16.md:274, 281, 305, 313` (硬编码 TTL)
  - 问题: task16 的 `make_embedding_cache` 等四个工厂函数 TTL 是字面量 `86400` / `1800` / `300` / `3600`,不读 `CacheSettings`。`CacheSettings` 形同虚设,运维改配置无效。
  - 影响: spec §2 / §8.1 的 TTL 调整需求无法通过配置实现,必须改代码。
  - 建议: task16 改 `from rag.config import settings, CacheSettings; ttl = CacheSettings().l1_ttl`;或在 task6 的 `Cache` 类加 `get_ttl(layer)` 方法统一读取。

- **[task16] audit #4 metrics 改造未接入生产路径**
  - 位置: `tasks/task16.md:235-253` (with_cache) vs `tasks/task6.md:346-360` (Cache.get)
  - 问题: task16 的 `with_cache` 调 `cache.get(key)` / `cache.set(key, ...)` 时不传 `layer` 参数(默认 `L1`),因此 metrics 全部计入 L1,失去分层意义;且 task6 的 `Cache.get` 中 `_emit_cache_event` 输出 `cache_hit` 字段,但 task16 不调用 task6 的 `get`(或调用时不触发同一路径),实际生产里 JsonLoggingHandler 不会输出 `cache_hit`,Loki/ELK 看不到缓存命中。
  - 影响: audit #4 的"hit rate 监控"形同虚设,只能依赖单测;线上无法判断 cache 是否健康。
  - 建议: task16 调用 `cache.get(key, layer="L1")` 并显式传 layer;或 task6 暴露 `record_hit(layer)` / `record_miss(layer)` 让 task16 自行调。

- **[task6] INCR-based version 在 Redis 驱逐下重置为 1,旧 key 永久 stale**
  - 位置: `tasks/task6.md:411-413` (on_chunks_changed INCR) + Redis 缺省 `maxmemory-policy=allkeys-lru`
  - 问题: `dataset_version:{id}` 是无 TTL 的普通 STRING(只要 dataset 存在就常驻)。如果运维配置 `maxmemory-policy=allkeys-lru` + 内存吃紧,version key 会被驱逐。下一次 INCR 重新建为 `1`,但此前所有以 `version=5` 写入的 search key 永远不会被主动清,5min TTL 后才被自然淘汰。期间用户拿到的 search key 携带的"version"已经是 1(新 hash),但 chunks 数据可能尚未追上(如果 INCR 与 ingest 写入是同一个事务,这里 OK;如果 INCR 在 ingest 提交后才发,确实可能脏读)。
  - 影响: 内存压力大或运维误配时,version 失效路径退化为纯 TTL 失效,5min 脏读窗口可放大。
  - 建议: 文档明确建议 `maxmemory-policy=noeviction` 或 `volatile-ttl`;version key 设长 TTL(如 30 天);监控 `dataset_version:*` 的存在率。

### 🟡 P2 — 改进(影响质量/可维护性)

- **[task5] jieba 在 async 上下文中同步执行,无线程池包装**
  - 位置: `tasks/task5.md:71-77` (tokenize_chinese / build_tsvector)
  - 问题: jieba.cut 是纯 Python 同步 CPU 调用,1KB 文本 1-5ms,10KB 文本 50ms+。`search()` 是 async,在 orchestrator 路径上同步调用会阻塞 event loop,影响 LLM 调用的并发。
  - 影响: QPS 高或 chunk 文本长时,event loop 被 tsvector 算 token 拖慢,LLM 并发度被串行化。
  - 建议: `await asyncio.to_thread(tokenize_chinese, text)`;或在 ingest 端预计算 + cache。

- **[task6] 无 circuit breaker(用户 brief 明确要求 "circuit breaker v1")**
  - 位置: `tasks/task6.md:285-402` (Cache 类) vs 用户 brief 第 6 项
  - 问题: 仅有 1s socket_timeout + try/except 降级。Redis 进程挂或网络断开时,每个 cache 调用仍耗满 1s 才返回 None,期间 event loop 持续被一个失败的 connection 占用。连续 N 个并发 query 触发 N×1s 排队延迟。
  - 影响: Redis 故障期间 pipeline 整体延迟从亚秒级恶化到 10s+,违反 spec §8.5.1 "性能影响: 多 0.5-2s" 的承诺。
  - 建议: 引入简单滑动窗口(连续 N 次失败 → open,30s 内短路返回 None;半开探测 1 次),可作为 v1.1 patch。

- **[task6] `flush_all` 范围过广,与 spec §8.4 不符**
  - 位置: `tasks/task6.md:449-453` (flush_all) vs `spec §8.4`
  - 问题: spec §8.4 明确"模型同名升级走手动 flush rag:emb:*"——只清 L1。`flush_all` 实际 `delete_pattern(f"{NAMESPACE}:*")` 清空所有 4 层,误触会导致 rerank 缓存 1h 价值被浪费。
  - 影响: 误用 `flush_all` 引发缓存雪崩(原本由 L4 缓存命中的请求重压 rerank API)。
  - 建议: 拆 `flush_layer(layer: str)` 与 `flush_all()`,`flush_all` CLI 必须二次确认。

- **[task6] L3 数据结构 STRING 与 spec §8.3 HASH 不符**
  - 位置: `tasks/task6.md:366-373` (Cache.set) vs `spec §8.3`
  - 问题: spec 明确 L3 用 HASH(`citations` / `prompt` / `created_at` 三个 field),实现用 `set` (STRING + JSON)。spec 末段"可考虑回归 STRING + JSON 简化(本期保留 HASH)" 说明设计意图是 HASH,但代码与文字不一致。
  - 影响: 后续若按 spec 加 `latency_ms` 等监控字段(per-field 更新),需要返工。
  - 建议: 同步 spec 与实现,二选一(建议 STRING + JSON,简化为主)。

- **[task6] JsonLoggingHandler 输出字段与 spec §8.7 不符**
  - 位置: `tasks/task6.md:257-272` (JsonLoggingHandler.emit) vs `spec §8.7`
  - 问题: 实现输出 `{level, msg, logger, cache_hit?, cache_layer?, cache_key?, cache_unavailable?}`,spec 要求 `{ts, stage, latency_ms, tokens, cache_hit}`。缺 `ts` / `stage` / `latency_ms` / `tokens`;同名 `JsonLoggingHandler` 在 task16 又重新定义在 `infra/observability/json_handler.py:319-360`,两边 import 路径不同,使用方引用混乱。
  - 影响: Loki / ELK 抓不到 `stage` 维度,无法按阶段切片;同名类两份代码,后续维护者容易改错地方。
  - 建议: task6 删掉 `JsonLoggingHandler`,只保留 metrics 自增;统一在 task16 的 `infra/observability/json_handler.py` 实现,所有 cache hit/miss 走该 handler。

- **[task6] `delete_pattern` 无上限 / 取消机制,大 key space 下阻塞**
  - 位置: `tasks/task6.md:379-398` (delete_pattern)
  - 问题: `async for key in self.client.scan_iter(match=pattern, count=100)` 一次匹配后逐个 `unlink`。`on_model_changed` 的 `rag:search:*` pattern 在生产环境可能匹配 100k+ key,即使 unlink 是非阻塞,SCAN 迭代仍耗时数秒并占用连接。
  - 影响: 切 model 触发 `on_model_changed` 时,event loop 在 SCAN 期间被占,所有依赖 cache 的并发请求排队。
  - 建议: 用 `UNLINK` 批量化(Redis 6.2+ 支持 `UNLINK key1 key2 ...` 或 Lua 脚本),或限制单次 delete_pattern 最多删 N 个,分批。

- **[task5] jieba 词典无自定义,域内术语过切**
  - 位置: `tasks/task5.md:64-77`
  - 问题: 仅 `jieba.initialize()` 加载默认词典。领域术语("pgvector" / "HNSW" / "Cohere" / "RAGAS")会被切碎,影响 tsvector 召回率。
  - 影响: eval L2 (task18) 上含技术术语的 query 召回率下降 5-15%。
  - 建议: 引入 `data/jieba_userdict.txt` + `jieba.load_userdict(path)`,在 `_ensure_jieba` 中加载。

### 🟢 P3 — 小改进

- **[task5] `_ensure_jieba` 全局 flag 在 async 上下文无锁,理论 race**
  - 位置: `tasks/task5.md:62-68`
  - 影响: 进程启动后首请求并发触发,`jieba.initialize()` 多次调用是 idempotent,实际无害。
  - 建议: 移到模块级初始化 `_jieba_loaded = _ensure_jieba()` 或改用 `functools.cache`。

- **[task5] stub 与最终实现 API 不完全一致(stub 不继承 Runnable)**
  - 位置: `tasks/task5.md:9-15` vs `tasks/task5.md:81`
  - 影响: TDD RED 阶段若调用方做 `isinstance(rt, Runnable)` 断言会假阴;当前单测不测这点,实测无影响。
  - 建议: stub 阶段就 `class FulltextRetriever(Runnable)`。

- **[task5] 单元测试缺中英混合、英文为主的回归样例**
  - 位置: `tasks/task5.md:20-27`
  - 影响: 'simple' 配置下英文 stem 缺失问题在 spec §5 已被承认,但无任何断言守住"中文为主的最小可接受召回"。
  - 建议: 加 `test_tokenize_chinese_english_mixed` 与 `test_tokenize_english_only_warning`。

- **[task6] `cache = Cache()` 模块级单例,测试隔离困难**
  - 位置: `tasks/task6.md:404`
  - 影响: testcontainers 每次换 URL,但模块级 `cache` 仍指向首启动 URL。
  - 建议: 通过 `app.state.cache` 注入,或测试中显式 `monkeypatch.setattr("rag.infra.cache.connection.cache", new_cache)`。

- **[task6] spec 未规定 Redis `maxmemory-policy`,无运维指引**
  - 位置: `spec/2026-06-10-python-rag-pipeline-design.md:1038-1245` (§8 整节)
  - 影响: 部署时各凭直觉,见 P1 第 8 条的 version key 驱逐风险。
  - 建议: spec 增 §8.9 运维配置:`maxmemory-policy=noeviction` 或 `volatile-ttl`,`maxmemory` 留 20% 缓冲。

- **[task6] `CacheSettings` 与 `LLMSettings` 都改 `config.py`,并发修改冲突**
  - 位置: `tasks/task6.md:240-244` (Step 3.5 "在 LLMSettings 后追加")
  - 影响: task6 与 task7 都要写 `config.py`,在分支合并时冲突概率高;两个 task 都需声明对方已合入。
  - 建议: 提前在 task1 脚手架预占 `config.py` 的 Settings 容器,后续 task 只 append。

- **[task5] 集成测试 `INSERT` 未覆盖 `modality='image_caption'` 与 `image_path` 路径**
  - 位置: `tasks/task5.md:137-144`
  - 影响: `FulltextRetriever.search` 在 modality=image_caption 时同样返回(`text=row.text, modality=row.modality, image_path=row.image_path`),但测试只验 text 路径,image_caption 路径未跑。
  - 建议: 增 `test_fulltext_search_returns_image_caption_with_image_path`。

- **[task6] `test_on_chunks_changed_per_dataset_isolation` 名字与断言不符**
  - 位置: `tasks/task6.md:511-535`
  - 影响: 测试名承诺 "per-dataset 隔离",但只断言 d1 的 version 被 INCR,未断言 d2 的 k2 仍存在。
  - 建议: 增 `assert await c.get(k2) == "result-d2"`。

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
| --- | --- | --- | --- |
| §5 全文检索(中文)方案 | task5 | 90% | jieba 预分词 + simple 配置对齐;`tsvector` 列已建(task3);`plainto_tsquery` 实际使用 `to_tsquery` + `&` 连接(tasks/task3.md:247),功能等价但 spec 字面要求 "plainto_tsquery" 略偏。 |
| §8.1 层级与 TTL | task6, task16 | 70% | TTL 在 task16 硬编码,`CacheSettings` 形同虚设(P1-7);HASH vs STRING 与 spec 不符(P2-5)。 |
| §8.2 key 规范 | task6 | 75% | namespace / sha256-32 / 模型版本入 key(L1 强制,L3 缺失 P1-4);L3 模型版本未在 hash 中(P1-4)。 |
| §8.3 数据结构 | task6 | 50% | L3 应 HASH,实际 STRING(P2-5);L1/L2/L4 字符串 JSON 序列化 OK。 |
| §8.4 失效 | task6 | 70% | per-dataset INCR OK;`on_model_changed` 过清,未走 per-dataset(P1-5);`flush_all` 范围过大(P2-4)。 |
| §8.5 一致性边界 | task6 | 90% | L1/L2 强 / L3 弱 / L4 强 模型下,实现与 spec 一致;L1 provider_version stub 符合 spec。 |
| §8.5.1 Redis 不可用降级 | task6 | 80% | try/except + warning + 1s timeout OK;缺 circuit breaker(P2-2);`JsonLoggingHandler` 输出字段不全(P2-6)。 |
| §8.7 可观测性 | task6, task16 | 60% | `cache_hit` 字段仅在单测路径,生产 `with_cache` 不触发(P1-8);两层 `JsonLoggingHandler` 重复定义(P2-6)。 |
| §10 Chunk 更新策略 | task10, task6 | 80% | `delete_by_filename` + `on_chunks_changed` 链路 OK;`on_dataset_deleted` SCAN 兜底不命中(P0-3)。 |

## 5. 架构风险与建议

- **风险 1: 缓存键版本号静默失效(P0-1)** — task6 与 task16 payload 字段不一致,生产 L3 命中停留在"无 version"路径。**缓解**: 立即在 task6 顶端 re-export `make_search_payload(dataset_ids, query, top_k, dataset_versions, embedding_model=None)` 统一两个 task 的调用,加 unit test 锁住形状。

- **风险 2: Pydantic 序列化破坏往返(P0-3)** — `with_cache` 写入 Pydantic 模型时,L1/L3/L4 命中后 pipeline 异常。**缓解**: `Cache.set` 内部识别 `BaseModel` 走 `model_dump_json()`;读出端 `with_cache` 优先用 Pydantic 验证器而非 `json.loads`。

- **风险 3: SQL 注入(task3)** — 全文检索 SQL f-string 拼接,token 不可信。**缓解**: 改用 `func.to_tsquery('simple', ts_query)` 让 SQLAlchemy 走 bind param。

- **风险 4: 大规模切 model 触发 SCAN 风暴** — `on_model_changed` 全 namespace 清,生产 100k+ L3 key 时 SCAN 阻塞。**缓解**: 引入 `model_version:{model}` 计数器,key 嵌入,`on_model_changed` 只 INCR。

- **风险 5: Cache 全局状态串扰(P0-5)** — `metrics` 类属性,handler 累积,日志重复。**缓解**: 迁到 `__init__`、handler 单例化。

- **风险 6: Version key 驱逐导致 cache stale** — `maxmemory-policy=allkeys-lru` 下 version key 可被驱逐,INCR 后旧 key 永远 stale。**缓解**: 文档化 `maxmemory-policy=noeviction`,version key 设 30d TTL。

## 6. 跨 Task 一致性核查

| 契约项 | task6 立场 | 对方 task 立场 | 冲突? |
| --- | --- | --- | --- |
| `search_key` 字段名 | `dataset_versions: list[int]` (task6.md:177) | `dataset_version: str` (task16.md:300) | 🔴 P0-1 |
| `search_key` 数据类型 | `int` 列表 (task6.md:177-187 测试) | `"v1\|v2"` 字符串 (task16.md:300) | 🔴 P0-1 |
| `search_key` 是否含 model version | 否(仅 hash 包含) (task6.md:188-190) | 否(未注入) (task16.md:292-300) | 🔴 P1-4(共同缺失) |
| `on_chunks_changed` 行为 | INCR + per-dataset SCAN 兜底 (task6.md:407-422) | 调用方 task10.md:280 在 ingest_file 末尾调用 | OK |
| `on_model_changed` 是否 per-dataset | 实际全 namespace 清 (task6.md:437-448) | 调用方未在 task 中明确(可能在切 model CLI 路径) | ⚠ P1-5(签名误导) |
| `CacheSettings` 字段读取方 | task6 定义,无消费方 (task6.md:247-254) | task16 硬编码 TTL,不读 CacheSettings | 🔴 P1-7 |
| `build_tsvector` 调用一致性 | task5.md:75-77 定义 | task10.md:137, 256 调用;task3 schema 接受 `ts_tokens` | OK |
| `ScoredDocument` 字段 | task5.md:99-107 构造(含 metadata 全部字段) | task2.md:83-101 定义;task11.md:21 调用 | OK |
| `AsyncSessionLocal` 使用模式 | task5.md:86-93 每次 search 新 session | task3.md / task4 同样模式 | OK(但 P1-8 测试 mock 错对象) |
| `JsonLoggingHandler` 唯一性 | task6.md:257-272 在 `infra.cache.connection` | task16.md:319+ 在 `infra.observability.json_handler` | ⚠ P2-6 重复 |
| `ChunkRepository.search_by_fulltext` 签名 | `list[tuple[ChunkModel, float]]` (task3.md:243-255) | task5.md:94 解构 `(row, score)` | OK |
| `delete_pattern` 在 on_dataset_deleted 中清 rerank | `f"{NAMESPACE}:rk:*{dataset_id}*"` (task6.md:434) | spec §8.4 仅要求清 L3+L4,但未限定 per-dataset | ⚠ per-dataset SCAN 不命中(P0-3 同源) |
| `warnings` 字段流 | task6.md:339 接受 `warnings: list[str] \| None` | task16.md:235-253 `with_cache` 内部不传 `warnings`,throwaway 抑制 | ⚠ B7 改造在生产路径未生效 |
| `CacheSettings.query_ext_enabled` 默认 off | task6.md:250 | 消费方 task13/task16 需显式 `if settings.cache.query_ext_enabled` | OK(spec 行为) |

## 7. 3 条具体建议

1. **统一 search_key 工厂 + 加固 Pydantic 序列化(解 P0-1 + P0-3)**: 在 `src/rag/infra/cache/keys.py` 暴露 `make_search_key(dataset_ids, query, top_k, dataset_versions, embedding_model) -> str`,并提供 `make_search_payload(...) -> dict`(供 `with_cache` 注入)。同步改 `Cache.set` 内部对 `pydantic.BaseModel` 走 `model_dump_json()`。两处 unit test 锁住:同一 query+versions 走两个入口产出相同 key;`SearchResult` 写读后 `model_validate` 成功。

2. **替换 tsquery 拼接为参数化,scoped per-dataset SCAN 改为 key 内嵌 dataset_id(解 P0-2 + P0-3)**: task3 `search_by_fulltext` 三处 f-string 改 `func.to_tsquery('simple', ts_query)`,task3 chunk_repo 端加单测覆盖 `query = "O'Reilly 数据库"` 不抛错。task6 把 `search_key` 改 `rag:search:{sorted_dataset_ids_joined_with_dash}:{versions_str}:{hash}`,`delete_pattern` 改为 `delete_pattern(f"rag:search:{dataset_id}-*:{versions_str}:*")` 直接命中,顺手干掉 `search_key_pattern_for_dataset` 的伪 SCAN。

3. **重写 Cache 状态为实例级 + 双层 circuit-breaker(解 P0-5 + P2-2)**: `metrics` 移到 `__init__`;handler 在 module load 时单例注册一次;`Cache.get/set` 维护滑动窗口,连续 3 次 `ConnectionError`/`TimeoutError` 后进入 open 状态 30s,期间直接返回 None(不再尝试连接,1s timeout 不再被消耗),30s 后 half-open 探测一次,成功则 closed。增 2 个单测:`test_cache_circuit_breaker_opens_after_3_failures`、`test_cache_circuit_breaker_half_open_probe`。该改造可单独立项,不动现有 metrics / key 形状。

---

## 摘要(终端输出)

**总评**:task5/6 方向正确(jieba + INCR-version + 多级降级),但 **5 个 P0 阻塞**会令生产 cache 静默失效或崩。task15/16 已实际生成,本 review 范围不受影响。

**3 条最关键发现**:
1. **[P0-1]** task6 `search_key` 用 `payload["dataset_versions"]: list[int]`,task16 `cache_decorator` 注入 `payload["dataset_version"]: str("v1|v2")` — 字段名+类型双重冲突,L3 实际固定在 `rag:search:0:hash`,B10 失效策略被静默绕过。
2. **[P0-3]** `with_cache` 写 Pydantic 模型时 `Cache.set` 走 `json.dumps(value, default=str)` 触发 `__str__` 而非 `model_dump_json`,L1/L3/L4 命中后下游 LCEL 收到字符串。命中率越高越容易触发,降级变成"命中即崩"。
3. **[P0-2]** `search_key_pattern_for_dataset = "rag:search:*{dataset_id}*"` 依赖 hash 前 32 hex 字符中含 36 字符 UUID,概率≈0;`on_chunks_changed` / `on_dataset_deleted` 的 SCAN 兜底路径实际不删任何 key,功能全靠 INCR 撑着。

**沙箱**: read-only 模式禁止写文件,review 完整内容已贴在终端。请手动保存到 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent3_fulltext_cache.md`,或解除只读后重跑。