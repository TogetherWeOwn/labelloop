.PHONY: dev down test lint import-contract setup

setup:
	uv sync
	corepack pnpm install --frozen-lockfile

dev:
	uv run --no-sync python scripts/check-fixtures.py
	LOCAL_UID="$${LOCAL_UID:-$$(id -u)}" LOCAL_GID="$${LOCAL_GID:-$$(id -g)}" \
	  docker compose up --build --detach --wait
	@# Compose reads .env; this shell expansion reproduces its port defaults.
	@ports="$$(set -a; [ ! -f .env ] || . ./.env; set +a; printf '%s %s %s' "$${WEB_PORT:-5173}" "$${API_PORT:-8000}" "$${S3_CONSOLE_PORT:-9001}")"; \
	  set -- $$ports; \
	  printf 'Web: http://localhost:%s\nAPI: http://localhost:%s/docs\nMinIO console: http://localhost:%s\n' "$$1" "$$2" "$$3"

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
