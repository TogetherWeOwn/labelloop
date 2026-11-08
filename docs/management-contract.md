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

## Class catalog draft save (unmounted)

`saveClassCatalogDraft`, `POST /projects/{project_id}/setup/classes`, replaces
only class-catalog draft intent. Send the expected project revision, current
`versions.ontology_id` as `base_ontology_id`, and a bounded list of proposed
stable IDs, names, definitions, aliases, optional parents, prose positive
examples, exclusions and mirror-safety intent. IDs and aliases must be unique;
parents must resolve within the proposed catalog without cycles. No approval,
class-index mapping, references, publication or spend field is accepted.

The owned project lock, retry reconciliation and attributed transaction are
shared with the other project writes. A missing/foreign/obsolete ontology pin
cannot be saved. Saves invalidate setup approvals and automatic-acceptance
qualification. `readProject.class_catalog_draft` is separate from authoritative
`classes` and ontology version pins: prose examples do not count as approved
reference evidence. The projection requires matching attributed draft evidence
and its still-current base ontology. A draft save never changes an ontology,
released class identity, YOLO export index, reference bank or frozen dataset.

Catalog edits block new freezes until the separate approval/publication and
ontology migration path validates them. Added/removed/merged/split class intent
is not an effective identity migration. Scope, guide and class drafts preserve
each other but consume one shared project revision sequence. Historical frozen
releases remain readable. This does not enable real asset admission.

## Source policy draft save (unmounted)

`saveSourcePolicyDraft`, `POST /projects/{project_id}/setup/sources`, persists
only source-collection intent: a nonempty unique list of the delivered grouping
keys (`session`, `sequence`, `subject`) and bounded collection notes. Use the
expected **project** revision and a new idempotency key for each edit. File
capture metadata, bytes, URLs, storage keys, assignments, pair decisions, class
keys and approval/holdout/spend overrides are not accepted in this operation.
Notes are untrusted text, not executable grouping instructions.

The canonical project lock, retry reconciliation and attributed atomic receipt
are shared with the other setup writes. Saves clear every setup approval and
automatic-acceptance qualification. The receipt-bound `source_policy_draft`
projection is distinct from `sources`, whose observed group/split facts remain
unchanged. Draft keys are not reported as effective `sources.metadata_keys`.
The save neither creates assets nor changes relations, assignments, references,
held-out sets or historical releases. New freezes fail closed until validated
source-policy approval/publication and grouping binding are implemented.
Real admission remains unbound pending exact-consuming-head Security acceptance.

## Proposed reference enrollment (unmounted)

`enrollReference`, `POST /projects/{project_id}/references`, enrolls **metadata
for an existing canonical source**, not a file selection, upload or URL fetch.
Send expected project revision, current `base_ontology_id`, current
`assignment_version`, owned `asset_id`, published `class_id`, source/permitted-use,
view, lighting, scale, validity scope and distinct optional confusion class IDs.
Provenance text is untrusted data and never grants a licence or external access.
No crop, embedding model, approval, reviewer identity or new class is accepted.

The canonical project/retry locks and atomic attributed receipt are reused.
Freeze and enrollment share the same authoritative grouping validator: complete,
current, unambiguous locked assignments, consistent exact hashes/declared groups,
materialized relation closure and no pending pairs. Only training sources are
accepted; historical holdouts, their byte-identical copies and members of their
current groups are excluded conservatively. An unpublished class-catalog draft
cannot supply effective reference identities. Missing grouping binding is a
real prerequisite, not permission to infer browser splits.

Records use the existing `reference_example` table with `approved: false`, no
embedding or crop, and receipt-bound public metadata. `readProject.references`
shows enrollment evidence only when persisted rows and workspace-attributed audit
receipts match. Enrollment alone is `proposed`; acceptance requires the separate
admin operation below. Neither operation publishes a retrieval pack or grants
training/gold eligibility.
Enrollment invalidates setup approvals/automatic acceptance and blocks new
freezes pending validated publication. Existing frozen releases are unchanged.
The mutation returns the project revision; refresh `readProject` for the
server-issued reference ID. Real asset admission remains unbound under its
exact-consuming-head Security condition. Pack publication, revalidation
and retrieval binding remain incomplete; withdrawal is described below.

## Reference acceptance (unmounted)

`acceptReference`, `POST /projects/{project_id}/references/accept`, accepts one
already enrolled reference. Send `reference_id`, expected project revision,
exact enrolled `base_ontology_id` and `assignment_version`, and a bounded reason.
The request cannot replace metadata, source/class identity, permitted use or the
authenticated reviewer. The admin gate, project/retry locks and atomic audit
receipt are shared with enrollment. Retries return the original project resource;
a different request with the same key conflicts. An already accepted reference
cannot acquire a second acceptance through another key.

Acceptance rechecks the current ontology, class membership, complete locked
assignments, relation closure, pending pairs and lifetime holdout exclusion.
Only the existing row's `approved` column changes; enrollment provenance stays
unchanged. The attributed acceptance receipt binds the exact reference, ontology,
assignment, reason and current scope/guide/source-policy context. Project reads
show `admin_approved` only when both receipts and the stored row match, and recheck
current source evidence in their SELECT-only snapshot. Stale context/grouping or
changed ontology fails closed; these are not automatically reapproved. The
withdrawal path below retires stale acceptance; revalidation remains unavailable.
Shared grouping checks are cached only within that read snapshot, not across
requests. No project step approval, publication, embedding, retrieval authority,
model qualification, paid call or real asset admission is enabled. Setup
approvals/automatic acceptance remain invalidated and new freezes remain gated.
The disposable Actions test role alone receives the narrow approval-column grant;
production runtime grants and final composition remain separate gated work.

## Reference withdrawal (unmounted)

`withdrawReference`, `POST /projects/{project_id}/references/withdraw`, retires an
owned enrolled reference using its server-issued `reference_id`, expected project
revision and reason. It can retire either proposed or accepted evidence. No
replacement metadata, approval, identity or obsolete ontology/assignment pin is
accepted. Withdrawal does not need current approval eligibility: stale scope,
guide, source-policy, ontology, holdout or pending-pair evidence must not prevent
revocation. The persisted row and attributed enrollment/acceptance history must
still match. Public reads continue to recheck current eligibility for every
unwithdrawn accepted reference; the history-only loader is private to revocation.

The shared project/retry locks serialize withdrawal with enrollment, acceptance
and setup changes. The transaction sets only the existing `approved` column to
false, clears setup approvals/automatic acceptance, and appends an attributed
withdrawal receipt. Enrollment provenance and past acceptance events remain
unchanged. `readProject.references` retains the metadata with `approval: withdrawn`;
it is not usable approval or retrieval evidence. A row/receipt mismatch fails
closed, even for withdrawn evidence. Retries return the original result; another
key cannot withdraw it again or reactivate it through `acceptReference`. A future
explicit revalidation or new enrollment must validate all current evidence anew.
No reference is deleted, no descendant release is rewritten, and no publication,
qualification, real admission or new production grant is enabled. New freezes
remain gated, and stale *other* accepted references still fail closed until retired.

## Remaining commands and accountable producer boundaries

The following are acceptance gaps, **not implemented routes or enablement flags**.
The management facade integrator owns the setup command/persistence adapter;
there is no alternate UI-side store or generic `values` passthrough endpoint.
Before live wiring these require typed payloads, admin gates, aggregate revision/
idempotency/audit tests and disposable Actions DB concurrency evidence.

| Fixture command family | Producer and intended implementation boundary |
|---|---|
| Remaining `save_setup`, `add_class` | Management facade: proposed reference enrollment and typed setup draft saves above are implemented separately, not generic capture payloads or approval; source admission/grouping binding and effective ontology publication/migration remain missing |
| `accept_class`, `approve_step` | Management facade: authenticated admin events, not client-set `approval`/human fields; `accept_reference` has the separate unmounted typed operation above |
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
