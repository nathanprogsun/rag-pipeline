.PHONY: dev up down doctor setup migration cleanup cleanup-hard lint test test-unit test-integration test-cache eval

# ---- infra ----

up:
	docker compose up -d

down:
	docker compose down

# ---- 生命周期 ----

# 不修改任何状态, 只读检查并报告 (pass / warn / fail)
doctor:
	uv run python -m rag.doctor

# 全量引导: 装依赖 -> .env -> 启 docker -> 同步 schema
# 幂等: 重复执行安全
setup:
	uv sync --extra dev
	@test -f .env || cp .env.example .env
	@echo "starting docker compose (pg + redis)..."
	docker compose up -d
	@echo "waiting for pg healthcheck..."
	@for i in $$(seq 1 30); do \
		if docker compose exec -T pg pg_isready -U rag -d rag >/dev/null 2>&1; then \
			echo "pg ready (took $$i s)"; break; \
		fi; \
		sleep 1; \
	done
	@echo "waiting for redis healthcheck..."
	@for i in $$(seq 1 30); do \
		if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then \
			echo "redis ready (took $$i s)"; break; \
		fi; \
		sleep 1; \
	done
	$(MAKE) migration
	@echo ""
	@echo "✓ setup complete"
	@echo "next: make doctor        # 验证环境"
	@echo "      make list-datasets # 列出已有 dataset (见 rag-search --help)"

# 同步 schema (幂等 create_all); 不动已有数据
# 未来切换到 Alembic 时, 这里改为 `alembic upgrade head`
migration:
	uv run python -c "import asyncio; from rag.infra.pg.database import init_pool; asyncio.run(init_pool()); print('schema synced')"

# 清空业务表数据, 保留 schema (RESTART IDENTITY + CASCADE)
# 需显式 CONFIRM=yes 防误操作
cleanup:
	@if [ "$$CONFIRM" != "yes" ]; then \
		echo "⚠  即将 TRUNCATE chunks, datasets (RESTART IDENTITY CASCADE)"; \
		echo "   重新执行: make cleanup CONFIRM=yes"; \
		exit 1; \
	fi
	uv run python -c "import asyncio; from rag.infra.pg.database import truncate_all; asyncio.run(truncate_all()); print('data wiped (schema kept)')"

# 删除所有表 (含 schema), 等同于 fresh install
# 需显式 CONFIRM=yes; 执行后必须 make migration 重建
cleanup-hard:
	@if [ "$$CONFIRM" != "yes" ]; then \
		echo "⚠  即将 DROP 全部业务表"; \
		echo "   重新执行: make cleanup-hard CONFIRM=yes"; \
		echo "   之后必须: make migration"; \
		exit 1; \
	fi
	uv run python -c "import asyncio; from rag.infra.pg.database import drop_all; asyncio.run(drop_all()); print('all tables dropped')"

# ---- 代码质量 ----

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src tests

# ---- 测试 ----

test-unit:
	uv run pytest tests/unit/ -v

# 需本地 PG 已启动（make up）；建议 .env 使用专用测试库 DATABASE_URL
test-integration:
	uv run pytest tests/integration/ -v

test-cache:
	uv run pytest tests/unit/test_cache_keys.py tests/unit/test_cache_metrics.py tests/unit/test_cache_settings.py tests/integration/test_cache.py -n auto -v

test: test-unit test-integration
