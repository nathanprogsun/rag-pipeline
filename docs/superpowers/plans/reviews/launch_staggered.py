#!/usr/bin/env python3
"""Staggered launch: 1 agent every 45s to avoid 429 rate limits.

Launches 10 subagents but with 45s between each start. The 429 limit usually
recovers within 30-60s for a single concurrent request.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Users/jung/pro/rag-pipeline")
REVIEW_DIR = ROOT / "docs/superpowers/plans/reviews"
PROMPTS_DIR = REVIEW_DIR / "prompts"
AGENTS_DIR = REVIEW_DIR / "agents"
LOGS_DIR = REVIEW_DIR / "logs"
COMMON_CTX = REVIEW_DIR / "_common_context.md"

AGENT_NAMES = [
    "agent1_foundation_domain",
    "agent2_pg_vector",
    "agent3_fulltext_cache",
    "agent4_llm_reader",
    "agent5_chunker_ingest",
    "agent6_fusion_filter",
    "agent7_query_extension",
    "agent8_subgraph_orchestrator",
    "agent9_missing_tasks_audit",
    "agent10_cli_eval_ci",
]

# Cleanup any old logs
for name in AGENT_NAMES:
    log = LOGS_DIR / f"{name}.log"
    if log.exists():
        log.unlink()
    out = AGENTS_DIR / f"{name}.md"
    if out.exists():
        out.unlink()

AGENTS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Build composite prompts
for name in AGENT_NAMES:
    agent_prompt = PROMPTS_DIR / f"{name}.md"
    composite = REVIEW_DIR / f"_composite_{name}.md"
    agent_out = AGENTS_DIR / f"{name}.md"

    composite.write_text(
        COMMON_CTX.read_text()
        + "\n\n---\n\n"
        + agent_prompt.read_text()
        + "\n\n---\n\n## 启动说明\n"
        + f"1. 仔细阅读以上 共同上下文 + 你的 agent 任务说明 + 必读参考\n"
        + f"2. 阅读你范围内的 task 文件(`tasks/taskN.md`) 全文\n"
        + f"3. 翻阅 spec `/Users/jung/pro/rag-pipeline/docs/superpowers/specs/2026-06-10-python-rag-pipeline-design.md` 对应章节\n"
        + f"4. 按\"输出格式\"结构组织 review\n"
        + f"5. 将完整 review **写入文件**: `{agent_out}`\n"
        + f"6. 在终端输出简短摘要(≤ 500 字,给出 3 条最关键发现 + 1 句总评)\n\n"
        + f"## 重要提醒\n- 你是 reviewer,不是 implementer\n- 不要修改任何 task 文件或源码\n- 所有结论必须有 `file:line` 证据\n- 跨 task 冲突优先在\"跨 Task 一致性核查\"列出\n- 若发现 task15/16 缺失影响你审查的 task,在 review 顶部明确说明\n"
    )

# Configuration
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "3"))  # 3 concurrent
BATCH_DELAY = int(os.environ.get("BATCH_DELAY", "60"))  # 60s between batches
STAGGER = int(os.environ.get("STAGGER", "20"))  # 20s between agents in batch

print(f"=== Staggered launch: BATCH_SIZE={BATCH_SIZE} BATCH_DELAY={BATCH_DELAY}s STAGGER={STAGGER}s ===", flush=True)

def launch_one(name: str) -> subprocess.Popen:
    composite = REVIEW_DIR / f"_composite_{name}.md"
    agent_out = AGENTS_DIR / f"{name}.md"
    agent_log = LOGS_DIR / f"{name}.log"

    log_fh = open(agent_log, "w")
    prompt_fh = open(composite, "r")

    cmd = [
        "codex", "exec",
        "-C", str(ROOT),
        "--sandbox", "read-only",
        "--skip-git-repo-check",
        "--output-last-message", str(agent_out),
    ]

    p = subprocess.Popen(
        cmd,
        stdin=prompt_fh,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
        start_new_session=True,
        close_fds=True,
    )
    return p, prompt_fh, log_fh

# Launch in batches
all_procs = []
for batch_start in range(0, len(AGENT_NAMES), BATCH_SIZE):
    batch = AGENT_NAMES[batch_start:batch_start + BATCH_SIZE]
    print(f"\n=== Batch {batch_start // BATCH_SIZE + 1}: {batch} ===", flush=True)

    for i, name in enumerate(batch):
        if i > 0:
            print(f"  Stagger wait {STAGGER}s...", flush=True)
            time.sleep(STAGGER)
        try:
            p, pf, lf = launch_one(name)
            all_procs.append((name, p, pf, lf))
            print(f"  {name}: PID {p.pid}", flush=True)
        except Exception as e:
            print(f"  {name}: FAILED to launch: {e}", flush=True)

    if batch_start + BATCH_SIZE < len(AGENT_NAMES):
        print(f"  Batch delay {BATCH_DELAY}s before next batch...", flush=True)
        time.sleep(BATCH_DELAY)

print(f"\n=== All {len(all_procs)} subagents launched in {len(AGENT_NAMES) // BATCH_SIZE + 1} batches ===", flush=True)

# Don't wait
for _, _, pf, lf in all_procs:
    try: pf.close()
    except: pass
    try: lf.close()
    except: pass

print("Parent exiting. Children continue.", flush=True)
