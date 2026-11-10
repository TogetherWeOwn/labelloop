# Management consumer contract (preparation)

This is an unbound facade contract, not an available production API. The route
factory is deliberately not mounted until the shared session/CSRF and real
transaction/queue adapters are integrated. Recording-port tests prove dispatch
and serialization only, not persistence, concurrency, authorization within a
project, or the CPU project-to-export acceptance path.

## Explicit local configuration (opt-in only)

`publishConfigurationSnapshot`, `POST /projects/{project_id}/configuration-snapshots`,
accepts the expected project revision, a new version, the current approved setup
summary digest and a reason. It reuses the canonical snapshot publisher: current
five-step setup, human anchors, locked source-group provenance and trusted server
training/recipe/checkpoint/inference profiles are rechecked and pinned atomically
with attributed audit. A response is an acknowledgment of the draft project revision,
not activation. Exact retries acknowledge historical receipts without renewing them.

`readConfigurationSnapshot`, `GET /projects/{project_id}/configuration-snapshots/{version}`,
resolves the explicitly selected version's stored pin, then verifies the complete
receipt and freshly prepared current evidence in one SELECT-only repeatable snapshot.
It returns only project/version/digest, approved setup digest, profile IDs and explicit
`runtime_binding: null` / `qualification: not_assessed`. It exposes no internal snapshot,
source locations, provider data or retrieval authority. Stale, duplicate, corrupt or
missing evidence refuses; it never silently selects another version.

`publishLocalConfiguration`, `POST /projects/{project_id}/local-configuration-releases`,
accepts an expected project revision, explicit configuration pin and reason. The
trusted application must separately configure the exact project/version/digest in
`CanonicalManagementOperations.configurations`; a request cannot create that binding
or select a backend. The existing local-only publisher verifies current evidence and
records fixed `local_ultralytics` preparation authority, with automatic acceptance off,
quality unassessed and promotion targets null. Both mutations require shared human-admin
sessions/CSRF and attributed revision/idempotency controls; reviewers can read only.

For a server-bound project, `readFreezePlan` and `freezeRelease` use the same local
publication verifier as bound training submission. Planning is SELECT-only and returns
its verified `configuration` pin alongside authoritative membership; POST repeats all
checks under canonical project/source locks and retains the pin in its freeze receipt.
No active publication, stale setup/profile/source evidence or mismatched pin grants
clearance. Unbound callers retain the existing draft refusal. Dataset releases still
need eligible labels and nonempty validation membership. Application construction
performs no I/O and does not auto-register workers or mount management by default.
This is local preparation, not a measured pilot, bulk-reference retrieval, qualification,
automatic acceptance, production runtime provisioning or real asset-admission clearance.
`readProject.published_release_id` remains null rather than conflating this scoped local
configuration with the complete project-release/pilot lifecycle.

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
| `readRecipePreview` | `GET /dataset-releases/{release_id}/recipes/{recipe_id}/preview?asset_id=...` | Frozen-source planning or verified compiler geometry, explicitly distinguished by `evidence_state`; core `AffineTransform`, transformed boxes, size flags and nullable publication artifacts |
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
The current assignment pin and counts also require the same canonical membership
constraints as freeze: exact hashes, declared ingest source groups and all persisted
relation edges must agree, including transitive edges and excluded sources. A
contradictory or cross-project edge withholds the assignment version/group/split
counts instead of presenting a usable pin. Image counts and authorized pending-pair
observations remain available. Intact groups can still be observed with pending
suggestions; those suggestions continue to block freeze. These are SELECT-only
observations, not assignment publication, readiness or qualification.

`collect_source_grouping_evidence` prepares byte-derived input for the delivered
core grouping engine. It requires matching content-addressed source bytes, recorded
dimensions and a fully decoded, orientation-normalized RGB JPEG before computing
core `phash_grayscale`. It neither transposes again nor reencodes the original.
It retains only supported caller-resolved canonical metadata and observed class
counts; empty counts are not verified background. Embeddings remain absent, not a
claim of embedding-model coverage. Byte and pixel bounds fail closed; even a valid
large normalized original needs a separately bounded processing path rather than
a decoder-limit override.

This internal collector is not a mounted read, admission path, grouping decision or
assignment publisher. Its caller still must authorize the project, resolve current
source/annotation/policy pins and preserve declared source groups and persisted
relations. No split lock or usable assignment version comes from these observations;
publication, pending-pair handling and historical holdout protection remain separate
transactional prerequisites.

`publishInitialSourceGrouping` (`POST /projects/{project_id}/source-assignments/initial`)
exposes the bounded first-publication adapter only in explicitly composed applications.
The strict body pins `expected_revision`, current `ontology_id`, the source-policy
intent digest and exact `annotation_revisions` for every source (zero means no
revision, not verified background). The command accepts no split, seed, threshold,
observed metadata, class counts, object key or assignment-version authority. It uses
the existing authenticated admin/session/CSRF boundary and actor-scoped retry key.
Reviewer reads remain available, but reviewers cannot publish assignments.

Trusted server construction must supply `CanonicalManagementOperations.grouping_objects`
with a callable `read`; the canonical upload store is not implicitly treated as a
reader. Absent configuration returns `management_unavailable` without object or DB
access after actor/context validation. This capability does **not** enable original
asset admission. Object keys resolve from owned canonical rows, never requests.
`discoverInitialSourcePairs` (`POST /projects/{project_id}/source-pairs/discover`)
uses the same strict `InitialSourceGrouping` body, explicit trusted reader and
admin/session/CSRF/revision/retry boundary. It computes current canonical pHash band
suggestions through the existing core grouping engine and atomically saves pending
pairs, setup/automatic-acceptance invalidation and a human-attributed receipt. It
returns the project revision acknowledgment, not assignments or resolved decisions.
Authorized `readProject.sources.pending_pairs` supplies server pair IDs for the
existing `resolveSourcePair` operation. No request selects endpoints, observations,
thresholds, split assignments or a human decision. Empty discovery also records a
receipt; exact retry acknowledges it without renewing evidence.

The grouping publisher atomically commits locked assignments, conservative relation
closure, setup/automatic-acceptance invalidation and the attributed receipt. Prior
assignment/reference history refuses. Persisted pairs require a current attributed
discovery and one complete human decision receipt per pair, matching freshly
recomputed source/annotation/policy/toolchain evidence. Pending, stale or corrupt
receipts refuse. Related decisions retain their exact edge; unrelated decisions
never remove automatic, declared or transitive constraints. There is no regrouping
or holdout quarantine bypass. The existing ingest command accepts optional `source_metadata`:
only `session`, `sequence` and `subject`, with bounded nonempty identifier values.
These are attributed declarations, not inferred identities or assignment authority.
The first-grouping adapter reads them from the owned ingest receipt and requires
every supplied key to be declared by the current source-policy draft before object
I/O. It never drops an unselected relationship silently. Core grouping applies
these values transitively alongside declared groups and persisted relations;
observed values and keys are pinned in the assignment receipt. Older ingests without
metadata retain empty observations and their original retry fingerprints. Changing
metadata under an existing retry key conflicts. No metadata-edit, regrouping or
holdout-repair shortcut is added. Embedding evidence and the complete later-ingest,
metadata-edit, regrouping/quarantine lifecycle remain missing. Default application
mounting and real asset admission stay disabled.

## Internal anchor-pack preparation (not publication)

`prepare_anchor_pack` assembles the existing core `ReferencePack` from current
canonical evidence, not enrollment metadata alone. The trusted caller supplies an
explicit bounded `AnchorPackPlan`: pack identity/version, current ontology and
assignment, reference IDs, exact current annotation revisions, scene, diversity
cluster, validity tags and finite utility. These descriptors are declarations,
not model quality measurements, gold adjudication or automatic-acceptance grants.
Only positive human-reviewed anchor examples are supported in this increment;
confusion negatives, automatic expansions and gold remain separate producers.

The loader requires receipt-verified current reference acceptance, owned workspace,
complete locked grouping and canonical source inventory. Historical holdout checks
remain in the shared reference validator. It reads each selected source's latest
annotation and matching completeness evidence; stale pins, incomplete/rejected
labels, unsupported classes and out-of-frame geometry fail closed. Core source-bound
revision, memory and pack validation also require a complete positive class instance
and refuse evaluation-group evidence. In particular, an approved reference attached
to a verified negative image does not become a positive memory example.

Mutation callers own authorization, the project lock and transaction; source locks
synchronize with existing review writers. A read-only caller must explicitly disable
source locking and use one repeatable snapshot. Preparation writes nothing and
fetches no objects.

`publishAnchorPack` (`POST /projects/{project_id}/reference-packs`) exposes the
existing `CanonicalManagementMutations.publish_anchor_pack` transaction only in
explicitly composed management applications. Its strict `AnchorPackPublish` accepts an expected project revision,
plan and reason, never a caller-authored snapshot or actor identity. The existing
human-admin/workspace/project/retry locks and append-only event authority apply.
The server prepares current source evidence under locks and stores a deterministic
JSON-native snapshot, SHA-256, plan, current reference-context digest and reason in
one attributed canonical event. The snapshot includes internal source lineage and
is not a public read. It is capped at two megabytes. A project-scoped pack ID/version
cannot be republished under another key; a new version appends history. Exact retries
return the original project receipt without revalidating or silently renewing the
pack. Policy invalidation and the event commit atomically; setup approvals and any
automatic-acceptance grant clear. The project remains draft.

The generated command uses shared human-admin/session/CSRF authorization and an
actor-scoped `Idempotency-Key`; reviewers may read project details but cannot publish.
Clients supply reference IDs and annotation pins plus bounded scene/diversity/tags/
utility declarations, never source objects, snapshots, approval, split or gold claims.
The server still rechecks all canonical evidence. The response contains the draft
project revision only, not the internal pack snapshot or usable retrieval authority.
The mutation adds no snapshot GET, retrieval binding, project release, freeze clearance,
admission, worker startup or default mounting. Historical publication does not prove
current eligibility after a later reference, annotation, grouping or context change.
`read_current_anchor_pack` is an internal-only verifier requiring an explicit pack
ID/version. Read callers use one SELECT-only repeatable-read transaction; the
canonical approval mutation instead owns the project lock and requests source locks
under READ COMMITTED. Its caller owns authorization and the owned project row; it
is not a public detail or download port.
The bounded publication history must have unique identities and valid plans. The
selected receipt must match human attribution, workspace/project, revision/result,
current reference context and the freshly prepared canonical pack. The verifier
rehashes the saved JSON snapshot as well as recomputing current evidence; a matching
stored digest alone cannot authorize altered content. Current reference lifecycle,
annotation/completeness, ontology, source inventory and locked grouping are rechecked
without locks or object I/O. Later revocation or changed evidence refuses consumption;
it does not rewrite the historical publication. A historical retry still acknowledges
the original receipt, never renews current eligibility, and cannot fall back to some
other version. Unchanged explicitly pinned versions may coexist.

`readAnchorPack`, `GET /projects/{project_id}/reference-packs/{pack_id}/versions/{version}`,
uses this verifier behind the shared human-admin/reviewer session boundary. Explicit
pack/version selection is required; no latest-version fallback is inferred. The
SELECT-only repeatable snapshot includes project ownership, current management
revision and all pack verification. The strict summary exposes only project/pack/
ontology/assignment identity, the recomputed snapshot SHA-256, example counts per
class and `qualification: not_assessed`. Counts are reference examples, not objects,
independent gold, measured quality or coverage. No snapshots, source pixels, source
URIs, object keys, scene/tag declarations or retrieval/download authority cross this
port. Missing publication, stale evidence or corrupt receipts refuse rather than
return a historical or partially verified summary. Inaccessible projects are not found.

`approveSetupStep` also accepts `step: evidence` with an explicit `anchor_pack`:
pack ID, version, ontology ID and assignment version. For this step only,
`draft_sha256` is the verified summary's `snapshot_sha256`, not a browser-computed
hash of metadata. Other steps reject pack pins; evidence refuses absent or partial
pins. Unchanged old-step serialization preserves historical retry identities.
The admin mutation locks the project and canonical sources in READ COMMITTED,
reassembles current evidence and verifies the same publication before atomically
saving the approval digest and attributed receipt. Reads recheck that receipt's
explicit pack against current evidence inside their SELECT-only repeatable snapshot.
Reference evidence validation is beneath setup approval, avoiding recursive approval
self-validation; public reference/pack reads retain setup validation at their boundary.
Later publication or setup/reference writes invalidate approvals; later source-label
changes make the saved evidence approval fail closed without rewriting its history.
Historical replay is acknowledgment, never renewal. Evidence approval remains draft
intent: it grants no pilot qualification, bulk binding, project publication or freeze
clearance. The default app and real admission remain disabled.

All five setup approvals require a fully validated attributed receipt: the owned
project/workspace, human actor, approval step/body, aggregate revision and draft
project result must agree before approval is projected or consumed by another
approval. A tied latest revision is ambiguous and refuses; older distinct approval
history remains valid history, not a current fallback. This check adds no new
approval or publication authority and changes no historical request serialization.

`readProject` supplies `setup_summary_sha256` only when all five setup steps have
current verified approvals. The versioned deterministic digest binds the project
and workspace, all five approved content digests and receipt revisions, and the
current ontology/assignment pins. Draft/catalog loaders and the evidence-pack
verifier run first inside the same authorized SELECT-only repeatable snapshot.
Missing steps return null; inconsistent or stale evidence refuses rather than
producing a partially verified summary. Editing a draft clears the summary with
its approvals. The digest is computed, never persisted as another authority.
It is an approved-setup **summary**, not a project release, executable configuration,
pilot assessment, source-policy/grouping-production proof or durable grant.
`published_release_id` stays null; recipes/runtime policies, final publication and
bulk binding still require their own validated producer composition.

This supplies current evidence validation and bounded approval, not automatic
bulk-job binding, retrieval or freeze clearance. A consuming
transaction must own its snapshot and locking policy; a successful read is not a
durable grant against future changes. Confusion negatives, automatic expansions,
gold and cross-project imports remain unavailable here.

Future real adapters must select only approved scalar training argument keys,
never raw logs/provider dictionaries containing paths, tokens or signed URLs.
Artifacts are application IDs authorized through the common artifact boundary.

The schema/client generator includes these models and operations in its Actions
artifact and the checked-in `apps/api/openapi/management.openapi.json` and
`apps/web/src/generated/management-client.ts`. The companion
`apps/api/openapi/application.openapi.json` uses the real opt-in application
factory to include the shared auth and health routes without binding operations
or changing the default application. Actions checks all three tracked artifacts
for drift before checking the client. Composed schema publication is not final
adapter integration or route availability; do not hand-edit a second client from
these tables.

## Frozen recipe planning (unmounted)

Before a variant is published, `readRecipePreview` returns `evidence_state: planned`
for selected training/validation sources of an owned, attributed frozen dataset.
It reuses the compiler worker's historical snapshot loader, reconstructing and
rehashing exact source, annotation, ontology and locked-assignment pins inside
one SELECT-only repeatable-read transaction. Newer reviews do not replace frozen
labels. Final-test sources, unselected exclusions, foreign sources, missing or
ambiguous receipts and changed frozen content fail closed.

Planning resolves only the two code-owned full-frame recipes. Core geometry
supplies actual raster-edge transforms and target-size flags. A target below
four derived pixels is flagged `exclude`; targets at or below six pixels are
counted as tiny. An all-below-minimum source is forecast as excluded, while a
mixture of retained and below-minimum targets explicitly fails the geometric
size check rather than hiding the difficult target or labeling it background.
No annotation, source, grouping, recipe, job or release is changed.

A plan has null manifest/validation artifacts and unknown compiler-publication
and model-qualification checks. It decodes no pixels, validates no file and does
not prove a nonempty compiled validation set. On committed compiler publication,
the same route returns `evidence_state: compiled` with the existing fenced
publication checks and artifacts for emitted sources. A selected training/validation
source marked excluded by that same verified manifest returns `evidence_state:
excluded` only if its frozen full-frame geometry confirms every target is below
the recipe minimum. Its boxes are explanatory projections, **not an emitted
raster**. Artifact IDs identify the variant's manifest/validation report, not a
source image or download grant. Negative, mixed-size, unselected and final-test
sources cannot use this exclusion path. Neither read fetches object bytes.
Invalid published evidence never falls back to a plan. This is frozen full-frame
geometry planning, not editable crop/ROI
planning or a preview of unpinned labels, and does not enable real admission or
default mounting.

## Local model publication (unmounted)

`CanonicalLocalModelPublisher` composes the existing training completion sink
with candidate-model persistence in the same queue application transaction. It
requires saved `training` and `export` stages; it is not an additional queue or
scheduler. The trusted ML stage must call the delivered `export_and_compare`
on the selected checkpoint and immutable recipe images, then pass its actual
ONNX file/evidence through `collect_export`. The collector validates the fixed
signature, policy, detection evidence, tolerances, file hash and size before a
content-addressed upload. It does not itself prove ONNX equivalence.

The completion sink revalidates the owned historical training/compiler intent,
derives model identity/classes/preprocessing from that training, and reuses the
model store's lineage/export gate. Training, development reports, candidate,
evaluation links, attributed completion/export audits and the queue application
fence commit or roll back together. No model identity, path, class, recipe or
registry selection comes from exporter output. Candidate publication never
promotes it, supplies quality targets, opens a final test or updates a champion.
A runtime needs only the existing model/evaluation SELECT and INSERT grants,
not registry writes or audit mutation privileges.

Export-stage orchestration and final worker registration are still unbound.
Synthetic database artifacts exercise transaction boundaries, not actual queued
training/export acceptance. The separate CPU smoke passes real controlled
export output through the collector; the complete new-project-to-export path
and exact-head Security/QA acceptance remain required before default mounting.

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

## Class catalog publication (unmounted)

`publishClassCatalog`, `POST /projects/{project_id}/setup/classes/publish`, creates
one new immutable ontology version from the saved catalog. Send the current
project revision, `versions.ontology_id` as `base_ontology_id`, the server-projected
`setup_draft_sha256.classes`, and a reason. Class definitions, indices, identities
and approval fields cannot be supplied by this command. Refresh `readProject`
after the mutation; the project resource remains `draft`.

The owned-project/retry locks and atomic attributed receipt are shared with draft
saves. Publication checks the latest saved catalog receipt and exact digest. All
existing stable IDs must remain: removal, merge and split migrations require a
separate explicit migration path and are refused here. Existing export indices
are preserved regardless of draft order; added stable IDs receive dense indices
in sorted order after the existing map. Earlier ontologies, annotations,
completeness assessments, frozen datasets and descendants are never rewritten.
Names, definitions, aliases and parents enter the canonical ontology. Prose
examples, exclusions and mirror-safety intent are retained in the publication
receipt, not silently converted to reference support or training configuration.

Every enrolled reference must first be explicitly withdrawn, including stale
accepted and proposed evidence. Publication never repins or reapproves it. New
enrollment must pass all current source/holdout/context checks. Publication clears
setup approvals and automatic acceptance, consumes the draft and records a
receipt-bound current ontology marker in canonical project policy. Project reads
verify that marker, publication receipt, consumed draft and canonical ontology
agree; new drafts must pin the new version. Retry returns the original resource,
not another ontology version. The read projection remains SELECT-only.

This publishes the **catalog only**, not a complete project configuration or a
qualification. `published_release_id` remains null, class quality approval and
reference-support fields remain unknown, and edited projects still cannot freeze
new datasets until the remaining setup/project-publication gates are implemented.
No real admission, runtime grant, retrieval, automatic migration or spend is enabled.

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

## Reference lifecycle recovery read (unmounted)

`readReferenceLifecycle`, `GET /projects/{project_id}/references/lifecycle`, is a
separate human admin/reviewer read for recovering server-issued reference IDs and
last approval revisions after reconnecting or losing a mutation acknowledgement.
It uses one SELECT-only REPEATABLE READ snapshot, scoped to the trusted workspace
and an existing managed project. It validates the existing enrollment, acceptance,
renewal and withdrawal chain against persisted reference rows. Corrupt or missing
history fails closed. A bank is bounded to 1,000 references; no truncation or
unbounded history response is permitted.

The response carries the project revision and each reference's source/class IDs,
historical state (`proposed`, `accepted` or `withdrawn`), historical ontology and
assignment pins, and nullable `last_approval_revision`. Every entry explicitly
has `eligibility: not_assessed`. These are **historical recovery pins**, not current
ontology/grouping facts, approved references or retrieval input. No provenance
text, URI/object key, context digest, raw event payload or actor identity is
returned. An empty bank is an explicit empty list, not missing authorization.

This read intentionally remains available when current scope, guide, source policy,
ontology, holdout or pending-pair evidence invalidates the normal project detail.
It cannot bypass those checks: `readProject.references` still refuses stale usable
approval, renewal still validates all current evidence, and withdrawal still needs
admin authority, expected revision, a fresh request key and intact history. An
already withdrawn reference cannot reactivate. Use current authorized producer
pins for renewal, never assume these historical pins are current. There is no
state mutation, approval, pack publication, qualification or admission authority.
The default app remains unmounted; source cases require exact-head Actions evidence.

## Reference revalidation (unmounted)

`revalidateReference`, `POST /projects/{project_id}/references/revalidate`, renews
an existing admin-approved reference against current context and source pins.
Send the expected project revision, reference ID, current ontology and assignment
IDs, a reason, and `previous_approval_revision` from the last successful acceptance
or renewal's resource revision. Keep that acknowledgement; current project revision
alone does not identify the approval being renewed. A fresh key is required for a
new renewal, while exact retries return the original acknowledgement.

The transaction uses the existing owned-project/retry locks and source validator.
It validates immutable enrollment and the complete attributed approval/renewal
chain, then rechecks current ontology/classes, locked grouping, pending pairs and
historical holdout exclusion. It never replaces enrolled source, class, provenance
or validity metadata; changed metadata requires withdrawal and fresh enrollment.
Proposed/withdrawn references and unchanged approval pins cannot be renewed. Class
publication still requires retirement of all references; this is not an ontology
migration or a way to reactivate retired evidence.

Only a new atomic attributed receipt and project approval invalidation are written;
the reference row, original enrollment/acceptance receipts and historical releases
are unchanged. Reads validate the latest renewal's context and source pins, never
fall back to an older valid approval, and remain SELECT-only. A later context or
source change fails closed again until explicitly renewed or withdrawn. No pack,
retrieval, admission, automatic acceptance, qualification or publication is enabled.

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
unwithdrawn accepted reference; the history-only loader is private to retirement
and the catalog publication's all-retired precondition, never public approval reads.

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

## Source-policy intent approval (unmounted)

`approveSetupStep`, `POST /projects/{project_id}/setup/approvals`, also accepts
`step: sources`. Refresh `readProject` for the current project revision and
`setup_draft_sha256.sources`; submit that exact server-computed digest, a distinct
idempotency key and a bounded reason. The digest pins the saved source-policy
metadata keys and collection notes, not current grouping or split evidence.

Approval requires the current attributed draft and reuses the existing admin,
workspace, project-lock, replay, revision and atomic receipt checks. Approval
composes with scope/guide/class intent approvals. Subsequent setup/reference
changes clear all approvals, including a byte-identical source-policy save;
restoring an old policy value cannot resurrect a stale approval. Project reads
validate the receipt and current digest without writing state.

This approves **intent only**. It does not compute relations, resolve pairs,
create or lock assignments, change any source or historical release, publish a
project/reference pack, grant eligibility, enable admission or qualify automatic
acceptance. Observed source/group/split fields remain separate from draft intent.
Effective grouping and project publication remain producer prerequisites. The
new source-policy cases require exact-head Actions evidence; source is not a pass.

## Source-pair decisions (partial, opt-in)

`resolveSourcePair`, `POST /projects/{project_id}/source-pairs/resolve`, accepts an
expected **project** revision, a server-returned pending `pair_id`, `decision`
(`related` or `unrelated`), a bounded reason and an idempotency key. Project details
already expose pending pair IDs. Endpoints, metric, observed value and algorithm
come exclusively from the owned canonical pair row, never from client claims.
The shared human-admin/session/CSRF boundary, project/retry locks and atomic
attributed receipt apply. Exact retry returns the original project resource.

`related` records the existing metric's conservative relation and resolves the
suggestion in the same transaction. If either endpoint already has assignment
history, **every** version must include both endpoints in the same group and split
under one policy. Partial, different-group/split or inconsistent historical
assignments refuse with `missing_evidence`; they need the still-unimplemented
regrouping/quarantine producer. A compatible current version cannot override an
incompatible old version. `unrelated` dismisses only this suggestion: it never
removes an automatic relation or separates a conservative/transitive group.

Either decision clears setup approvals and automatic-acceptance qualification;
restoring old approval policy cannot revive its receipt. No source/annotation,
assignment, frozen release, worker, default mount or admission gate changes.
This is persisted human relationship evidence, not source-image/metric validation,
materialized grouping, split assignment, project publication or qualification.
The new DB and transport cases require exact-head Actions verification.

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
| Remaining `resolve_pair` regrouping | The typed decision above persists compatible pair evidence only; management still owns assignment publication and revisioned split changes/quarantine using the delivered grouping policy |
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
