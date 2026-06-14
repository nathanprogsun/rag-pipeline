沙盒是只读且 `Approval policy: never`,无法在 sandbox 内写入。我提供完整 review 文本,可由用户落入目标文件 `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/reviews/agents/agent5_chunker_ingest.md`(其他 agent 1/2/3/4/9 的文件是其他会话写入的)。

---

## 终端摘要(≤500 字)

**总评**: task9/10 模块边界与依赖方向正确,关键 bug 在契约层:`_step_headings` 的 `_HEADING_LINE_RE` 不含标题文本,实际不能解析多级标题 → `parent_title` 始终空。

**3 条最关键发现**:
1. **🔴 [task9:188,262-282] heading 解析 bug**:`_HEADING_LINE_RE` 只匹配 `# ` 前缀,`re.split` 后得到的 `sec` 元素不再以 `#` 开头,`heading_path` 栈永远空。`test_markdown_header_inheritance` 弱断言给出伪绿。**修复**:用 `_HEADING_RE` 全模式 split,或改消费 `extract_structure` 输出。
2. **🔴 [task8:160-172 ↔ task10:213-218] 扁平 vs 嵌套契约冲突**:`extract_structure.heading_tree` 是扁平 `list[Heading]`(children 从未填充),`task10` 当嵌套栈 join 全部 heading → 整篇文档的 chunk 都写相同 `parent_title`,污染 task14 `parent_doc` 的 sibling 检索。
3. **🔴 [task10:284-356 ↔ task17:188-198] CLI 不调 `ingest_directory`**:`ingest_file` 串行,`LLMSemaphore=16` 与 `IngestResult.failed` 失效,首个文件失败即中断;SUPPORTED_SUFFIXES 过滤规则也未复用。

**附加**:事务内调 embed(P1-1)、embed 重试无 L1 缓存(P1-2)、`_split_section` 用 `_CAPTION_SEPS` 缺英文(P1-5)、`test_overlap_preserved` 退化断言(P1-6)、"12-level" vs "17-level" 跨 8 处文档矛盾(P2-1)。

> 关于 task15/16 缺失:任务简介称未生成,但目录中 `task15.md` (11.9KB) 和 `task16.md` (23.3KB) 实际存在,只有 `INDEX.md:15,17,53` 状态栏仍标 MISSING。

---

## 完整 Review 文本(可直接复制落入目标文件)

```markdown
# Architecture Review: Agent #5 — L3/L4 Chunker + Ingest Pipeline

> Reviewer: 架构设计师(只读)
> Scope: `task9.md` (Chunker) + `task10.md` (Ingest Pipeline)
> Cross-references: `task8.md` (Reader/Structure), `task3.md` (ChunkModel), `task5.md` (build_tsvector), `task6.md` (on_chunks_changed), `task7.md` (LLMSemaphore), `task17.md` (CLI), `spec §0.1 / §6.5.3 / §6.5.4 / §8.8 / §10 / §13`
>
> 关于 task15/16 缺失的更正:任务简介称 task15/16 未生成;实际目录中 `task15.md` (11.9KB) 和 `task16.md` (23.3KB) 已存在。`INDEX.md:15,17,53` 状态栏仍标记为 MISSING,与文件系统不一致。

---

## 1. 一句话总评

任务 9/10 在 API 契约层存在 3 处跨 task 冲突(`extract_structure` 平面化 vs 消费方假设嵌套、heading 解析在 task9 重复实现且正则有 bug、CLI 复用 `ingest_file` 而非 `ingest_directory`),事务边界把高延迟 embed 调用圈在内会持锁 10s+,`task9._step_headings` 实际不能解析多级标题。

---

## 2. 模块边界 / 依赖方向 / 契约一致性

| 检查项 | 结论 | 证据(file:line) | 评级 |
|--------|------|-----------------|------|
| 依赖方向: pipeline → infra (PG / cache / llm) | 正确 | `task10.md:135-141` 仅 import `infra.pg.fulltext_store` / `infra.cache.invalidation` / `infra.llm.semaphore` / `infra.pg.repositories.chunk_repo` | OK |
| 依赖方向: pipeline → ingest (reader / chunker / structure) | 正确 | `task10.md:135-136` | OK |
| 依赖方向: pipeline → domain | **未违反** (无 domain import) | `task10.md:135-141` | OK |
| 依赖方向: chunker → infra/domain | 纯计算,不依赖 infra/domain | `task9.md:155-184` | OK |
| 跨模块契约: `on_chunks_changed(dataset_id)` | 与 task6 签名一致 | 消费 `task10.md:262`;定义 `task6.md:409` `async def on_chunks_changed(dataset_id: uuid.UUID)` | OK |
| 跨模块契约: `build_tsvector(text) -> str` | 与 task5 签名一致 | 消费 `task10.md:137,256`;定义 `task5.md:75-78` | OK |
| 跨模块契约: `LLMSemaphore.run(provider, coro)` | 与 task7 签名一致 | 消费 `task10.md:241`;定义 `task7.md` semaphore.py class | OK |
| 跨模块契约: `extract_structure` 返回结构 | **冲突** | `task8.md:162-172` 返回 `heading_tree: list[Heading]` (扁平,未嵌套);`task10.md:213-218` 当作层级栈使用 | 🔴 |
| 跨模块契约: `Chunker.split(text) -> list[str]` | 与 task10 消费一致 | `task9.md:206`;消费 `task10.md:225` | OK |
| 跨模块契约: `IngestPipeline.ingest_file(...) -> int` | 与 task17 期望一致 | `task10.md:189-264`;`task17.md:334,189` | OK |
| 跨模块契约: `IngestPipeline.ingest_directory(...) -> IngestResult` | **与 task17 CLI 不一致** | 定义 `task10.md:284-356`;CLI `task17.md:188-198` 完全不调用,自行 `for` 循环串行 | 🔴 |
| 跨模块契约: `Heading` 字段 | `task8.md:140-144` 定义 `level / text / line / children`;`task9.md` 内部用 `"{level}:{title}"` 字符串栈,跨边界语义不统一 | `task9.md:265-281,505-524` vs `task8.md:140-144` | ⚠ |
| 命名一致性: "12-level" vs "17-level" | **多文档矛盾** | `task9.md:1` 标题 "12-level";`task9.md:27,150,192,523` 称 "17 级";spec §0.1:200 "17 级",spec §13 标题 "12 级",spec §13 表 17 行(Step 0-16) | ⚠ |
| Stub-first 模式应用 | **task10 未应用** (跳过 Step 0 stub) | `task10.md:18` Step 1 直接写集成测试;主 plan "TDD 执行约定" 要求每 task Step 0 stub | ⚠ |
| asyncio.to_thread (M6 trade-off) | **未应用** | `task10.md:225` 用 `asyncio.get_event_loop().run_in_executor`;主 plan 实施时已知 trade-off 表 "M6: 用 asyncio.to_thread 替代" | ⚠ |
| 单测覆盖率目标 95% (chunker) | spec §9.1, task9 14 tests 但 `test_overlap_preserved` / `test_markdown_header_inheritance` 弱断言 | `task9.md:103-114,76-82` | ⚠ |

---

## 3. 发现清单(按严重度降序)

### 🔴 P0 — 必须修复(阻塞)

#### **[task9]** `_step_headings` 实际无法识别标题边界
- **位置**: `task9.md:262-282`(`_step_headings` 实现)
- **根因**:
  - `_HEADING_LINE_RE = re.compile(r"^#{1,5}\s", re.MULTILINE)` (`task9.md:188`) 只匹配 `#` 后的空格,不含标题文本。
  - 调用 `sections = _HEADING_LINE_RE.split(text)` 会在 `#` 与标题之间切分,得到的 `sec` 元素是 `"A\n\n"`、`"B\n\nbody"` 这种**以标题文本开头**的串,不再以 `#` 开头。
  - 随后 `if _HEADING_LINE_RE.match(sec)` 全部返回 `None` → `heading_path` 始终为空 → `parent_title` 始终为 `""`。
- **影响**: 对任何带 markdown 标题的文档,标题继承失效,所有 chunk 退化为 caption 路径切分。`test_markdown_header_inheritance` (`task9.md:76-82`) 弱断言 `any("# A" in c or "## B" in c for c in chunks)` 在切分失败时仍可命中(标题文本本身未被移除),提供**伪绿**。
- **建议**:
  - 方案 A: 改用 `_HEADING_RE` 全模式做 split,即 `re.compile(r"^(#{1,5})\s+.+$", re.MULTILINE)`,保留标题完整性,或用 `re.finditer` 找位置再切片;
  - 方案 B: 直接消费 `extract_structure` (`task8`) 的 `heading_tree` 作为输入,避免重复解析(更彻底,见 P0-2)。

#### **[task8 ↔ task10]** `extract_structure.heading_tree` 被当作层级栈消费,但实现是扁平 list
- **位置**:
  - 定义 `task8.md:160-172`(`extract_structure`): `headings` 是 `list[Heading]`,且 `Heading.children` 字段从未被填充 → 实际返回扁平数组。
  - 消费 `task10.md:213-218`:
    ```python
    heading_path: list[Heading] = list(structure.heading_tree) if structure.heading_tree else []
    parent_title = " > ".join(h.text for h in heading_path) if heading_path else ""
    ```
- **影响**:
  - 整篇文档的所有 heading 文本被一次性 join → `parent_title` 形如 `"A > B > C > D"` (10 个标题) 写入每个 chunk。
  - task14 `parent_doc.py` (`task14.md:395-453`) 按 `parent_title` 拉兄弟 chunk,会拉回整篇文档的兄弟 → 上下文窗口污染。
  - 检索时按 `parent_title` 过滤会失效,过滤意义完全丧失。
- **建议**:
  - **首选**: task9 自身用 `extract_structure` 的输出,**取消** `_step_headings` 重复解析;chunk 边界由结构驱动(每个标题即一个 section 起点)。
  - **次选**: task8 修 `extract_structure` 真正构造嵌套 tree,然后在切片时按 chunk 在原文的 offset 找到该 chunk 所属的最深 heading 作为 `parent_title`。
  - **最差**: task10 改为传 `structure.heading_tree[0]` (首个 H1) 作为整文档 `parent_title`(语义退化但可控),并删除 task14 的 sibling 扩展直到 tree 修复。

#### **[task10 ↔ task17]** CLI 完全绕开 `ingest_directory`,B9 全部修复在 CLI 路径无效
- **位置**:
  - 定义 `task10.md:284-356` `IngestPipeline.ingest_directory`(并发 + 失败隔离 + IngestResult)。
  - CLI `task17.md:188-198`:
    ```python
    files = sorted(p for p in path.rglob("*") if p.is_file())
    total = len(files)
    for i, f in enumerate(files, start=1):
        count = await pipeline.ingest_file(f, ds_uuid)
        typer.echo(f"[{i}/{total}] {f.name} → {count} chunks")
    ```
- **影响**:
  1. **无并发**:`for ... await` 串行;16 路 `LLMSemaphore` 形同虚设(每次只有 1 个文件在跑)。
  2. **无失败隔离**:第一个文件失败即 raise,后续文件不再处理;`ingest_directory` 设计的 `IngestResult.failed` 永不出现。
  3. **过滤规则漂移**:CLI 自己用 `f.is_file()`,没复刻 `SUPPORTED_SUFFIXES` (`task10.md:148`);`.DS_Store` / 二进制混入会触发 reader 抛错。
- **建议**:
  - task17 CLI 改为 `result = await pipeline.ingest_directory(path, ds_uuid); typer.echo(f"Ingested {len(result.ingested)} failed {len(result.failed)}")`,进度走 `logger.info` (已有)。
  - 若必须保留 `for` 循环(便于逐文件 typer.echo),把 `try/except` 包到 `ingest_file` 调用,失败写到本地日志而非中断。

---

### 🟠 P1 — 应当修复(影响正确性 / 性能 / 可维护性)

#### **[task10]** Embed 调用位于 DB 事务内,持锁 10s+
- **位置**: `task10.md:233-260`
  ```python
  async with session.begin():
      await repo.delete_by_filename(dataset_id, filename)
      # ... embed batch × N (可能 30+ 次 aembed_documents 调用) ...
      await repo.bulk_insert(...)
  ```
- **影响**:
  - 单个文件 1000 chunks → 32 个 embed batch → 按 2s/batch 估算,事务持锁 60s+。
  - 同时其他协程对该 dataset 的 `delete` / `insert` 阻塞。
  - PG 的 `idle_in_transaction_session_timeout` 默认无上限,长事务会撑爆 `pg_stat_activity`。
- **建议**:
  - **预计算**: 在 `session.begin()` 之外先跑全部 embed + 在内存建好 `models: list[ChunkModel]`,**只**把 delete+insert 放在事务内(理想 ≤ 1s)。
  - **或** 用 staging 表:insert to `chunks_staging` → 一次性 `INSERT ... SELECT ... FROM chunks_staging`(避免大 bulk insert 在事务内分批)。

#### **[task10]** Embed 失败重试时重复计算,未使用 L1 embed 缓存
- **位置**: `task10.md:241-248`,事务回滚后,异常 `raise`;操作员手动 re-ingest 时(chunker 给出相同 chunk 文本),embed 会**全量重跑**,不计费但计延迟。
- **影响**: 上游 LLM 临时 502 恢复后,re-ingest 同样 N chunks 仍需 N × 2s。
- **建议**:
  - ingest 路径增加 `rag.emb_cache` (task6 L1 已有此 layer, `rag:emb:{model}:{provider_version}:{hash}` TTL 24h) 包装,失败重试时按 chunk_text hash 命中。
  - 在 `EMBED_BATCH` 循环中:先查 cache → 未命中调 API → 写 cache → 进入 `embedded`。
  - 注:embed cache 写入是 io,可在事务外完成,事务内只读。

#### **[task9]** `_split_section` 软切使用 `_CAPTION_SEPS` 而非完整 `soft_seps`,英文 body 切不开
- **位置**: `task9.md:303-305`
  ```python
  sub_pieces = self._split_by_seps(body, ["\n\n", "\n", "。", "？", "！", "；", "，"])
  ```
- **影响**: 中英混排语料,英文 body 内的 `. ` / `! ` / `? ` / `; ` / `, ` 不会触发切分,英文段落可能撑爆 `chunk_size`。
- **建议**: `_split_section` 也使用 `self.s.soft_seps`,或者额外定义 `SECTION_SEPS` (caption + english) `["\n\n", "\n", "。", ". ", "？", "? ", "！", "! ", "；", "; ", "，", ", "]`,与 spec §13 步骤 9-15 对齐。

#### **[task9]** `test_overlap_preserved` 是退化断言
- **位置**: `task9.md:103-113`
  ```python
  text = "。".join([f"段落{i}的内容在这里" for i in range(20)])
  chunks = Chunker(s).split(text)
  if len(chunks) >= 2:
      ...
  else:
      # 宽松断言
      overlap_chars = set(tail) & set(head)
      assert len(overlap_chars) > 0 or len(chunks) <= 2
  ```
- **影响**: 文本 20 短句(每句 ~8 字符) + `chunk_size=100` → 实际产出 1 个 chunk → 测试直接通过 `len(chunks) <= 2` 分支,overlap 实现**永远不被覆盖**。
- **建议**: 文本改为 `f"长段落{i} " * 50` ≈ 200+ 字符每段,确保 `chunk_size=100` 触发多 chunk 路径;并 hard assert 相邻 chunk 字符级重叠 (e.g. `assert chunks[0][-15:] in chunks[1]`)。

#### **[task9]** `test_markdown_header_inheritance` 弱断言,与 P0-1 一起失效
- **位置**: `task9.md:76-82`,`any("# A" in c or "## B" in c for c in chunks)`。
- **影响**: 即使 P0-1 的 heading 解析 bug 让 `parent_title=""`,标题文本仍可能出现在某个 chunk 内容里(因为 chunker 用 `text.split(sep)` 切分时没移除文本),测试给出伪绿。
- **建议**: 改为强断言: `parent_title = "1:A"` (顶级 H1) 必须出现在每个 chunk 的**前缀位置**,例如 `assert all(c.startswith("# A") for c in chunks)`。

#### **[task10]** 软删除 vs 硬删除决策未在 task10 docstring 显式声明
- **位置**: `task10.md:166-176` 提到 "整批替换" 但未引用 spec §10;`task10.md:225-263` 的 `delete_by_filename` 是硬删。
- **影响**: spec §10 明确"整 dataset 删除**不**软删除(无 archived 字段,数据可恢复性差)"。task10 文件级删除同样硬删,但 `IngestResult.failed` 中失败的 chunk 已被清空 → re-ingest 前如果忘记 dataset 级备份,数据丢失。
- **建议**: 在 `IngestPipeline.__init__` docstring 引用 spec §10,明确"硬删是预期行为";考虑加 `soft_delete: bool = False` 选项,默认 `False` 但允许 dataset 配置覆盖(本期可 stub)。

#### **[task10]** `asyncio.get_event_loop().run_in_executor` 与主 plan M6 trade-off 冲突
- **位置**: `task10.md:225`
- **影响**: 主 plan "实施时已知 trade-off" 表 M6 明确要求改用 `asyncio.to_thread` (Python 3.12+ 推荐)。task7 嵌入式注释也已写明。
- **建议**: 改为 `chunks = await asyncio.to_thread(self.chunker.split, text)`。

---

### 🟡 P2 — 建议改进(可读性 / 一致性 / 文档)

#### **[spec ↔ plan ↔ task]** "12-level" vs "17-level" 命名混乱
- **证据**:
  - 主 plan `2026-06-10-python-rag-pipeline.md:136` "Chunker (12-level recursive split)"
  - 主 plan 同文件 `:196` trade-off 表 "17 级完整复刻 FastGPT"
  - INDEX `tasks/INDEX.md:21` "12-level recursive split"
  - task9 标题 `:1` "12-level",同文件 `:27,150,192,523` "17 级"
  - spec `2026-06-10-python-rag-pipeline-design.md:200` "17 级分隔符递归切",同文件 `:311,632,1260,1499,1503,1529` "12 级"
  - spec §13 标题 "12 级分隔符详细说明" 但表 17 行 (Step 0-16)
- **影响**: 跨文档口径不一,易让 reviewer / implementer 误判"12 步"或"17 步"。
- **建议**: 统一为 **"17 步"** (Step 0-16),其中 soft seps 占 12 个具体分隔符;spec §13 标题改为 "Chunker 17 步分隔符表(soft seps 12 个)";主 plan 与 INDEX 同步。

#### **[task9]** `if False else` 死代码未清理
- **位置**: `task9.md:272` `heading_path = [h for h in heading_path if _heading_level(h) < level] if False else heading_path`。
- **影响**: refactor 残留,降低可读性,且类型不一致(head 是 `"{level}:{title}"` 字符串,`Heading` dataclass 已定义却未用)。
- **建议**: 删掉该行,仅留 `heading_path = _push_heading_path(heading_path, level, title)`。

#### **[task9]** `_HeadingNode` dataclass 定义但未使用
- **位置**: `task9.md:508-513`
- **影响**: 死代码,实际栈用 `str` (`"{level}:{title}"`)。
- **建议**: 删 `_HeadingNode`,或将栈类型统一为 `_HeadingNode` (强类型更佳)。

#### **[task10]** Step 0 stub 缺失
- **位置**: `task10.md:18` 直接 Step 1 写集成测试,无 stub 占位。
- **影响**: 测试运行时会触发 `ImportError: cannot import name 'IngestPipeline' from 'rag.ingest.pipeline'` 崩溃(虽然集成测试启动 testcontainers 在前,可能掩盖)。违背主 plan TDD 约定。
- **建议**: 增加 Step 0 stub `class IngestPipeline: async def ingest_file(self, *a, **k): raise NotImplementedError; async def ingest_directory(self, *a, **k): raise NotImplementedError`。

#### **[task10]** CLI `ingest` 命令未做 `dataset_id` 校验
- **位置**: `task17.md:177-200` 接受 `--dataset-id=<uuid>`,但未校验该 UUID 在 `datasets` 表中是否存在。
- **影响**: 误传不存在的 UUID 时,`ingest_file` 走到 `bulk_insert` 才报 FK violation,事务回滚留下空清理结果(`delete_by_filename` 也是 0 rows)。
- **建议**: pipeline 入口加 `SELECT 1 FROM datasets WHERE id = :id` 预检,失败抛 `DatasetNotFoundError`;CLI 把异常 message 直接显示给用户。

#### **[task9]** 默认 `soft_seps` 中 `. ` 与中文标点并列,纯英文文档场景下中文 sep 是 dead ops
- **位置**: `task9.md:160-171` 12 项;英文语料用 `text="Hello. World. Foo."` 时 `。` 切分无效(noop),不影响结果但多 N 次 `pieces = [p]; new_pieces.extend(p.split(sep))` 循环。
- **影响**: 微小性能开销(12 次 split × 1000 docs)。
- **建议**: 已知 trade-off,接受;但 audit #2 P1-7 已加 `soft_seps` 字段,文档化应在 docstring 写"纯英文语料建议移除中文标点"。

#### **[task10]** `IngestResult` 用 `tuple[Path, str]` 但 `ingested` 用 `list[str]`
- **位置**: `task10.md:152-164` `failed: list[tuple[Path, str]]` vs `ingested: list[str]`。
- **影响**: 类型不一致,消费者需要分别处理 `Path` 和 `str`。
- **建议**: 统一为 `list[tuple[Path, str]]` 或 `list[Path]`,取一。

#### **[task10]** 进度反馈 "ingest 进度" 字段在 `logger.info` 而非 callback,CLI 集成有断点
- **位置**: `task10.md:222,346,354` 用 `logger.info("[ingest] [%d/%d] %s → %d chunks", ...)`。
- **影响**: 调用方(CLI / API server / background worker)拿不到结构化进度,只能 `tail -f` 日志。
- **建议**: 考虑加 `progress_callback: Callable[[int, int, str, int], None] | None = None` 参数,默认 `None` 时走 logger,这样 CLI 可以接到 `typer.echo`。

---

### 🟢 P3 — 微小 / 锦上添花

#### **[task9]** `Chunker.split` 入口守卫 `if not text or not text.strip(): return []` 写在了 `split` 内,没在 `chunk_table` 等内部 step 中覆盖
- **位置**: `task9.md:206-208`
- **影响**: 理论上 `text=""` 经 step 0-7 后所有 step 都收到 `""`,`_step_code_blocks` 会把空 parts 转 `remaining=[]`,OK。但健壮性可加强。
- **建议**: 保留现状,但补单测覆盖 "caption + 空" 组合。

#### **[task10]** `parent_title` schema 默认 `""` 与 task14 `get_siblings` 行为: 空 parent_title 会拉所有 chunk
- **位置**: `task3.md:198` `parent_title: Mapped[str] = mapped_column(Text, default="")`;`task3.md:270-282` `get_siblings` 按 `parent_title == parent` 过滤。
- **影响**: 若 `parent_title=""` (P0-2 bug 时常态),`get_siblings(dataset, "", 0, 100)` 会返回该 dataset 全部 chunk → 上下文爆炸。
- **建议**: task14 `parent_doc.py` 加 `if not parent_title: return []` 守卫(本期若 task14 还没实现可后置)。

#### **[task9]** `paragraph_chunk_deep: int = 5` 字段在 spec 列出但 task9 实现中**未使用**
- **位置**: spec §2 defaults,`task9.md:174` `ChunkSettings` 有字段,`task9.md:230-410` `_step_headings` 实际递归深度只到 H1-H5(由 `_HEADING_RE = re.compile(r"^(#{1,5})\s+(.+)$", re.MULTILINE)` 决定),与 `paragraph_chunk_deep` 无关。
- **影响**: 字段无作用,误导读者。
- **建议**: 删字段,或在 docstring 说明"保留对齐 FastGPT API,本期不消费"。

---

## 4. Spec 覆盖矩阵

| Spec 章节 | 覆盖 Task | 完整性 | 偏差说明 |
|-----------|-----------|--------|----------|
| §0.1 ingest 侧全景图 | task8 + task9 + task10 | **70%** | 全景图行 200 说 "17 级",spec §13 标题 "12 级",口径不一(P2-1) |
| §2 默认值 (chunk_size=1000 等) | task9 | 90% | `paragraph_chunk_deep=5` 字段定义但未消费 (P3-3) |
| §3 数据模型 (Chunk, ChunkMetadata) | task2 + task3 | OK | task10 消费 ChunkModel 字段顺序与 task3 一致 |
| §6.5.3 文档结构提取 | task8 | **60%** | 返回 `heading_tree: list[Heading]` 字段但 `Heading.children` 从未填充,实际是扁平 list (P0-2) |
| §6.5.4 增量更新与原子性 | task10 | **80%** | 整批 DELETE+INSERT 在事务内 OK;cache invalidation 提交后调用 OK;但 embed 在事务内(P1-1)违反 "cache invalidation 提交后" 的精神,扩展到 "embed IO 也在事务内" 更糟 |
| §8.8 ingest 异步化 (producer-consumer) | task10 | **60%** | 实现了 batch 化的 embed + insert;但 `get_event_loop().run_in_executor` 与 M6 trade-off 冲突;`run_in_executor` 调用在事务内而非 pipeline 入口 |
| §10 整批替换策略 | task10 | 95% | DELETE + INSERT 顺序与 spec 一致;docstring 未引用 spec §10 (P1-7) |
| §13 Chunker 17 步分隔符表 | task9 | **75%** | 17 步实现齐全;但 `test_markdown_header_inheritance` 弱断言 (P1-6);`_split_section` 软切用 `_CAPTION_SEPS` 缺英文 (P1-5);`_step_headings` 实际 bug (P0-1) |
| §9.2 单元测试关键点 (chunker) | task9 | 80% | "12 级分隔符" "标题继承" "代码块保护" "overlap 比例" "min/max" "末块合并" 6 个 key point 覆盖 5 个;overlap 退化断言 (P1-6) |
| §9.5.1 L1 组件级评测 (chunker 长度分布) | **未覆盖** | 0% | 任务范围内无 L1 eval 任务;`task18.md` Eval L2 起步;L1 chunker 质量评测未列入任何 task(应在 task18 之前补 task) |

---

## 5. 架构风险与建议

- **风险 1 (高)**: `extract_structure` 平面化 + 消费者误用 = 整个 `parent_title` 体系失效
  - 缓解: P0-2 修复;同时 task14 `parent_doc.py` 加 `if not parent_title: return []` 防御。

- **风险 2 (高)**: Heading 解析 bug 让所有 markdown 文档的标题继承失效
  - 缓解: P0-1 修复;补 `test_step_headings_extracts_correct_path` 强断言:`# A / ## B / ### C` 文档,所有 `### C` 下 chunk 必须有 `parent_title` 含 `"C"`,且不被 `## D` 章节污染。

- **风险 3 (中)**: 事务内 embed 持锁时间长,影响并发 ingest
  - 缓解: P1-1 修复(预计算 embed,事务内只做 delete+insert)。

- **风险 4 (中)**: CLI 与 `ingest_directory` 双实现,行为漂移
  - 缓解: P0-3 修复,CLI 改调 `ingest_directory`;或 task10 删 `ingest_directory` 简化(但失去 B9 修复价值)。

- **风险 5 (中)**: 命名 "12-level" vs "17-level" 导致 onboarding 误判
  - 缓解: P2-1 统一口径,推荐 "17 步 (soft seps 12 项)"。

- **风险 6 (低)**: Spec §0.1 与 spec §6.5.3 在 chunker 路径上存在功能重叠(structure 已解析 heading,chunker 又解析)
  - 缓解: 长期重构 task9 不再独立解析 heading,完全消费 `extract_structure` 输出。

- **风险 7 (低)**: 大文件无 size guard,1GB 文件 OOM
  - 缓解: `read_file` 阶段加 `MAX_FILE_SIZE` 检查;或在 `_soft_split` 之前先按 `text` 长度预警。

- **风险 8 (低)**: 中文 + 英文符号双写(`. ` 与 `。`)造成 soft_seps 列表 12 项,但 `DEFAULT_SOFT_SEPS` 命名暗示"可裁剪",用户裁剪时需清楚知道每个 entry 的含义
  - 缓解: docstring 加示例 `ChunkSettings(soft_seps=[("\n\n",True),("\n",True),("。",True),(", ",True)])`。

---

## 6. 跨 Task 一致性核查

| 契约 | 出 | 入 | 状态 |
|------|----|----|------|
| `Heading` 数据结构 | `task8.md:140-144` 字段 `level / text / line / children` | `task9.md:265-281` 用 `"{level}:{title}"` 字符串栈(自创格式);`task10.md:213-218` 当 `Heading` 列表栈用 | 🔴 三方对同一概念三种表示 |
| `extract_structure` 形状 | `task8.md:162-172` 实际返回**扁平** list | `task9.md` 独立解析;`task10.md:213-218` 假装嵌套 | 🔴 见 P0-2 |
| `IngestPipeline.ingest_directory` | `task10.md:284-356` 存在,带并发+失败隔离 | `task17.md:188-198` CLI **不调用** | 🔴 见 P0-3 |
| `IngestPipeline.ingest_file` 返回 `int` | `task10.md:189-264` 返回 `len(embedded)` | `task17.md:189,196` 期望 `int` | OK |
| `LLMSemaphore.run(provider, coro)` | `task7.md` semaphore.py | `task10.md:241` 显式注释 `LLMSemaphore.run() guard` | OK |
| `on_chunks_changed(dataset_id: UUID)` | `task6.md:409` | `task10.md:262` 调用 | OK |
| `build_tsvector(text) -> str` | `task5.md:75-78` 返回空格 join 的 token 字符串 | `task10.md:256` 赋值给 `ChunkModel.ts_tokens: TSVECTOR` | ⚠ SQLAlchemy TSVECTOR 类型 + 字符串值的隐式 cast 行为依赖 driver,需 task3 的 `db_session` 集成测试覆盖 |
| `ChunkModel` 字段顺序 | `task3.md:189-209` `id / dataset_id / text / modality / image_path / parent_title / chunk_index / filename / embedding / ts_tokens` | `task10.md:244-260` 构造时按 `dataset_id / text / parent_title / chunk_index / filename / embedding / ts_tokens / modality` | OK(SQLAlchemy 关键字参数不要求顺序) |
| `parent_doc_window` 在 dataset / search 域 | spec §0.1 / `task2.md:131` `SearchRequest.parent_doc_window: int = 0` | `task10.md` ingest 不消费 (正确,仅检索侧用) | OK |
| `custom_separator` 在 chunker / domain | `task2.md:70` `ChunkMetadata.custom_separator: str \| None = None`;`task9.md:175,177` `ChunkSettings.custom_separator: str \| None = None` | `task10.md:225-263` **未把 `custom_separator` 写入 `ChunkModel`** (无对应列) | ⚠ domain ↔ chunker 已对齐;ingest 写入 DB 时丢弃 (本期 spec 不要求落库,可接受) |
| `Dataset.chunk_size` (spec §3 `chunk_size: int = 1000`) | `task3.md:174` `DatasetModel.chunk_size: Mapped[int] = mapped_column(Integer, default=1000)` | `task10.md:175` `self.chunker = Chunker(ChunkSettings())` 用了全局默认,没读 dataset.chunk_size | 🔴 dataset 配 chunk_size=500 时,ingest 仍按 1000 切,数据写错 |
| `Dataset.embed_dim` | `task3.md:172` `embed_dim: Mapped[int]`,默认未设 (任务测试手动传 1536 / 3) | `task10.md:244-260` `embedding=vec` 直接写,未校验 dim 是否匹配 | ⚠ 与 model 字段顺序一致,但 ingest 缺 dim 校验 |
| 命名: "12-level" / "17-level" | 多文档 | 多文档 | ⚠ 见 P2-1 |
| 异步风格 (Python 3.12 asyncio.to_thread) | 主 plan M6 trade-off | `task10.md:225` `run_in_executor` | ⚠ 见 P1-9 |

---

## 7. 三条具体建议

1. **(执行 P0-1 + P0-2) 立刻合并 heading 解析到 `extract_structure`**:让 task8 的 `extract_structure` 真正构造嵌套 tree(填充 `Heading.children` 递归),task9 取消 `_step_headings` 整段实现,改为消费 `structure.heading_tree` + 在原文 offset 找每个 chunk 的最深深标题作为 `parent_title`。这同时修复 P0-1 (heading 解析 bug)、P0-2 (扁平 vs 嵌套)、P1-6 (弱断言) 三个问题。

2. **(执行 P0-3) CLI 改调 `pipeline.ingest_directory`**:task17 删 `for i, f in enumerate(files, start=1)` 循环,改为 `result = await pipeline.ingest_directory(path, ds_uuid)`;进度走 logger;失败统计用 `typer.echo(f"Ingested {len(result.ingested)}/{total} files; {len(result.failed)} failed")`。这样 B9 修复在 CLI 路径生效,与 task16 `build_full_pipeline` 集成时一致。

3. **(执行 P1-1 + P1-3) 把 embed 移出事务并加 L1 cache 复用**:`ingest_file` 主体顺序改为:①读文件+chunk(线程池)、②embed 全部 chunks(LLMSemaphore 限流,逐 chunk 查/写 L1 embed cache)、③开事务做 `delete + bulk_insert`、④提交后 `on_chunks_changed`。事务持锁从 60s+ 降到 1s 内,失败重试时已 cached 的 chunk 不再付费。
```

> 由于沙盒强制 read-only 模式,文件无法落入 `docs/superpowers/plans/reviews/agents/agent5_chunker_ingest.md`。上述 fenced markdown 可直接复制到该目标路径(已包含完整结构、文件:行号引用、修复建议)。如需我重写为更紧凑格式或聚焦特定子项,告知即可。