# M3 review core: first implementation slice

This slice implements the pure, offline contracts for plan §5.4 and part of
§5.2/§11.2. It does not enable model calls or automatic annotation acceptance.
The existing image-review specification in `docs/design/screens/01-image-review.md`
remains authoritative for the later UI implementation.

## Untrusted patch boundary

`labelloop_core.review.decode_patch` accepts a bounded JSON document with exactly
these fields:

- `asset_id`, `expected_revision`;
- `operations`: at most 128 operations, each with `action`, `object_id`, `reason`,
  `evidence_ids` and `representation`;
- `full_image_inspected`, `output_truncated` (strict booleans);
- `unresolved_regions`: canonical source-coordinate boxes.

The actions are `confirm`, `relabel` (`class_id`), `geometry` (`box`), `add`
(`class_id`, `box`), `duplicate` (`duplicate_of`), `delete`, and `uncertain`
(`label_state`). Only the action's listed extra fields are permitted. Reasons are
untrusted plain text; consumers must never render them as markup or execute them.

`validate_patch` checks exact source/revision identity, approved evidence IDs,
class membership, representation policy, finite positive geometry and source
bounds. Multiple operations on one object are rejected atomically. Deletions,
duplicate flags, near-identical boxes (IoU ≥ 0.95, including across classes), and
all model passes against human-reviewed/adjudicated revisions require adjudication;
none returns a replacement revision. Old revisions remain unchanged.

`ReviewContext` is trusted orchestration input, not part of the model response.
Its source, scope/ontology digest, class/evidence allowlists and representation
must come from pinned project records. Discovery must run separately from review,
on the full original image before exposing the teacher's answers; this slice
checks distinct run identities but does not execute or attest that discovery.
Only approved, project-permitted, non-holdout evidence may enter the allowlist.

## Bounded corrections and completeness

`CorrectionLoop.consume` advances one pass without doing I/O. The maximum is one
or two passes, never more. Invalid patches terminate as `failed`; truncation,
adjudication, oscillation and exhausted corrections terminate as `unresolved`.
Changed objects invalidate completeness and need another complete inspection.
The loop binds scope, source and evidence across passes.

An `accepted` loop means **machine review evidence settled**, not accepted labels
or release readiness. Its annotation remains `proposed`, with a machine review
tier, `training_eligible=false`, and `gold_eligible=false`. Qualified policy code
must independently decide any auto-acceptance, and authenticated humans own human
review/adjudication. Even an empty object set needs full-image inspection,
non-truncated independent discovery, and no unresolved regions. Payload and
transport truncation are combined; a model cannot clear a transport truncation.

The caller must persist proposals, old/new boxes, patch reasons, discovery/review
run identity and completeness evidence with an atomic expected-revision check.
The pure loop is not a durable retry counter or an authentication boundary. Worker
leases and the database must retain its pass count/history across restarts.

## Independent audit primitives

`select_audit_sample` samples ordinary accepted annotations without confidence
filtering. Its hash-ranked selection is repeatable for the sample ID, seed and
frozen source/revision pool, independent of input ordering. When the pool includes
both accepted positives and negatives, the requested size must cover both strata.
This is stratified sampling, not an unweighted population error-rate estimator.

`assess_audit` requires one observation for every sampled source/revision and a
reviewer distinct from the original decision-makers. A missing result is pending;
a wrong label, missed object, nonblind inspection or truncated output blocks a
passing verdict. These are trusted audit records; future API/worker adapters must
authenticate principals and record blind-first inspection before invoking it.
The verdict does not yet gate the model registry: that integration is subsequent
M3/qualification work, not a claim made by this primitive.

## Remaining milestone work

Proposal/discovery execution, recorded-fixture provider probes, the LLM budget
ledger and encrypted-key boundary, authenticated review/audit persistence, and the
React review screen are not implemented by this slice. No paid operation is
introduced. Tests for these primitives run through the existing GitHub Actions
unit and strict core-type jobs; no local full-suite evidence substitutes for CI.

REA angle: the merged plan already identifies CVAT and Label Studio as source
references for the subsequent adjudication workflow. No third-party code is copied;
this slice implements the plan's deterministic domain contracts only.
