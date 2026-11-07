# labelloop

Multi-domain image annotation with vision-LLM review, reproducible YOLO dataset compilation and separate per-recipe model training (for example 640 x 640 and 320 x 320). Training runs locally or through the user's Ultralytics Platform account.

This repository is at **M0: development bootstrap**. It supplies the monorepo, placeholder services, licensed fixture inputs and CI. Domain features are implemented in later milestones. Read [`docs/plan.md`](docs/plan.md) before feature work.

## Requirements

- Docker with the Compose plugin and resources for the development ML worker (2 CPUs, 4 GB memory limit).
- `make` and `uv` for the fixture integrity preflight.
- No providers, API keys, paid calls or live Platform account are used.

For Python or web changes, installed `uv`, Node 24 and `corepack` support local development (`make setup`). The optional ML package uses its separate dependency group and the CPU PyTorch index. Its Docker image replaces the vendor's GUI OpenCV distribution with the same pinned headless OpenCV wheel used by the compiler, since those two `cv2` packages cannot coexist.

## Start the stack

```sh
cp .env.example .env
uv sync --frozen
make dev
```

`make dev` builds and starts all six long-running services, verifies the 32 licensed fixture images (160 × 160 JPEG, under 5 MB total), creates the development bucket through a one-shot `minio-init` service, and waits for health checks.

- **Web:** `http://localhost:5173` (substitute `${WEB_PORT}` if you configure the port below)
- **API/OpenAPI:** `http://localhost:8000/docs`
- **API liveness/readiness:** `/healthz`, `/readyz`
- **MinIO S3:** `http://localhost:9000` (or `${S3_PORT}`)
- **MinIO console:** `http://localhost:9001` (or `${S3_CONSOLE_PORT}`)
- **PostgreSQL:** `localhost:${POSTGRES_PORT:-5432}`
- The web view calls `/api/healthz`, which the Vite dev server proxies to the API container. `/readyz` verifies PostgreSQL, the `vector` extension, and MinIO before the web container starts.

Workers are placeholders and do not dequeue jobs or train models. Source directories are bind-mounted for iterative development; changes to dependencies or container definitions require `docker compose up --build --detach --wait`. No production image, account or deployment is configured.

The `.env.example` values are intentionally public **local-only** credentials. Supply separate credentials and configure appropriately for any other environment; Compose publishes its development ports to loopback only. Use `make down` to stop the stack, or `docker compose down --volumes` to also remove the development data volumes.

## Monorepo

| Path | Purpose |
| --- | --- |
| `packages/core` | Pure domain model and strict typed core |
| `packages/compiler` | Deterministic dataset compiler |
| `packages/platform` | Ultralytics Platform adapter boundary |
| `packages/llm` | LLM reviewer contracts |
| `apps/api` | FastAPI application |
| `apps/web` | React + Vite + TypeScript application |
| `workers/core` | Non-ML job runner placeholder |
| `workers/ml` | CPU-only ML worker |
| `fixtures` | Two independently licensed image domains with 16 images each and provenance manifests |
| `docs/adr` | Architecture decision records |

`ultralytics` and `ultralytics_platform` imports are allowed only in `packages/platform` and `workers/ml`. `make import-contract` uses the tracked `importlinter.ini`, mirrors the source packages to a temporary directory, then injects a deliberate forbidden import and requires import-linter to report it. Only that temporary fixture is modified.

## Validation

```sh
make test
make lint
uv run mypy --strict packages/core
corepack pnpm --dir apps/web typecheck
```

`make test` covers every Python package and the web component test. `make lint` adds the import-linter failing-fixture check. GitHub Actions is the authoritative complete-suite environment and additionally runs `mypy`, `tsc`, web build, fixtures, actionlint/zizmor, and the full `make dev` startup. Draft repair commits run every affected check. `ci-ok` fails on missing or failed jobs and accepts skipped area jobs only when that exact event's changed-area map and full-run decision disabled them.

`gitleaks` scans full history; `pr-lint` enforces Conventional Commits; `pr-size` labels changes and is advisory. Actions are pinned by commit SHA. `ci-ok` and secret scanning do not use workflow-level path filters.

## Licence

Labelloop is AGPL-3.0. See [`LICENSE`](LICENSE). The project depends on `ultralytics` and `ultralytics-platform`, which are both AGPL-3.0; see plan §12.3. This does **not** grant arbitrary third-party fixture rights; see [`fixtures/README.md`](fixtures/README.md). The interface set is CC0; the office keyboard set is CC BY 4.0 with per-image creator and source-page attribution. The unchanged MinIO development binary uses its last official archived release, verified by release SHA-256, with its license in [`dev/minio/LICENSE`](dev/minio/LICENSE). If you run a modified version as a network service, AGPL §13 requires you to offer its source to your users.
