"""``rag.doctor`` — 环境与依赖诊断。

不修改任何状态, 只读检查并报告:
- .env / Python / uv / DATABASE_URL / PG 连通 / PG 扩展 / Redis / 表存在

通过 `python -m rag.doctor` 触发 (由 Makefile 的 `make doctor` 调用)。
退出码: 0 (全部通过 / 仅 WARN), 1 (有 FAIL)。
"""

from __future__ import annotations

import asyncio
import socket
import sys
import urllib.parse
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from rag.config import settings
from rag.infra.llm.tokenizer import load_minimax_m3_tokenizer
from rag.infra.pg.base import Base

# ANSI 颜色 (终端不支持时自动降级)
_USE_COLOR = sys.stdout.isatty()
_GREEN = "\033[32m" if _USE_COLOR else ""
_YELLOW = "\033[33m" if _USE_COLOR else ""
_RED = "\033[31m" if _USE_COLOR else ""
_BOLD = "\033[1m" if _USE_COLOR else ""
_RESET = "\033[0m" if _USE_COLOR else ""


@dataclass
class CheckResult:
    """单条检查结果。"""

    name: str
    status: str  # "pass" | "warn" | "fail"
    detail: str
    hint: str = ""


@dataclass
class DoctorReport:
    """汇总报告。"""

    results: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str, hint: str = "") -> None:
        self.results.append(CheckResult(name, status, detail, hint))

    @property
    def has_fail(self) -> bool:
        return any(r.status == "fail" for r in self.results)

    def print(self) -> None:
        print(f"{_BOLD}=== rag.doctor ==={_RESET}")
        for r in self.results:
            icon = {"pass": f"{_GREEN}✓{_RESET}", "warn": f"{_YELLOW}!{_RESET}", "fail": f"{_RED}✗{_RESET}"}[r.status]
            line = f"  {icon} {r.name:<28}  {r.detail}"
            if r.hint:
                line += f"\n      {r.hint}"
            print(line)
        passes = sum(1 for r in self.results if r.status == "pass")
        warns = sum(1 for r in self.results if r.status == "warn")
        fails = sum(1 for r in self.results if r.status == "fail")
        print()
        print(f"{_BOLD}summary:{_RESET} {passes} pass, {warns} warn, {fails} fail")
        if fails:
            print(f"{_RED}doctor 报告 FAIL, 请先修复再继续。{_RESET}")
        elif warns:
            print(f"{_YELLOW}doctor 报告 WARN, 可选修复 (见上方 hint)。{_RESET}")
        else:
            print(f"{_GREEN}doctor 报告 PASS, 环境就绪。{_RESET}")


# ---------- 单项检查 ----------


def check_env_file(report: DoctorReport) -> None:
    """`.env` 存在性 + 关键键检查。"""
    env_path = Path(".env")
    if not env_path.exists():
        report.add(
            ".env",
            "warn",
            "未找到 .env 文件",
            "运行 `make setup` 或 `cp .env.example .env`",
        )
        return
    content = env_path.read_text(encoding="utf-8")
    required = ["DATABASE_URL", "REDIS_URL", "OPENAI_API_KEY"]
    missing = [k for k in required if k not in content or f"{k}=" not in content]
    if missing:
        report.add(".env", "warn", f"缺少键: {', '.join(missing)}", "补全 .env 后重跑")
    else:
        report.add(".env", "pass", "存在且含必需键")


def check_python_version(report: DoctorReport) -> None:
    """Python 版本 >= 3.13。"""
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 13)
    if ok:
        report.add("python", "pass", f"{sys.version.split()[0]}")
    else:
        report.add(
            "python",
            "fail",
            f"{sys.version.split()[0]} (要求 >= 3.13)",
            "通过 pyenv 或 uv 切换到 3.13+",
        )


def check_uv(report: DoctorReport) -> None:
    """`uv` 命令可用性。"""
    import shutil

    uv = shutil.which("uv")
    if uv:
        report.add("uv", "pass", f"位于 {uv}")
    else:
        report.add("uv", "fail", "未安装", "安装: https://docs.astral.sh/uv/")


def check_database_url(report: DoctorReport) -> None:
    """`DATABASE_URL` 可解析, scheme 为 postgresql+asyncpg。"""
    url = settings.database_url
    try:
        parsed = urllib.parse.urlparse(str(url))
        if not parsed.scheme.startswith("postgresql"):
            report.add("DATABASE_URL", "fail", f"scheme={parsed.scheme!r}", "期望 postgresql+asyncpg://...")
            return
        if not parsed.hostname:
            report.add("DATABASE_URL", "fail", "缺少 host", "")
            return
        report.add("DATABASE_URL", "pass", f"{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}")
    except Exception as e:
        report.add("DATABASE_URL", "fail", f"解析失败: {e!r}", "")


def check_redis_url(report: DoctorReport) -> None:
    """`REDIS_URL` 可解析, scheme 为 redis。"""
    url = str(settings.redis_url)
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "redis":
            report.add("REDIS_URL", "fail", f"scheme={parsed.scheme!r}", "期望 redis://...")
            return
        report.add("REDIS_URL", "pass", f"{parsed.hostname or 'localhost'}:{parsed.port or 6379}")
    except Exception as e:
        report.add("REDIS_URL", "fail", f"解析失败: {e!r}", "")


async def _check_pg(report: DoctorReport) -> None:
    """PG 连通性 + 扩展 + 表存在性。"""
    url = str(settings.database_url)
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
    except Exception as e:
        report.add("pg", "fail", f"engine 创建失败: {e!r}", "检查 DATABASE_URL")
        return
    try:
        async with engine.begin() as conn:
            # 连通
            try:
                version = (await conn.execute(text("SELECT version()"))).scalar()
                report.add("pg connectivity", "pass", version.split(" on ")[0])
            except Exception as e:
                report.add(
                    "pg connectivity",
                    "fail",
                    f"SELECT 1 失败: {type(e).__name__}: {e!r}",
                    "确认 docker compose 已启动, 端口 5432 可达",
                )
                await engine.dispose()
                return

            # 扩展
            try:
                rows = (
                    await conn.execute(
                        text("SELECT extname FROM pg_extension WHERE extname IN ('vector', 'pgcrypto')")
                    )
                ).all()
                installed = {row.extname for row in rows}
                if {"vector", "pgcrypto"}.issubset(installed):
                    report.add("pg extensions", "pass", "vector, pgcrypto")
                else:
                    missing = {"vector", "pgcrypto"} - installed
                    report.add(
                        "pg extensions",
                        "warn",
                        f"缺少 {', '.join(missing)}",
                        "需 pgvector/pgvector 镜像, 已在 docker-compose 中配置",
                    )
            except Exception as e:
                report.add("pg extensions", "warn", f"查询失败: {e!r}", "")

            # 表
            try:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
                        )
                    )
                ).all()
                tables = {row.tablename for row in rows}
                expected = {"datasets", "chunks"}
                missing = expected - tables
                if missing:
                    report.add(
                        "pg tables",
                        "warn",
                        f"缺少 {', '.join(missing)} (现有: {', '.join(sorted(tables)) or '无'})",
                        "运行 `make migration` 创建表",
                    )
                else:
                    report.add("pg tables", "pass", f"{sorted(tables)}")
            except Exception as e:
                report.add("pg tables", "warn", f"查询失败: {e!r}", "")
    finally:
        await engine.dispose()


async def check_redis(report: DoctorReport) -> None:
    """Redis PING。"""
    import redis.asyncio as redis_async

    url = str(settings.redis_url)
    parsed = urllib.parse.urlparse(url)

    client = redis_async.from_url(url, socket_connect_timeout=2)
    try:
        pong = await client.ping()
        if pong:
            report.add(
                "redis",
                "pass",
                f"PONG from {parsed.hostname or 'localhost'}:{parsed.port or 6379}",
            )
        else:
            report.add("redis", "fail", "PING 返 falsy", "")
    except Exception as e:
        report.add(
            "redis",
            "fail",
            f"{type(e).__name__}: {e!r}",
            "确认 docker compose 已启动, 端口 6379 可达",
        )
    finally:
        await client.aclose()


def check_minimax_m3_tokenizer(report: DoctorReport) -> None:
    """项目专用的 MiniMax-M3 BPE tokenizer 能否加载。"""
    try:
        tok = load_minimax_m3_tokenizer()
        report.add("minimax_m3 tokenizer", "pass", f"已加载, vocab={tok.get_vocab_size()}")
    except Exception as e:
        report.add("minimax_m3 tokenizer", "warn", f"加载失败: {type(e).__name__}: {e!r}", "首次使用会 lazy 下载")


# ---------- 入口 ----------


async def main() -> int:
    report = DoctorReport()

    check_env_file(report)
    check_python_version(report)
    check_uv(report)
    check_database_url(report)
    check_redis_url(report)
    check_minimax_m3_tokenizer(report)
    await _check_pg(report)
    await check_redis(report)

    # 已安装但用不到的包检测 (开发可选)
    try:
        ragas_ver = importlib_metadata.version("ragas")
        report.add("ragas (optional)", "pass", f"v{ragas_ver} (eval 需要)")
    except importlib_metadata.PackageNotFoundError:
        report.add("ragas (optional)", "warn", "未安装", "仅 ragas 真实指标需要; 可选 `uv sync --extra eval` (若配置)")

    report.print()
    return 1 if report.has_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
