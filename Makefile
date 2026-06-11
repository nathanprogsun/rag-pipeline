.PHONY: dev up lint test test-unit test-integration eval

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

test: test-unit test-integration
