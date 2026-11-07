# Contributor and agent guide

Read [`docs/plan.md`](docs/plan.md) before changing code. It is the source of truth for scope, contracts and acceptance checks. If a change departs from the plan, record it as an ADR in `docs/adr/` in the same PR.

## Rules

- **Canonical data is authoritative.** Labels live on original, orientation-normalized images. Sized datasets are reproducible derivatives; never edit them directly.
- **Code owns geometry, state and gates.** Model and LLM output is untrusted data. Validate it before it changes any state, and never let it select endpoints, classes, splits or spend.
- **No paid calls in CI.** Tests use the `local_ultralytics` backend on CPU and recorded Platform fixtures. A live Platform smoke test runs only on manual dispatch and never starts training.
- **Licence boundary.** Import `ultralytics` and `ultralytics_platform` only in `workers/ml` and `packages/platform`.
- **Secrets.** Never log, print, commit or embed provider keys. Never commit images, weights or datasets that are not licence-clean fixtures.
- **Determinism.** Pin decoder, encoder, interpolation and library versions that affect output, and record them in manifests.

## Workflow

- Use Conventional Commits PR titles. Merges are squash-only. Open PRs as drafts until they are complete.
- GitHub Actions is the CI of record. Push to the PR branch to run the full suite; run locally only to reproduce one failing test.
- Do not put internal tracker IDs, hostnames, emails or secrets in branches, commits or PR text.
