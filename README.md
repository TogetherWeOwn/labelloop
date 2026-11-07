# labelloop

Multi-domain image annotation with vision-LLM review, reproducible YOLO dataset compilation and separate per-recipe model training (for example 640 x 640 and 320 x 320). Training runs locally or through the user's Ultralytics Platform account.

The engine sets up a new visual domain and then works through this loop:

1. drafts labels with detectors and approved references;
2. reviews identity, geometry and missing objects with vision LLMs plus deterministic checks;
3. adjudicates and audits uncertain cases;
4. freezes annotation releases;
5. compiles reproducible datasets for each image recipe;
6. trains one model per recipe on its compiled dataset and evaluates predictions against original-frame ground truth;
7. promotes or rolls back each recipe independently;
8. repeats while measured evidence justifies it.

Quality claims are always scoped: a model is **qualified for a declared scope and a measured target**, never "perfect".

## Status

Pre-implementation. The build plan is [`docs/plan.md`](docs/plan.md) (design v0.3). The review that produced v0.3 is [`docs/plan-review.md`](docs/plan-review.md). Work follows the milestones in plan §15, starting with M0 (bootstrap).

## Layout (planned, see plan §2.3)

```text
apps/api/        FastAPI service
apps/web/        React review and management UI
packages/core/   domain model, geometry, transforms, split grouping (pure Python)
packages/compiler/  deterministic YOLO dataset compiler
packages/platform/  Ultralytics Platform adapter
packages/llm/    LLM reviewer adapters
workers/         job runners (core, ml)
fixtures/        tiny licence-clean datasets and golden outputs
docs/            plan, ADRs, runbooks
```

## Licence

AGPL-3.0. The project depends on `ultralytics` and `ultralytics-platform`, which are both AGPL-3.0; see plan §12.3. If you run a modified version as a network service, AGPL §13 requires you to offer its source to your users.
