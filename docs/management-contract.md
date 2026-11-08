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
Project setup also distinguishes unknown fields from observed empty/false values.
`annotation_guide` is the stored bootstrap free text; nullable `guide` is the
structured geometry/completeness policy, never parsed or guessed from that text.
Without a setup evidence producer, display name, representation, intended use,
specificity, references, approved steps, class approval/mirror safety/exclusions/
reference support and configured metadata keys remain null. Source image/pending
pair facts are available, while group/split counts and assignment version are null
unless one complete, locked, unambiguous version exists. Counts are not approval.
Consumers must not coalesce missing evidence into a passing check or zero support.

Future real adapters must select only approved scalar training argument keys,
never raw logs/provider dictionaries containing paths, tokens or signed URLs.
Artifacts are application IDs authorized through the common artifact boundary.

The schema/client generator includes these models and operations in its Actions
artifact and the checked-in `apps/api/openapi/management.openapi.json` and
`apps/web/src/generated/management-client.ts`. Actions checks both tracked files
for drift before checking the client. Final combined auth/app OpenAPI remains
incomplete; do not hand-edit a second client from these tables.

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

## Scope draft save (unmounted)

`saveScopeDraft`, `POST /projects/{project_id}/setup/scope`, is an admin-only
full replacement of the scope step: expected project revision, name, scope,
domain, representation, intended use, specificity and permitted source use.
It cannot set providers, spend, human approval or publication fields. Send a
new idempotency key for an edit; retries return the original revision, while a
changed body using the same key conflicts. Refresh `readProject` after saving.

The canonical adapter locks the owned project and commits scope/domain/policy
and an attributed append-only audit/retry receipt together. It stores structured
metadata in the existing canonical project policy, not a UI or second database.
Reads expose it only when it matches the authenticated workspace's latest
attributed scope-save receipt. A save invalidates every setup approval and
removes automatic-acceptance qualification; it never approves a class or step.
Until the separate approval/publication producer is implemented, any project
with an edited scope draft fails closed on a new dataset freeze, even if the
old scope text and prior completeness assessment still match. Existing frozen
datasets remain immutable historical records. Other setup steps remain missing
except for the separate guide draft save below.

## Guide draft save (unmounted)

`saveGuideDraft`, `POST /projects/{project_id}/setup/guide`, replaces the free-text
annotation guide and structured draft policy: visible extent, truncation,
occlusion, reflections, positive minimum target pixels, exhaustive-scope intent
and background policy. It requires the expected **project** revision and a new
idempotency key for each edit. Estimated hidden extent is not implemented and is
rejected, as are unknown approval, publication, identity or spend fields.

The canonical project lock, scoped retry receipt and append-only attributed
transaction are shared with the other project mutations. Scope and guide drafts
preserve each other but consume one aggregate revision sequence. Guide saves
clear setup approvals and automatic-acceptance qualification, including saves
that leave the free-text guide unchanged. `readProject` exposes a structured
policy only when it matches the latest workspace-scoped attributed guide-save
receipt; the bootstrap guide alone still yields `guide: null`.

This records draft intent only: `exhaustive_for_scope` does not create an
image-completeness assessment, and `min_target_px` does not modify an immutable
compiler recipe. Approval/publication must bind and validate their actual
policies before use. A guide-edited project cannot freeze a new dataset until
that separate producer exists; historical frozen datasets remain readable.

Real asset-admission composition also requires accepted Security evidence for
the exact consuming head. This preparation does not enable real admission.

## Remaining commands and accountable producer boundaries

The following are acceptance gaps, **not implemented routes or enablement flags**.
The management facade integrator owns the setup command/persistence adapter;
there is no alternate UI-side store or generic `values` passthrough endpoint.
Before live wiring these require typed payloads, admin gates, aggregate revision/
idempotency/audit tests and disposable Actions DB concurrency evidence.

| Fixture command family | Producer and intended implementation boundary |
|---|---|
| Remaining `save_setup`, `add_class` | Management facade: source and class draft writes remain missing; typed scope/guide saves above are implemented separately; stable class IDs and edits invalidating dependent approvals are required |
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
