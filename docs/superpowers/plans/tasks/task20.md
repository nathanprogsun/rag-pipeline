# Task 20: CI + Final Integration + Coverage Report

**Status**: 未开始 (2026-06-14 审计重标)

## 状态: 未开始 (2026-06-14 审计重标)

> **实际交付**(`refactor/chunker-reader` 分支):
>
> - `.github/workflows/ci.yml` — on-PR `test` job + schedule (cron `0 2 * * 1` weekly RAGAS + robustness + l1_metrics)
> - `tests/conftest.py` — 全局 fixture (PG testcontainers + Redis testcontainers + sample data)
> - `Makefile` — 新增 `eval / coverage` target
> - `pyproject.toml [tool.coverage.report]` — 4 模块覆盖率目标:`rag.retrieval.lazy_greedy` 95% / `rag.retrieval.decomposition` 95% / `rag.pipeline.query_ext` 90% / `rag.retrieval.citation_check` 90%
>
> **后续 review/audit 影响 (2026-06-13 同步)**:
>
> - **PAudit-1 (chunk_repo 集成测试)**:`tests/integration/test_chunk_repo.py` 加 `bindparams + flush + transaction()` 路径测试,确保集成测试覆盖 SQL 注入防护
> - **PAudit-5 (ErrorCode 分组)**: CI 输出里 `ReaderError.code` 按域分组聚合,`tests/unit/test_rag_error.py` 新增 ErrorCode 枚举全覆盖测试
> - **PAudit-5 (pytest upper)**: CI 配置 `pyproject.toml [tool.pytest.ini_options]` 路径全用 upper,`tests/` 下文件命名规范化(`test_extensions_pptx.py` 等)
> - 当前 CI 跑通结果:无可验证交付(2026-06-14 审计重标:重构分支未交付,见下方"实际实现"段)
>
> **历史溯源**(本 task 原始描述):原 plan 写 audit #1 + subagent #5/7 修复 4 项,详见下方。原描述保留为溯源依据。

> Extracted from `/Users/jung/pro/rag-pipeline/docs/superpowers/plans/2026-06-10-python-rag-pipeline.md` (lines 4797-4875).
>
> Fixes applied:
> - **(audit #1) 完善 on-PR CI 包含新增测试文件**: on-PR `test` 步骤追加 `test_lazy_greedy.py` / `test_query_ext.py` / `test_query_decomposition.py` / `test_citation_check.py` 显式列表,以及 nightly/weekly 单独的 robustness / l1_metrics 跑通检查。
> - **(audit #1) weekly CI 加 robustness / l1_metrics**: 在 `ragas` weekly job 中加 `tests/eval/robustness.py` 和 `tests/eval/l1_metrics.py` 的执行步骤。
> - **(subagent #5 + P0-24 修复) 覆盖率目标更新**: 4 个新增源码模块的覆盖率目标写入 `pyproject.toml` `[tool.coverage.report]` 段:`rag.retrieval.lazy_greedy` 95% / `rag.retrieval.decomposition` 95% / `rag.pipeline.query_ext` 90% / `rag.retrieval.citation_check` 90%。**`rag.eval.robustness` 已从 module_targets 移除** — 它位于 `tests/eval/` 是测试入口,不是源码模块,coverage 跟踪无意义(由 CI 单独 weekly 跑通检查)。
> - **(F2 P0)** 模块路径名错修正: `rag.query.extension` → `rag.pipeline.query_ext`(实际模块路径),`rag.audit.citation_check` → `rag.retrieval.citation_check`(实际模块路径,见 task15 `src/rag/retrieval/citation_check.py`)。
> - **(subagent #7) CI 包含 `caches_have_no_stale` 集成测试**: 在 on-PR `test` job 中加 `pytest tests/integration/test_cache_invalidation.py -v -k caches_have_no_stale` 步骤,确保缓存 staleness 集成测试纳入 CI。

## Open P0s (2026-06-14 audit)

| P0 ID | 描述 | 文件:行 | 解决路径 |
|---|---|---|---|
| G-P0-1 | 状态 "已完成 (2026-06-13 同步)" 虚假 — 4 deliverable 全部 0 实现 (`.github/workflows/ci.yml` / `tests/conftest.py` 真实版本 / `Makefile` coverage/eval target / `pyproject.toml [tool.coverage.report]`); "373 unit + 19 integration passed, 80%+ 覆盖" 不可验证, `make eval` 会 `No rule to make target` | task20.md:3, 9-19, 33-36 | M4 (5j) — 改状态"未开始", 删不可验证 metrics, 实施时按 spec 落地 4 文件 + 跑通 CI 附 Actions URL 证 |
| G-P0-2 | 4 模块覆盖率目标 (`rag.retrieval.lazy_greedy` 95% / `rag.retrieval.decomposition` 95% / `rag.pipeline.query_ext` 90% / `rag.retrieval.citation_check` 90%) 全部引用 phantom 模块, `src/rag/pipeline/` 目录根本不存在; 写入 pyproject 后 coverage.py 静默忽略 (no source matches glob) | task20.md:12, 138-152 | M4 (5j) — 临时单 `module_targets = [{name = "rag", target = 80}]` floor + TODO 注释链回各 task owner; 真实模块落地后单独 PR 加 4 条 target; 加 CI 步骤 `python -c "importlib.import_module(m)"` 验证存在 |
| G-P0-3 | `tests/eval/` 引用 4 入口 (`run_ragas.py` / `robustness.py` / `l1_metrics.py` / `regression.py`) 全部不存在, weekly cron `0 2 * * 1` 触发会 ModuleNotFoundError 红 badge 一周 | task20.md:84, 105, 110, 116, 196 | M4 (5j) — Option A: 改 `workflow_dispatch` 手动触发, 不注册 cron; 或 Option B: 创建 stub 各脚本 `if __name__ == "__main__": print("TODO")` 防 FileNotFoundError |
| G-P0-4 | CI 声称 "testcontainers 自动管理容器" 但 `uv.lock` 无 `testcontainers` 包, `pyproject.toml` 仅有 `mypy.overrides` ignore list 无 `[project.optional-dependencies.dev]` 条目; `uv pip install -e ".[dev]"` 不会装 testcontainers, CI 跑 import 即 ModuleNotFoundError | task20.md:58 | M4 (5j) — 加 `testcontainers[postgresql,redis]>=4.8.0` 到 dev extras; CI step 验证可起 pgvector + redis 容器; 或决策改用 `services: postgres + redis` (GitHub Actions 原生) |
| G-P0-5 | on-PR `test` job 显式列 4 测试文件 (`test_lazy_greedy.py` / `test_query_ext.py` / `test_query_decomposition.py` / `test_citation_check.py`) 全部不存在, 每次 PR 都 `pytest file_or_dir_not_found` 退出码 2 红 | task20.md:64-69, 195 | M4 (5j) — 改 `uv run pytest tests/unit --ignore=tests/unit/test_lazy_greedy.py --ignore=tests/unit/test_query_ext.py --ignore=tests/unit/test_query_decomposition.py --ignore=tests/unit/test_citation_check.py` + 注释链回各 task; 落地后逐个移除 `--ignore` |

详细分析见 `audit/2026-06-14-task20-alignment.md` §5 (修复建议)。

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `tests/conftest.py` (覆盖最终 conftest)
- Modify: `Makefile` (增加 eval / coverage)
- Modify: `pyproject.toml` (覆盖率目标段)

- [ ] **Step 1: 写 CI workflow (L6 修正: testcontainers 自行管理容器;audit #1: 新增测试文件纳入)**

```yaml
# .github/workflows/ci.yml
name: CI
on:
  push:
  pull_request:
  schedule:
    - cron: "0 2 * * 1"  # weekly RAGAS + robustness + l1_metrics

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install uv
      - run: uv pip install -e ".[dev]"
      # testcontainers 自动启动 PG/Redis 容器 (无需 docker compose)

      # audit #1: unit 测试显式列出新增文件, 防止 glob 漏抓
      - name: Unit tests
        run: |
          uv run pytest \
            tests/unit \
            tests/unit/test_lazy_greedy.py \
            tests/unit/test_query_ext.py \
            tests/unit/test_query_decomposition.py \
            tests/unit/test_citation_check.py \
            --cov=src/rag --cov-fail-under=80

      - name: Integration tests
        run: uv run pytest tests/integration --cov=src/rag --cov-append

      # subagent #7: caches_have_no_stale 集成测试纳入 CI
      - name: Cache invalidation (no-stale) test
        run: |
          uv run pytest tests/integration/test_cache_invalidation.py \
            -v -k caches_have_no_stale

      - name: Coverage gate (unit + integration merged)
        run: uv run coverage report --fail-under=80   # H10: 合并 unit+integration 覆盖率检查

      - name: Regression suite
        run: uv run pytest tests/eval/regression.py tests/integration/test_regression.py

      - name: Lint
        run: uv run ruff check src tests

      - name: Type check
        run: uv run mypy src

  # audit #1: weekly robustness + l1_metrics 跑通
  # M5: runs-on 已修正
  ragas:
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule'
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: "3.12"}
      - run: pip install uv
      - run: uv pip install -e ".[dev]"

      - name: RAGAS L3
        run: uv run python tests/eval/run_ragas.py

      # audit #1: weekly robustness
      - name: Robustness suite
        run: |
          uv run pytest tests/eval/robustness.py -v \
            --junitxml=reports/robustness.xml

      # audit #1: weekly L1 component metrics
      - name: L1 component metrics
        run: |
          uv run pytest tests/eval/l1_metrics.py -v \
            --junitxml=reports/l1_metrics.xml
```

- [ ] **Step 1b: 更新 `pyproject.toml` 覆盖率目标 (subagent #5)**

```toml
# pyproject.toml (追加, 替换原 [tool.coverage.report] 段)
[tool.coverage.report]
# subagent #5: 5 个新增模块的覆盖率目标, 全局仍 80% floor
exclude_lines = [
    "pragma: no cover",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
precision = 1
show_missing = true
skip_covered = false

# 模块级 precision target, 与 H10 全局 --fail-under=80 协同
# 注意: 这是目标值, 不是硬阈值; CI 仍以 80% 整体 fail-under 为准
[[tool.coverage.report.module_targets]]
name = "rag.retrieval.lazy_greedy"
target = 95

[[tool.coverage.report.module_targets]]
name = "rag.retrieval.decomposition"
target = 95

[[tool.coverage.report.module_targets]]
name = "rag.pipeline.query_ext"
target = 90

[[tool.coverage.report.module_targets]]
name = "rag.retrieval.citation_check"
target = 90

# P0-24 修复 (audit #7): `rag.eval.robustness` 已移除
# 原因: 该模块位于 tests/eval/ 是测试入口,不是 src/ 下的源码模块,
# coverage report --include=src/rag 不覆盖测试目录,目标值无效。
# 改用 CI weekly 步骤显式执行 tests/eval/robustness.py + tests/eval/l1_metrics.py。
```

- [ ] **Step 2: 跑全部测试 + 检查覆盖率**

```bash
uv run pytest tests/ --cov=src/rag --cov-report=term-missing --cov-fail-under=80
# 期望: 全 pass, 整体 ≥ 80%
```

- [ ] **Step 3: 跑 lint + type check**

```bash
uv run ruff check src tests
uv run mypy src
# 期望: 0 errors
```

- [ ] **Step 4: 跑 E2E (CLI smoke test)**

```bash
docker compose up -d
sleep 5
make up
uv run python -m rag.cli.main search "test" --dataset-ids=$(uuidgen) 2>&1 | head
# 期望: 启动 + 报错 dataset 不存在 (正常)
```

- [ ] **Step 5: commit + 标记完成**

```bash
git add .github/workflows/ci.yml Makefile pyproject.toml
git commit -m "chore: CI workflow + final integration + per-module coverage targets"
```

---

## Audit Findings Applied

- **(audit #1) 完善 on-PR CI 包含新增测试文件**: on-PR `test` job 显式列出 `test_lazy_greedy.py` / `test_query_ext.py` / `test_query_decomposition.py` / `test_citation_check.py`,不依赖 glob;同时 regression 步骤也加 `test_regression.py` 入口,确保 subagent #4 新增的 lazy_greedy 对比 + query_extension 路径测试每次 PR 都跑。
- **(audit #1) weekly CI 加 robustness / l1_metrics**: `ragas` weekly job 新增两个独立步骤(`Robustness suite` / `L1 component metrics`),通过 `if: github.event_name == 'schedule'` 控制只在 weekly 触发,不污染 PR 反馈循环。
- **(subagent #5) 覆盖率目标更新**: 5 个新增模块写入 `pyproject.toml` `[tool.coverage.report.module_targets]`:
  - `rag.retrieval.lazy_greedy` 95% — 纯算法,接近全覆盖
  - `rag.retrieval.decomposition` 95% — 纯算法,接近全覆盖
  - `rag.pipeline.query_ext` 90% — 含 LLM 调用 stub,留 10% 集成
  - `rag.retrieval.citation_check` 90% — 强约束,核心安全路径
  - `rag.eval.robustness` 70% — 探索性评测,变异多,目标适度
  CI 仍以 80% 整体 `--fail-under` 为硬阈值,模块目标作为 PR review 参考。
- **(subagent #7) CI 包含 `caches_have_no_stale` 集成测试**: on-PR `test` job 新增 `Cache invalidation (no-stale) test` 步骤,跑 `tests/integration/test_cache_invalidation.py -k caches_have_no_stale`,防止 cache staleness 回归。
