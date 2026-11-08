# Management consumer contract (preparation)

This is an unbound facade contract, not an available production API. The route
factory is deliberately not mounted until the shared session/CSRF and real
transaction/queue adapters are integrated. Recording-port tests prove dispatch
and serialization only, not persistence, concurrency, authorization within a
project, or the CPU project-to-export acceptance path.

## Detail reads

All reads require the shared authenticated human principal (admin or reviewer),
return `Cache-Control: no-store`, and require the adapter to authorize the
containing project before loading any resource or artifact. An inaccessible
resource is `not_found`, not an empty successful projection. List/index results
remain small `Resource` summaries; they are not sufficient screen details.

| Generated operation | Route | Projection and authority |
|---|---|---|
| `readProject` | `GET /projects/{project_id}` | Scope, class definitions/aliases/parents/exclusions/mirror safety, explicit proposed/admin-approved provenance, references with validity/source/permitted-use metadata, structured guide, source-group counts and pending pairs, setup approvals and version pins |
| `readFreezePlan` | `GET /projects/{project_id}/freeze-plan` | Server-selected eligible annotation revision membership, ontology, split assignment and expected project revision; null payload plus explicit checks when not ready |
| `readDatasetRelease` | `GET /dataset-releases/{release_id}` | Exact source hashes/revisions/splits, class support, exclusions, quarantine, checks, provenance, prior release and immutable manifest artifact/hash |
| `readRecipePreview` | `GET /dataset-releases/{release_id}/recipes/{recipe_id}/preview?asset_id=...` | Compiler-produced `AffineTransform`, already-transformed boxes, actual raster dimensions/gains/padding/forward/inverse, small/clipped/excluded target counts and validation artifacts |
| `listTrainingProfiles` | `GET /projects/{project_id}/training-profiles` | Server-approved local profiles: ID/version, recipe, initialization hash, epochs and pinned safe arguments |
| `readTrainingRun` | `GET /training-runs/{run_id}` | Profile/version, dataset/variant lineage, requested/resolved arguments and mismatches, hashed core checkpoints with development validation, retention evidence and progress/failure |
| `readEvaluation` | `GET /evaluations/{evaluation_id}` | Actual core original-frame `EvaluationReport` for candidate/incumbent: class support, slices, coverage loss and policy; separate qualification/check evidence, nullable thresholds, final-test opening count |
| `readRegistry` | `GET /projects/{project_id}/registry/{recipe_id}?cursor=...&limit=...` | Core atomic `ModelRelease` for champion/candidate/rollback, registry revision, qualification/checks, attributed history with bounded cursor pagination |

Detail DTOs reuse core `EvaluationReport`, `Checkpoint`, `AffineTransform`, `Box`
and `ModelRelease`; they do not recompute geometry, metrics, checkpoint selection
or qualification in the API/browser. `checks[].passed: null`, null reports and
null thresholds mean missing evidence, never success. Display copy can map wire
identifiers to accepted screen labels without treating those labels as state.
Future real adapters must select only approved scalar training argument keys,
never raw logs/provider dictionaries containing paths, tokens or signed URLs.
Artifacts are application IDs authorized through the common artifact boundary.

The schema/client generator includes these models and operations in its Actions
artifact. Checked-in generated publication and final combined auth/app OpenAPI
remain incomplete; do not hand-edit a second client from these tables.

## Mutation alignment

- `startTraining`: resolve a profile from `listTrainingProfiles`, then send its
  immutable ID with the **variant's** expected revision. Profile IDs name frozen
  versions: updating a profile produces another ID. The server verifies the
  profile is approved for the project/recipe and resolves checkpoint/epochs/
  arguments. Never forward UI base-hash/epoch fields as dispatch authority.
- `compileVariant`: one request per recipe, using the **dataset release's**
  expected revision and a separate idempotency key per recipe. A recipe-list UI
  action is a client fan-out, not a new server compiler or scheduler. Refresh
  resource revisions as necessary; do not assume the first mutation leaves the
  aggregate revision unchanged.
- `freezeRelease`: take ontology, assignment and annotation membership from
  `readFreezePlan`, not from browser-generated membership. The transaction must
  lock/recheck the expected project revision and all canonical eligibility,
  completeness and grouping facts. A stale preview is a conflict, not approval.
- `createProject`: current bootstrap-pack route creates canonical state; it is
  **not** draft autosave, class/reference approval or project-release publication.
- `promoteModel`: the existing promotion port is a composition placeholder for
  the qualification/operations producer's single route/service. Do not register
  two handlers on the same path or bypass qualification/null-target gates.

Every mutation remains admin-only with strict unknown-field refusal, scoped
idempotency, revision protection and attributed append-only audit committed with
the shared fenced-queue intent where asynchronous. Read contracts do not grant
mutation authority.

## Remaining commands and accountable producer boundaries

The following are acceptance gaps, **not implemented routes or enablement flags**.
The management facade integrator owns the setup command/persistence adapter;
there is no alternate UI-side store or generic `values` passthrough endpoint.
Before live wiring these require typed payloads, admin gates, aggregate revision/
idempotency/audit tests and disposable Actions DB concurrency evidence.

| Fixture command family | Producer and intended implementation boundary |
|---|---|
| `save_setup`, `add_class` | Management facade: typed per-step draft writes; stable class IDs and structured scope/guide/source metadata; edits invalidate dependent approvals |
| `accept_class`, `accept_reference`, `approve_step` | Management facade: authenticated admin events, not client-set `approval`/human fields; reference provenance and evaluation-holdout exclusion enforced server-side |
| `resolve_pair` | Management facade persists the decision using delivered canonical grouping contracts; no new grouping engine; revisioned split changes/quarantine must respect the existing grouping policy |
| `publish_project` | Management facade freezes approved ontology, guide, reference, split, recipe and policy versions; validates prerequisite approvals, keeps null targets fail-closed |
| `new_recipe_version` | Management facade's canonical recipe/version adapter; immutable released recipes and delivered compiler transform rules, not browser geometry |
| `cancel_local_run` | Management facade requests cancellation through the shared fenced queue producer; the worker owns lease/fence and terminal state, no browser process kill |
| `open_locked_test` | Qualification/operations producer owns final-test lifecycle and development-reuse transition; management consumes its authorized command/evidence rather than creating a second rule |
| `promote`, `rollback` | Qualification/operations producer owns qualified registry service and atomic release/history; management composes that single service with shared auth |

Shared auth/review owns principal/session/CSRF and annotation review writes.
The queue producer owns durable enqueue/cancel/lease/fence semantics. The
management integrator owns canonical project/asset/release adapters and binds
those delivered ports. The qualification/operations producer owns gate policy,
final-test lifecycle, promotion/rollback, and recovery inventory registration.
These are explicit implementation owners, not claims that upstream ports have
landed. No management table is restore-qualified before its migration/table is
registered in the existing recovery inventory and tested by the restore owner.

REA angle: project these details from the already-delivered canonical/compiler/
training/evaluation/registry contracts; borrow no new state store or scheduler.
