# Versioned fixture backup and isolated recovery

Plan §12 and acceptance check 21. This is an offline recovery contract and an
Actions drill, **not an operational or production restore command**. No endpoint,
provider call, credential creation/rotation, deployment or live restore is added.

## Inventory v1

`labelloop_api.backup_manifest.BackupManifest` pins format 1, inventory `m1-m2-v1`
and Alembic `0002_local_models`. `backup_store.COLUMNS` explicitly registers all
21 delivered canonical and model tables and every column. Empty tables are
included. Unknown database tables/columns and unsupported migrations fail closed;
new tables must register their inventory and obtain non-empty drill coverage before
milestone completion. Later auth, secret, budget, queue and Platform tables are not
implicitly included. Their owners must integrate an explicit data-only recovery
contract, encryption/custody policy and tests before updating this version.

The snapshot includes:

- Source objects under their original content-addressed identity, all canonical
  annotation revisions, completeness/review events, ontology and source split policy.
  “Original” here is the delivered M1 orientation-normalized immutable RGB source,
  not a raw upload that M1 does not retain. No source or derivative is edited.
- Reference source pixels through source FKs, crop, approval and provenance.
- Frozen releases and recipes, complete compiler manifests, class mappings, hashes,
  derived transforms and exclusions. Derivative pixels/labels are regenerated.
- Initialization/checkpoint/model/export bytes under SHA-256 keys; training and
  evaluation records, model-evaluation links, recipe-specific heads, promotion
  before/after payloads and rollback pointers. Filenames and temporary URLs are not
  durable identities. Existing local artifact files must first be registered under
  the digest recorded by their delivered model/training contract.
- Canonical/review/promotion audit events and annotation history, with row counts,
  canonical table hashes and snapshot-time hash-chain seals. Annotation revisions
  and registry event revisions must be contiguous; registry before/after records
  and head pointers must agree. Append-only evaluation enrichment is allowed between
  registry events; immutable model payloads and existing evidence cannot change.
  Every event scope must retain its registry head. Existing M1/M2 events have no persisted cryptographic
  append-chain, so seals establish snapshot integrity, **not proof against omission
  before capture**. Keep the approved root digest outside the snapshot. A digest
  supplied only by an untrusted snapshot is not an authentication mechanism.

Capture requires one caller-owned `REPEATABLE READ`, read-only transaction.
Immutable object bytes are verified against their content keys. Manifests record
the compiler's actual decoder/encoder/interpolation/library toolchain; restore
rejects a changed toolchain rather than quietly producing different bytes.

Bounds: 100,000 rows per table, 256 MiB per object, 512 MiB total snapshot including
manifest. This in-memory fixture implementation does not stream a production
backup. An operational implementation needs bounded streaming, an authenticated
root and a consistent capture/retention policy; do not lift the fixture limits or
reuse this tool against production to work around that prerequisite.

## Encryption and key custody

Version 1 accepts only `fixture-plaintext`, `fixture_only: true`, with
`provider_and_private_keys_included: false` and
`key_custody: external-prerequisite-no-key-export`. This honestly describes the
licence-clean fixture artifact, not encryption that has not happened. No encryption
algorithm, wrapping key or credential is invented or exported by this work.

Operational backup/restore is **not ready or authorized** until the responsible
security/operations authorities establish encrypted storage/transport, encryption
algorithm and wrapping-key identifiers, independent authenticated root custody,
retention/access controls and tested recovery of those wrapping keys through their
ordinary authority. Provider plaintext and private keys must never be added to a
manifest, fixture, artifact or log. The manifest parser rejects credential-bearing
field names, known key/private-key forms and credential/query-bearing URLs as
defense in depth; it is not a complete secret detector and does not make arbitrary
application JSON safe to publish. Encrypted secret records need a future explicit
inventory extension, never an automatic table dump.

## Disposable drill and evidence

The `recovery-drill` job in `.github/workflows/ci.yml` is part of `ci-ok` and uses
existing locked dependencies and the existing pinned PostgreSQL/MinIO runtime.
There are no new dependencies or services beyond the job lifetime. All full
verification runs on GitHub Actions; no paid calls or ML training occur in this
recovery drill. Opaque synthetic checkpoint/export bytes exercise recovery and
hashing, not model quality or export qualification; the separate real CPU ML smoke
retains those checks.

`apps/api/tests/test_recovery_drill.py`:

1. Starts two random `recovery_<uuid>` PostgreSQL databases and fresh
   `recovery-<uuid>` buckets on the job's fixed loopback fixture services. Migrates
   each DB through Alembic without dropping/replacing any existing schema.
2. Seeds every registered table with the CC0 interface fixture, canonical evidence,
   two annotation revisions with interleaved review events, synthetic model/evaluation
   lineage, appended evaluation evidence and two successive promotions per recipe.
3. Captures one fixture snapshot; removes the source bucket before restoring.
4. Rejects missing/corrupt objects, wrong manifest roots, stale schema versions and
   truncated registry audit chains, before writing accepted restore state.
5. Restores to a schema-current, empty target under exclusive table locks. Any
   non-empty target or non-disposable/non-loopback target is refused. Writes use
   ordinary INSERTs and existing database constraints/triggers, never disabled
   guards, upserts, source overwrites or external endpoints selected by a manifest.
   Annotation revisions replay in order with each review inserted while its expected
   revision is current; foreign-key table order alone is not sufficient.
6. Rehydrates compiler inputs from restored PostgreSQL/object storage, compiles 320
   and 640, and compares **every file byte hash**, including sidecars/manifests.
   Recovers model and evaluation lineage, then rolls back 320 with its full payload
   while verifying 640 remains unchanged.
7. Drops only invocation-owned databases and buckets in `finally` blocks. The job's
   `always()` teardown removes its Compose containers and volumes. A failed partial
   restore is discarded, never resumed into an existing target.

The seven-day `recovery-drill-evidence` Actions artifact contains the backup
manifest, JUnit result and `restore-report.json`: exact PR head, run URL, inventory
counts, object count, restore/total durations, byte-identical recipes, full rollback
payload and cleanup completion. Record the exact-head run/job and artifact links
in the PR and source task after the run finishes. A missing/failing drill or artifact
is a gate failure, not a claim of recovery success. On failure JUnit remains useful;
the success report is deliberately written only after all assertions and cleanup.

## Runtime inventory

No preview runtime is introduced: no hosted app, always-on service, host timer or
idle spend. PostgreSQL and MinIO exist only inside the bounded 15-minute Actions
job, with a 120-second readiness deadline, a single test invocation and unconditional
cleanup. Existing application development runtime is unchanged. Operational
credential-bearing restoration and any future preview/deployment retain their
ordinary security/release authority.
