# Canonical annotation reads

This is the first bounded review-API surface after the authentication foundation.
It is not the complete review view, a write API, a completeness decision or an M3
acceptance claim. It uses the existing single-organization admin/reviewer access
model. No new accounts, service scopes, provider calls or credentials are created.

## HTTP contract

Both routes require an authenticated **human** with the `review` permission.
Admin and reviewer sessions qualify; all four worker principal types are refused
before the annotation connection is acquired. The existing authentication layer
records permission refusals and applies `Cache-Control: no-store` to every response.

- `GET /projects/{project_id}/assets/{asset_id}/annotations` returns the latest
  committed annotation revision, canonical source hash, width and height. Coordinates
  remain in the original orientation-normalized source frame. The latest revision
  and source metadata are selected in one statement, not assembled from separate
  reads of a changing latest-revision pointer.
- `GET /projects/{project_id}/assets/{asset_id}/annotation-history` returns
  descending immutable revision records. `limit` defaults to 20, accepts 1–100,
  and the optional `before_revision` is an exclusive positive integer cursor.
  A non-null `next_before_revision` is the last returned revision when another
  record exists. Newer revisions do not shift an in-progress history page.

`project_id` and `asset_id` are the existing persisted `project.id` and
`source_asset.id` UUIDs, not canonical keys or UUIDs computed from those keys.
Use their standard 36-character hyphenated representation as bounded opaque lookup
handles. Ingestion already assigns these IDs to every stored project and asset;
no migration, key rewrite, new resolver table or key alphabet/length restriction is
needed. Snapshot responses return the stored `project_key` and `asset_key` unchanged,
including slashes, Unicode, reserved URL characters and keys longer than 256 characters.
Do not percent-encode a canonical key into either path slot. Future project/asset
listing and ingestion HTTP contracts must expose these existing IDs to consumers;
those discovery APIs are not added by this read slice.

Both project and asset IDs constrain every query. An absent asset, an ID paired
with the wrong project, or an asset without an initial annotation has the same
`404 annotation_not_found` response. An exhausted cursor for an existing annotation
returns an empty page. Invalid query values return the existing redacted
`422 invalid_request`; database failures return `503 annotations_unavailable`
without driver messages. There is no logging of rows, credentials or driver errors.

Each record includes revision/parent, recorded actor identity, timestamp, source-frame
objects and recorded annotation states. It does not upgrade machine-reviewed states
because a human opened the page. In particular, empty objects remain unreviewed unless
an authoritative completeness workflow previously recorded otherwise. The explicitly
named `recorded_training_eligible` and `recorded_gold_eligible` fields are **historical
record fields**, not a fresh release-eligibility, audit-independence or qualification
verdict. Consumers must use the actual current release/qualification gates.

The response is an allowlisted projection. Object-store locations, project access
policy, passwords, sessions and provider configuration are not selected. Existing
object attributes are canonical data, not executable UI content; consumers must
render free text as text, never HTML.

## Verification and remaining integration

`apps/api/tests/test_annotation_reads.py` exercises production application routing,
real stored session/service authentication, SQLite-backed execution of the actual
SELECTs, cross-project lookup refusal, immutable pagination under intervening appends,
zero-object history, retained machine tiers, no-store, bounds and safe driver failures.
Authenticated admin/reviewer regressions read snapshots and both history pages for
slash-containing, long and Unicode/reserved-character canonical keys through opaque
IDs, verify the keys remain unchanged in storage, and refuse mismatched projects.
Malformed and oversized lookup strings are rejected before annotation database access.
SQLite tests do not establish PostgreSQL migration/locking behavior. This change adds
no DDL or write transaction; existing PostgreSQL migration Actions remain mandatory.
GitHub Actions is the CI of record; no local full suite or CI mirror is required.

Pydantic response contracts appear in the application's generated OpenAPI structure;
the public schema route remains disabled. A checked-in generated OpenAPI artifact,
generated web client and drift check remain part of the complete S2 contract delivery,
not established by checking route presence here. Subsequent review-view assembly must
add undecided proposals, approved nonholdout references, queue/audit state and history
with the correct per-object provenance. Expected-revision writes, human completeness,
exclusions, worker-operation scopes, adjudication/audit and the accepted review UI
remain unfinished. Do not use these reads to claim any of those gates are delivered.
