# Test Plan (2026-06-14)

> **Status:** ACTIVE — referenced by `2026-06-14-rag-pipeline-delivery.md` §5 and §6.
> **Scope:** M1-M4 实施期间所有测试相关的目标、范围、启动条件。
> **Owner:** Each task author (M1-M4) is responsible for landing the tests defined here.

---

## 1. 覆盖率目标

### 1.1 Global Gate

| 指标 | 目标 | 阻断 CI? |
|---|---|---|
| Line coverage | **≥ 80%** (project-wide) | **是** (PR 阻断) |
| Branch coverage | **≥ 70%** (project-wide) | 是 |
| Total uncovered lines | < 200 | 否 (warning) |

参考 spec §9.1。`pyproject.toml` 用 `[tool.coverage.report] fail_under = 80` 强制。

### 1.2 Module-specific 目标(4 个)

| 模块 | 目标 | 阻断? | 验收里程碑 |
|---|---|---|---|
| `rag.retrieval.citation_check` | ≥ 95% | 是 | M2 末 (5e) |
| `rag.eval.retrieval_metrics` | ≥ 90% | 是 | M4 末 (5h) |
| `rag.eval.ragas` | ≥ 85% (含 mock) | 是 | M4 末 (5i) |
| `rag.pipeline.fusion` | ≥ 90% | 是 | M1 末 (5a) |

参考 spec §9.1 提到的"4 个核心模块单独目标"。`pyproject.toml` 用 `[tool.coverage.run] source = ["rag"]` + per-module allowlist。

### 1.3 不计入覆盖率的文件

```
# pyproject.toml [tool.coverage.report] exclude_lines
- pragma: no cover
- raise NotImplementedError
- if __name__ == .__main__.:
- if TYPE_CHECKING:
- \.\.\.
```

文件级 excludes:
- `src/rag/cli/*.py` (CLI 入口,主要靠 CLI 集成测试覆盖)
- `src/rag/infra/llm/clients/*.py` (外部 SDK wrapper,靠 mock 覆盖)

---

## 2. 单元测试范围(per contract)

9 个 Contract 各自有一组**强制 test contract**,实施时必须落地。

完整 contract → test 映射见 `.agents/design/2026-06-14-cross-task-contracts.md` §"Test contracts summary"。下表是 test 文件归属:

| Contract | Test 文件 | 最小 test 数 | 验收里程碑 |
|---|---|---|---|
| 1 `intra_fusion` | `tests/unit/test_fusion.py` | 8 | M1 (5a) |
| 2 `score_breakdown` | `tests/unit/test_document.py` | 2 | M1 (5a) |
| 3 `pipeline.ainvoke` | `tests/integration/test_full_pipeline.py` | 1 | M3 (5f) |
| 4 `SearchResult.response` | `tests/unit/test_search_result.py` | 2 | M1 (前置改动) |
| 5 inline citation | `tests/unit/test_citation.py` | 2 | M2 (5d) |
| 6 `_intermediate_hits` | `tests/unit/test_search_result.py` | 2 | M1 (前置改动) |
| 7 `with_cache` removed | (无 test, 仅 absence) | 0 | M3 (5f) |
| 8 stage ordering | `tests/integration/test_pipeline_ordering.py` | 2 | M3 (5f) |
| 9 `QueryDecomposer` dropped | (无 test, 仅 absence) | 0 | M2 (5c) |

**合计:** 19 个强制 test contract,跨 6 个 test 文件。

---

## 3. 集成测试范围

### 3.1 范围(5 类)

| 类别 | 文件 | 范围 | 启动方式 |
|---|---|---|---|
| Ingest e2e | `tests/integration/test_ingest.py` | Reader → Normalizer → Chunker 全链路,8 种 source (File/Url/Buffer/Api) | pytest |
| Retrieval e2e | `tests/integration/test_retrieval.py` | fusion + filter + query_ext,真实 PG + Redis(testcontainers) | testcontainers |
| Pipeline e2e | `tests/integration/test_full_pipeline.py` | `build_full_pipeline` happy path with fake LLM/embed/cache | pytest |
| Eval e2e | `tests/integration/test_eval.py` | EvalRunner 跑 5 metric,real goldset | pytest |
| CLI e2e | `tests/integration/test_cli.py` | typer 6 subcommand,subprocess 调用 | subprocess + assert exit code |

### 3.2 testcontainers 依赖(M3 起)

```toml
# pyproject.toml [project.optional-dependencies.dev]
testcontainers = {extras = ["postgres", "redis"], version = "^4.0"}
```

启动方式:
```python
# tests/conftest.py
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer

@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg.get_connection_url()

@pytest.fixture(scope="session")
def redis_container():
    with RedisContainer("redis:7-alpine") as r:
        yield r.get_connection_url()
```

CI 启动时间: ~10-15s per container(预热一次,session scope 复用)。

### 3.3 Fake 实现(用于 5f e2e,免 testcontainers)

```python
# tests/integration/_fake.py
class FakeEmbed:
    async def aembed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]

class FakeLLM:
    async def achat(self, messages: list[dict]) -> str:
        return "This is a fake answer [1](CITE)."

class NoopCache:
    async def get(self, key, layer, warnings) -> Any: return None
    async def set(self, key, value, ex, layer, warnings) -> None: pass
```

e2e 用 fake 跑,5f 落地 1 个 happy path test 验证 `Pipeline.ainvoke(SearchRequest) → SearchResult` 全链路通畅。

---

## 4. Eval 启动条件(L4 / L2 / L3 何时算"达成")

### 4.1 L2 检索级评测(对应 task 18)

**达成条件(M4 末):**
- 5 metric 函数全部实现 + ≥1 test each
- `EvalRunner.run(goldset, pipeline) → EvalReport` 工作
- Gold set 至少 50 条 query(非空,chunk 级别 + entity 级别)
- 跑通 1 次端到端,产出可读 `EvalReport`(json)

**未达成条件(不阻断 M4):**
- Gold set 100 条
- 多 dataset 评估
- 显著性检验

### 4.2 L3 生成级 RAGAS(对应 task 19)

**达成条件(M4 末):**
- RAGAS wrapper 实现,judge model 从 settings pin
- `faithfulness` / `answer_relevance` / `context_precision` / `context_recall` 4 个 metric 跑通 mock judge
- `result.response` (而非 `result.prompt`) 传入 RAGAS(per C4)
- `jaccard(t, t-1)` + `compare_results` baseline 对比工作
- 1 个 mock e2e test 跑通

**未达成条件(不阻断 M4):**
- 真实 OpenAI judge 跑通
- 真实 goldset 50 条
- 显著回归自动告警

### 4.3 Regression Testing(对应 task 19 + 20)

**达成条件(M4 末):**
- `eval/regression.py` 实现 `jaccard` + `compare_results`
- CI weekly cron(`.github/workflows/ci.yml`)跑 RAGAS regression
- Threshold ±0.05 触发告警(非阻断)
- 上一次 baseline 存 `.eval_baseline.json`

**未达成条件:**
- 真实阈值调优(prod 数据反馈)
- 多 baseline 对比

### 4.4 L4 用户级评测

**不做**(v3 推迟,见 delivery plan §8)。

---

## 5. 已知不测的(明确边界)

| 类别 | 不测原因 | 替代 |
|---|---|---|
| LLM 输出质量(语义正确性) | 主观,无法 unit test | L3 RAGAS + 人工 spot check |
| 真实 OpenAI judge 一致性 | 成本 + 不稳定 | L3 mock + 真实数据小样本对比 |
| 多模态 end-to-end | image_caption 未落地 | 暂不测 |
| pgvector 索引性能 | 调优,非功能 | 跑 benchmark,见 spec §15 |
| FastGPT API 兼容 | rag-pipeline 是 library,非 app | OpenAPI 暴露,client 自测 |

---

## 6. CI Workflow 矩阵(对应 task 20)

6 阶段 workflow,触发条件和目的:

| 阶段 | 触发 | 内容 | 阻断? | 估计时长 |
|---|---|---|---|---|
| lint | push to PR | ruff + mypy strict + format check | **是** | 30s |
| unit | push to PR | pytest tests/unit/ + coverage gate 80% | **是** | 1-2 min |
| integration (testcontainers) | push to PR | pytest tests/integration/ with PG/Redis container | **是** | 3-5 min |
| full-eval | on-merge to main | EvalRunner 跑 goldset(50 query) | 否 (post-merge) | 5 min |
| ragas-weekly | weekly cron (Monday 02:00 UTC) | RAGAS regression + jaccard vs baseline | 否 | 10 min |
| pre-release | on tag | full e2e + perf benchmark + audit log check | **是** | 15 min |

参考 spec §9.6 "Eval 时机矩阵" + §9.8 CI。

### 6.1 `concurrency:` block(节省 runner-minute)

```yaml
# .github/workflows/ci.yml
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

Superseded PR 自动取消,避免资源浪费(per audit task 20 P1-3)。

---

## 7. Mock 与 Fixture 库

`tests/conftest.py` 提供:

| Fixture | 作用域 | 说明 |
|---|---|---|
| `pg_container` | session | testcontainers PostgresContainer |
| `redis_container` | session | testcontainers RedisContainer |
| `fake_embed` | function | `FakeEmbed` 实例 |
| `fake_llm` | function | `FakeLLM` 实例 |
| `noop_cache` | function | `NoopCache` 实例 |
| `pipeline_deps` | function | `PipelineDeps` with all fakes |
| `goldset_path` | session | `tests/eval/goldset.jsonl` 路径 |
| `search_request_factory` | function | 生成 `SearchRequest` 测试实例 |

---

## 8. 验证命令(CI 本地复现)

```bash
# Lint
make lint                                  # ruff check + ruff format --check + mypy

# Unit + coverage
make test                                  # pytest tests/unit/ -v --cov=rag --cov-report=term-missing

# Integration (需要 docker)
make test-integration                      # pytest tests/integration/ -v

# Eval
make eval                                  # python -m rag.eval.eval_runner --goldset tests/eval/goldset.jsonl

# RAGAS weekly (本地复现)
make ragas                                 # python -m rag.eval.ragas --baseline .eval_baseline.json

# Coverage report
make coverage                              # coverage html + open
```

`Makefile` 是 task 20 5j 的产物;`make` 不可用时,直接用上方 `python -m ...` 命令。

---

## 9. 与 9 Contract 的对应

每个 contract 都有 test contract(在 design note §"Test contracts summary"中),实施时:

1. **先写 test**(TDD 严格)
2. **test 引用 contract**(用 docstring 写明 `# per cross-task-contracts.md Contract N`)
3. **CI 跑 test**(见 §6)
4. **覆盖率检查**(§1)

**违反 contract 的 test = 改 test**(contract 不动;若 contract 真错了,改 contract 必须更新 design note + delivery plan §4)。

---

## 10. 不在范围内(明确)

- Property-based testing (hypothesis) — 暂不引入,如有需要 M4 后再加
- Mutation testing (mutmut) — 暂不引入
- Fuzz testing — 暂不引入
- Load testing / stress testing — v3 推迟

---

**最近更新:** 2026-06-14
**下次更新触发:** M1 末(5a/5b 落地后,更新 §2 中 5a/5b 的 test 文件路径)
