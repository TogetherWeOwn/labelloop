.PHONY: dev down test lint import-contract setup

setup:
	uv sync
	corepack pnpm install --frozen-lockfile

dev:
	uv run --no-sync python scripts/check-fixtures.py
	docker compose up --build --detach --wait
	@echo "Web: http://localhost:5173"
	@echo "API: http://localhost:8000/docs"
	@echo "MinIO console: http://localhost:9001"

down:
	docker compose down

test:
	uv run pytest
	corepack pnpm --dir apps/web test

lint:
	uv run ruff check .
	corepack pnpm --dir apps/web lint
	uv run python scripts/check-import-contract.py

import-contract:
	uv run python scripts/check-import-contract.py
