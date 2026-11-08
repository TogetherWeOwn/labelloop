# Qualification evidence and lifecycle contract

Implements plan v0.3 §§4.2, 11.1–11.5 and acceptance checks 15–17/19 as pure
transitions. This is not an operational qualification, an HTTP authorization
boundary, or a claim that a trained fixture model meets production targets.
No plan departure or new production quality target is introduced.

## One registry, stronger promotion gate

`RegistryState.promote` still commits one recipe's complete `ModelRelease` and
rollback pointer. It now requires a `QualificationContext` in addition to the
existing model report, explicit `PromotionPolicy`, expected revision and incumbent
comparison. Omitting the context fails closed. `roll_back` restores the complete
prior payload; it does not grant automatic acceptance or clear drift incidents.
320 and 640 have independent registry and acceptance states.

A qualification pins the exact release payload (including preprocessing, class
map, inference thresholds, export and linked evaluation IDs) and the complete
annotation pipeline: teacher digest, recipe/preprocessing, reviewer model and
provider versions, prompt/schema, references, ontology, geometry and acceptance
policy. The teacher digest is deliberately separate from the candidate weights:
a different qualified recipe may supply proposals. Changing any pinned input
requires new evidence; appending linked evaluation IDs also requires a new
selection/qualification snapshot before promotion.

`QualificationTargets` has null production defaults. Every target, including each
critical class/slice's support, score and regression limit, must be explicit.
Critical slice names resolve to actual `EvaluationReport.slices` entries; aggregate
scores never fill a missing class or slice. The incumbent need not meet today's
absolute gates but must have measured comparison support on the same frozen test.
No metric is treated as certified correctness.

## Annotation and canary evidence

`AnnotationEvidence` reuses the delivered `AuditSample`, `AuditObservation` and
`assess_audit`, not a second audit engine. Missing, dependent, truncated,
non-full-image or adverse observations block promotion, even for apparently
confident ordinary accepted labels. Accepted negatives must be sampled to inspect
missed objects. Ground truth must be independently adjudicated or supplied by a
trusted, aligned system; repeated model agreement is explicitly rejected.

A `ScopeCell` is a class/source/condition combination. Audit measurements must
cover exactly the declared cells. Each measurement binds independent source-group
IDs, accepted/correct label counts, truth/discovered object counts and maximum
normalized geometry error under the pinned geometry policy. Group count, not the
number of objects or repeated votes, supplies independent audit support. Counts
and geometry are trusted audit measurements, not assertions obtained from a model
response. Their measurement protocol and representative sampling are obligations
of the authoritative audit producer, not inferred from this finite test.

A separate canary audit uses the same targets, an explicit minimum fresh group
count and the exact candidate/pipeline. Canary source groups cannot overlap
training, development, calibration, final evaluation, references or the earlier
annotation audit. This implementation conservatively requires completed canary
evidence before registry promotion as well as before expanding auto-acceptance.
There is no invented duration or quality threshold. Fixture values are explicitly
synthetic and never production defaults.

## Evaluation roles and the final-test lifecycle

- Training fits weights; development selects candidates/checkpoints; calibration
  tunes thresholds. `EvaluationSet.require_use` rejects other uses, including all
  training, selection, tuning and retrieval uses of locked final evidence.
- `select_candidate` pins the entire release from matching development evidence
  (and optional isolated calibration evidence) before opening a final assessment.
- `FinalTestState.expose` records the complete canonical report and selected
  candidate. Any exposed information counts, including an aggregate or pass/fail
  bit. The state permits one selected assessment before requiring a fresh final
  release, a conservative implementation of infrequent final qualification.
- If an exposed result guides a later change, `record_adaptation` spends the
  **entire** release: its effective role becomes development/regression. Original
  membership and historical results remain intact. Neither removing failures nor
  renaming a report restores independence.
- `quarantine` preserves results while invalidating qualification after a new
  cross-split relation or other integrity finding. There is intentionally no
  unquarantine/reset transition; reassessment needs fresh independent membership.
- Qualification checks disjoint training/development/calibration/final groups,
  excludes evaluation groups from references, and checks the selection's set IDs.
  Current final-test state must equal the evidence snapshot. An old serialized
  success cannot bypass a later adaptation or quarantine when the current state
  is loaded correctly.

Exposure and adaptation are distinct audit facts. If a UI or worker exposes data
before a transaction fails, its real exposure must still be recorded/reconciled;
a failed request does not make the data unseen. Trusted persistence must save the
selection before granting access to final outputs. Group IDs come from the
canonical transitive grouping version, never caller-provided arbitrary strings.

## Scoped drift and resumption

`AcceptanceState` is an acceptance gate, not another model registry. Drift pauses
name explicit affected cells and retain reason, baseline release/digest and
qualification. Unaffected cells and the other recipe remain eligible. The caller
expands a class-wide or source-wide incident into all affected approved cells;
unknown scope is never implicitly eligible.

Resumption requires a different release identity and new fully passing
qualification/canary covering every paused cell. Missing targets, insufficient
support, stale final state and incomplete canary prevent resumption. Pause history
is retained after resumption. Rolling back to a drift-invalidated release cannot
restore acceptance in that scope, even after a newer release resolved the pause.
The resumption transition checks historical invalidation before claiming success,
not only when later acceptance is attempted.

Different release/qualification IDs alone do not prove post-drift freshness.
Both qualification and canary carry `remediation_for` incident references, binding
the project, recipe, unique pause ID, opening acceptance revision and affected
release digest. Resumption requires an exact reference to each incident being
resolved. A pre-incident context has no such link; copying a new link only onto
qualification cannot reuse the old canary. The trusted producer must construct
these records from a persisted incident **before** collecting remediation evidence,
and persist that causal provenance immutably. Incident links are not request-body
claims or a substitute for authoritative sequencing. A fresh final assessment and
all other qualification gates remain mandatory; incident linkage waives none.

## Durable JSON v1

`qualification_wire.dumps/loads` uses a strict envelope:

```json
{"schema":"labelloop.qualification.v1","record_type":"QualificationContext","payload":{}}
```

The empty payload above illustrates the envelope only and is intentionally not
valid evidence. Supported roots are `QualificationEvidence`, `QualificationContext`,
`CandidateSelection`, `FinalTestState` and `AcceptanceState`. Nested field names
and types are exact; all fields, including nulls and empty optional memberships,
are serialized. Unknown versions, fields, type names, duplicate keys/set members,
non-finite numbers, booleans masquerading as counts, missing fields and records
larger than 2 MB are rejected. Collections serialize as arrays; frozensets are
sorted. Canonical JSON uses sorted keys, compact separators and UTF-8 for hashes.
A digest provides content identity, **not** authentication or independent truth.

No record stores provider secrets, images or weights. Source/policy IDs and the
historical final report are audit data; adapters must apply project permissions.
Durable schemas must migrate deliberately rather than guessing a future version.

## Trusted integration port (not yet an HTTP feature)

The internal `model_store.change_registry` accepts the typed context and writes
its versioned JSON into the same immutable promotion event as before/after state.
Without it, promotion is refused. No endpoint, model response codec, principal
claim, authentication bypass or new database schema is added here.

The privileged integration must consume the authenticated service separately:

1. Authorize an admin in the selected project; derive the actor from the session,
   never a request/model claim. Service/model principals cannot qualify truth.
2. Lock the recipe head, policy/pipeline, final-test lifecycle, grouping version,
   authoritative audit/canary records and acceptance state in one transaction.
3. Load the current champion and persisted candidate/incumbent reports; reconstruct
   the context from those trusted records. Decode-and-validate alone is not enough.
4. Compare expected revisions, evaluate all pure gates and persist head, complete
   release payload, evidence, authenticated promotion event and acceptance changes
   atomically. Replay or a failed write must not leave a changed pointer.
5. Record final access/exposure and adaptation/quarantine before permitting reuse;
   emit a new qualification rather than mutating old evidence. Check the current
   champion and acceptance gate for every automatic-acceptance operation.

Until these authenticated authoritative loaders/locks are implemented, production
promotion callers must omit the context and remain fail-closed. The test helper
`fixtures/qualification_fixture.py` is not a production loader. Persistence smoke
uses explicitly labelled synthetic lifecycle/audit/group records copied from CPU
development outputs solely to exercise transactions, stale revisions, races and
rollback. It does **not** establish independent final-test accuracy of CPU models.

REA angle: reuse delivered registry, evaluator and blind-audit primitives and the
accepted plan's independent-holdout research; no replacement engine or scheduler.
