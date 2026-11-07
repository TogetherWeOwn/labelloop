# Review of "Bulk Image Annotation Plan" (design v0.2)

Status: COMPLETE (2026-11-07).

## Summary of the goal and operating principles (as understood)

- **Goal:** one engine, with no per-domain code, that takes a new visual domain from an empty project to qualified YOLO detectors. It drafts labels with detectors and references, reviews them with vision LLMs plus deterministic checks, adjudicates and audits uncertain cases, freezes annotation releases, compiles reproducible 640 and 320 datasets, trains one model per recipe through the user's Ultralytics Platform account, evaluates each independently, promotes per recipe, and repeats while measured evidence justifies it.
- **Principles:**
  1. Claims are scoped and measured ("qualified for a declared scope and target"), never "perfect".
  2. Code owns geometry, state, releases and gates. Models only propose, and their output is untrusted data.
  3. Canonical labels live on original pixels. Every sized dataset is a reproducible derivative.
  4. Splits are fixed by source group before any derivative is made.
  5. Evaluation stays independent from training, calibration and reference retrieval.
  6. Every artifact has lineage and can be rolled back.
  7. Vendor facts are researched and cited; design choices are marked as proposals.

The principles are sound and unusually careful. Most of the issues below are gaps in delivery, cost, ownership and scope, not in the core ML reasoning.

## A. Design and delivery issues found (own review)

| # | Severity | Issue | Fix in final plan |
|---|---|---|---|
| A1 | High | **Uncapped spend.** Cloud training on Ultralytics Platform and vision-LLM review are paid per use. §8.1 stores "the user's authorized project policy … so routine eligible iterations can proceed", which lets the system spend on its own with no budget, cap or cost estimate. | Add a cost ledger, per-project and per-iteration budgets with hard stops, and a dry-run cost estimate before every training start or LLM review batch. The default is a manual start. |
| A2 | High | **Licence not addressed.** `ultralytics` is AGPL-3.0, and the plan imports it in the controlled worker, evaluator and exporter. A network-served application that links it must be AGPL-compatible, or the operator needs an Ultralytics Enterprise licence. | Licence the repo AGPL-3.0, keep `ultralytics` imports in the worker package, and record the Enterprise-licence route as a future product-owner spend decision if a closed deployment is ever wanted. |
| A3 | High | **No deployment target or compute plan.** Exact-shape inference, fixed-shape validation, dataloader audits and the controlled trainer all need a Python worker with `ultralytics`/PyTorch, and a GPU for anything beyond tiny fixtures. The plan never says where this runs. | Milestone 0 defines local dev on docker compose (Postgres, MinIO, API, worker, web). CI uses CPU-only fixtures (yolo26n, 1–3 epochs on a ~16-image fixture). Hosted deployment and GPU hosting are a later spend decision. |
| A4 | High | **No CI-safe training path.** Every training test depends on a paid external account, so Milestones 3, 4 and 6 cannot be tested in GitHub Actions. | Make `LocalUltralyticsBackend` a first-class `TrainingBackend` next to `UltralyticsPlatformBackend`, with the same run contract. CI runs the local backend on CPU. Platform contract tests use recorded fixtures plus an opt-in live smoke test behind a secret. |
| A5 | Medium | **No authentication or tenancy design**, although Project has "Owner/workspace, access policy" and §12 says LLM workers are separated from privileged workers. | v1 is single-tenant with one admin plus reviewer accounts (session auth and role checks), with every review event tied to the authenticated principal. Multi-tenant is out of scope for v1. |
| A6 | Medium | **Secret storage is underspecified.** The Platform key authenticates as the workspace owner. | Encrypt it at rest with an app master key from the environment. Only the platform-mutation worker can decrypt it. It never reaches the web tier, prompts, logs or exports. Store a key fingerprint for display only. |
| A7 | Medium | **The first vertical slice is too broad.** Milestones 1–7 are sequential, with M5 (LLM review) after the Platform work, and the "two domains" exit for M1 has no fixture source. | Reorder into M0 bootstrap → M1 canonical data and compiler (pure, offline) → M2 local training and evaluation loop → M3 review UI and LLM review → M4 Platform adapter → M5 qualification and promotion → M6 improvement loop. Ship two public, licence-clean fixture domains (for example a COCO subset and a permissively licensed UI-screenshot set) in-repo or by pinned download. |
| A8 | Medium | **Concrete technology is unspecified.** "Python API", "TypeScript interface", "durable DB-backed queue" and "embedding index" are left open. | FastAPI + SQLAlchemy 2 + Alembic. Postgres 16 with pgvector for reference retrieval, and a Postgres job queue (`SELECT … FOR UPDATE SKIP LOCKED`, or a library such as Procrastinate). S3-compatible storage (MinIO locally). Web: TypeScript + React (Vite), built from `two-design-kit` tokens and components per project design policy. uv for Python, pnpm for web. |
| A9 | Medium | **The near-duplicate method is unspecified**, yet grouping correctness depends on it. | Perceptual hash (pHash, Hamming ≤ threshold) plus embedding cosine ≥ threshold, both versioned. Union-find over all relations gives transitive groups. Thresholds are per-project config with a review queue for borderline pairs. |
| A10 | Medium | **No original-frame evaluator is named.** §11.2 rightly rejects Ultralytics' per-variant validation for promotion, but the replacement is unspecified. | Own `EvaluationRunner` that inverse-maps predictions to source frames and scores COCO-style mAP50-95, P/R at the deployment threshold, and per-class and per-slice results, with pycocotools-compatible numbers checked against a reference implementation in tests. |
| A11 | Medium | **No cost or privacy policy for LLM review.** Images go to third-party model providers, while §3.4 records "permitted use" only for references. | Each project declares the allowed providers. Each review request records the provider, model and token cost. Projects can forbid external providers (local-only proposals). |
| A12 | Low | **Reuse was not explored.** The plan builds a full review UI, while CVAT, Label Studio and FiftyOne already cover box editing, review queues and dataset QA. | Prior art: study how CVAT and Label Studio model review/adjudication state and how FiftyOne computes duplicates and mistakenness, and reuse ideas rather than embed them. The v1 review UI stays narrow: crops, references, accept/edit/add/uncertain. |
| A13 | Low | **Deadlock demo artifacts** are cited (6 scene instances, 12 portrait tiles) but are not attached, and game screenshots carry publisher ToS/copyright limits. | Treat the Deadlock pack as a private example that must not be committed. Public fixtures only in the repo. |
| A14 | Low | **The interpolation default `linear` for downscaling** 1920→320 aliases small objects. | Recipe default `area` for downscale and `linear` for upscale, pinned per recipe (see B for the Ultralytics behaviour). |
| A15 | Low | **Observability is missing**: job metrics, cost, and audit-log export. | Structured logs, per-stage timings and cost, and an audit-event table, all queryable in the UI. |
| A16 | Low | **The 22 acceptance checks are not mapped to milestones.** | Map each check to the milestone that must demonstrate it, as automated tests where possible. |

Prior art: CVAT and Label Studio (open source annotation and review state machines), FiftyOne Brain (near-duplicate and label-mistake scoring), the Ultralytics SDK `openapi.json` (the contract snapshot the adapter is generated and tested against) and the SAHI source (tile merge rules). These are read-first sources, and no reverse engineering is needed beyond reading them.

## B1. Fact-check: `ultralytics` library (verified against ultralytics 8.4.204 wheel source and docs, 2026-11-07)

| # | Plan claim | Verdict | Evidence | Change in v0.3 |
|---|---|---|---|---|
| B1.1 | 5-field labels, 0-based class, normalized cxcywh; empty label file = negative | Confirmed | `data/utils.py:479-506` | The parser **tolerates** coords in [-0.01, 1.01] and zero-size boxes, drops duplicate rows silently, and treats a missing label file the same as an empty one. The compiler gate must be stricter than the parser, and "explicit negative" must live in the manifest. |
| B1.2 | No detection ignore-region | Confirmed | ignore=255 only for semantic masks | unchanged (exclude/recrop) |
| B1.3 | `LetterBox` scales labels by theoretical r while rounding raster | Confirmed | `augment.py:1800-1801` | Defaults recorded: pad 114, center, INTER_LINEAR, scaleup train/True val/False, pad split `round(dh∓0.1)`. |
| B1.4 | (implied) inverse mapping also uses r | **Wrong** | `utils/ops.py:227-230` `scale_boxes` now uses **actual per-axis raster gains** | §7.2 text corrected. The library's forward and inverse differ by under 1 px, and the app's own inverse is authoritative. |
| B1.5 | `box_candidates` wh>2 px, area 0.1, ar 100; still applies with zero geometry | Confirmed | `augment.py:1416-1454`; RandomPerspective always in `v8_transforms` | Strict `>2` px: the plan's own 2-px object at 320 is **always** dropped. With default `scale=0.5`, boxes ≲6 px drop intermittently. Thresholds are not exposed as args. |
| B1.6 | `multi_scale` float, `imgsz` int, mosaic/mixup/cutmix/seed/deterministic | Confirmed | `cfg/default.yaml` | The no-scene-composition profile still inherits `scale=0.5, translate=0.1, fliplr=0.5`, HSV, and Albumentations p=0.01 transforms if installed. v0.3 config pins all of them. |
| B1.7 | Train-time val is rect with pad 0.5 → 672 / 352 | Confirmed | `detect/train.py:74`, `data/build.py:270`, `data/base.py:506` | unchanged (own fixed-shape selection) |
| B1.8 | `model.val()`/`predict()` default rect=True; `rect=False` works | Confirmed | `engine/model.py:543,642` | unchanged |
| B1.9 | `box.maps` fills absent classes with mean mAP | Confirmed | `utils/metrics.py:1008` | unchanged |
| B1.10 | YOLO26 exists and is NMS-free | **Partly wrong** | `default.yaml:61` `nms:` None = external NMS (default); `end2end = args.nms is False` | The default predict/val/export uses the one-to-many head + NMS, and `nms=False` selects the end-to-end head (YOLO26n mAP 40.1 vs 40.9). v0.3 pins `nms` per recipe across val, predict, export and tile merging, and records it in the model release. |
| B1.11 | static ONNX export, imgsz respected | Confirmed | `exporter.py:770` | imgsz is rounded up to a stride multiple; 320 and 640 are already multiples |
| B1.12 | SAHI integration | **Outdated** | The guide is marked deprecated in favour of the native **`tile`** arg (train/val/predict, fixed 20% overlap, edge-anchored last window, `ops.merge_tiles` NMS) | §7.4 now states the native `tile` must be **explicitly disabled** when the app's own tiling is used, or adopted as a named recipe with its fixed policy. SAHI is dropped from the default path. |
| B1.13 | `check_det_dataset` | Confirmed | `data/utils.py:719` | call with `autodownload=False` |
| B1.14 | Licence | **Missing from plan** | METADATA `License: AGPL-3.0` | See A2 and §12.3 |

New risks the plan missed:

- `fliplr=0.5` default can corrupt left/right-asymmetric identities (HUD text, handed characters), so default to 0 unless the class is marked mirror-safe.
- `translate=0.1` clips edge fragments, which then fail the 10% area-retention check. These are exactly the truncated targets the plan wants kept.
- `rect=True` training silently zeroes mosaic, mixup and cutmix.
- With `nms=False`, output is capped at `max_det` (default 300), which matters for dense tiles.
- The minimum supported target size per recipe must be declared, measured by the dataloader audit, and enforced: v0.3 sets the default `min_target_px_after_transform = 4` and flags anything ≤ 6 px.

## B2. Fact-check: Ultralytics Platform and SDK (live `openapi.json` at ultralytics/sdk commit 8af3921, PyPI, docs; 2026-11-07)

| # | Plan claim | Verdict | Evidence | Change in v0.3 |
|---|---|---|---|---|
| B2.1 | `ultralytics-platform` SDK with `Platform`/`AsyncPlatform`; HUB retired | Confirmed | PyPI 0.1.107, Python ≥3.11, httpx only; HUB shut down 2026-07-31; `ultralytics.hub` removed in 8.4.115 | Pin SDK ≥0.1.107 |
| B2.2 | Keys act as workspace owner; Bearer; origin | Confirmed | API-keys page; `securitySchemes.BearerAuth` (`ul_` prefix); keys don't expire | No scoped, read-only or OAuth keys exist, so treat the key as a full-owner credential (A6) |
| B2.3 | OpenAPI 3.2.0, API 0.1.104 | **Outdated by 1 day** | `info.version` is now 0.1.107 (104→107 changed predict/batch, image predict and explore/search wording only) | Pin the contract by file SHA-256, not by version string |
| B2.4 | All 17 §8.3 routes | Confirmed | All present; training also has `DELETE` (cancel), export also `PATCH` | Add useful routes: `GET /api/training/gpu-availability`, dataset image list, `/api/images/bulk`, `/versions/compare` |
| B2.5 | Upload flow; `targetSplit` overrides; raw uploads auto-split | Confirmed + nuance | Ingest with `sessionId` completes the upload, so `complete` is optional. Signed URLs last 12 h, are create-only (a second PUT returns 412), and accept zip/tar/tgz/ndjson. Ingest returns **201 queued** and accepts `classMapping` and `conflictPolicy`. **If val is empty, Platform moves about 20% of train to val.** | Every release must ship a non-empty val; reconcile splits after ingest; a retry needs a new signed URL |
| B2.6 | Image PATCH replaces all labels, rounds to 5 dp, no revision guard | Confirmed | 0 occurrences of If-Match/ETag/idempotency in the spec | unchanged |
| B2.7 | Training body and `ul://` URIs; dataset version on the model record, not the start response | Confirmed | `TrainingStartResponse` = modelId, status, gpuType, **estimatedCost, billing** | unchanged |
| B2.8 | GPU selectable | Confirmed | `gpuType` enum of 26; the API default (`rtx-4090`) disagrees with the docs (RTX PRO 6000) | Always send `gpuType` explicitly |
| B2.9 | (omitted) cost | **Missing from plan** | Credits are deducted; 402 = insufficient credits; $0.24/h (RTX 2000 Ada) to $7.39/h (B300), RTX 4090 $0.69/h; minimum 15 min reserved; cancelled runs billed; batch auto-annotation **$1.00 per 1,000 images** and snapshots a dataset version per run | §12.1 budget controls; handle 402 and 503 |
| B2.10 | Auto-annotation has no imgsz/rect; preserves labels; class-prompted has no confidence; partial flag | Confirmed | `predict/batch` body; model predict has `imgsz` 32–1280 but no `rect` | unchanged |
| B2.11 | NDJSON export | Confirmed | Image URLs signed for 7 days | unchanged |
| B2.12 | YOLO26 + earlier families | Confirmed | YOLO26 (recommended), YOLO11, v8, v5 | unchanged |
| B2.13 | No idempotency | Confirmed | Rate limits per key: 100/min default, training 10, upload 10, predict 20, export 20, download 30; 429 + `Retry-After`; training-start 409 if already active | Per-category client throttle; treat 409 as "reconcile" |
| B2.14 | Licence | **Missing from plan** | **Both `ultralytics` and the `ultralytics-platform` SDK are AGPL-3.0**; the Ultralytics licence page requires AGPL open-sourcing or an Enterprise licence, including for SaaS, APIs, internal tools and trained models used commercially | The repo is AGPL-3.0 (§12.3) |

New issues the plan missed:

1. **§9.3's "collect candidate checkpoints" does not work for cloud runs.** Platform exposes only the promoted `best.pt` (no `last.pt` and no per-epoch checkpoints). Fixed-shape checkpoint selection therefore requires the controlled trainer, or accepting the cloud `best.pt` as the single candidate.
2. **There are no server-side artifact hashes.** `GET …/files` returns name, size and URL only. The app hashes weights on download, and the remote digest is not attestable.
3. **`trainArgs` allows additional properties**, so schema validation will not catch a typo. Read back the resolved args from the model record and diff them against the request.
4. **Ingest resizes images over 4096 px** (long side) and rejects anything under 28 px (short side). Reconciliation must expect this.
5. **SDK retry semantics:** the SDK retries any JSON-body request on 429. Never add POST to `retry_methods`, because it would duplicate training starts.
6. **The SDK pings `http://127.0.0.1:8765/wake`** after ingest and training start (an On-Premise worker hook). Inject a custom `http_client` in server deployments to suppress it.
7. Ingest `classMapping` and post-ingest `classNames` order must be checked, because unnamed classes are remapped densely.

## C. Outcome

All findings in A and B are resolved in the final plan **v0.3** (`docs/plan.md`). §18 of the plan lists decisions taken, items delegated to the Engineering Lead, product-owner decisions reserved for later (none block M0–M3), and the REA angle.
