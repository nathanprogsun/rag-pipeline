.PHONY: dev up lint test test-unit test-integration test-cache eval

up:
	docker compose up -d

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src tests

test-unit:
	uv run pytest tests/unit/ -v

# 需本地 PG 已启动（make up）；建议 .env 使用专用测试库 DATABASE_URL
test-integration:
	uv run pytest tests/integration/ -v

test-cache:
	uv run pytest tests/unit/test_cache_keys.py tests/unit/test_cache_metrics.py tests/unit/test_cache_settings.py tests/integration/test_cache.py -n auto -v

test: test-unit test-integration
