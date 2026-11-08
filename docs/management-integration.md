# Management facade integration

Status: **contract preparation, not an integrated management API**. The default
application has not mounted these routes. The isolated OpenAPI generator mounts
unavailable ports, which return `management_unavailable`; it never substitutes a
fixture store for production persistence. Keep the PR draft until the acceptance
matrix below has real exact-head Actions evidence.

REA angle: reuse the delivered canonical records, orientation-normalizing ingest,
compiler, local training backend, source-frame evaluator and per-recipe registry.
Consume the existing session, append-only event and fenced queue implementations.
There is no new domain engine, scheduler, ledger, authentication store or UI shell.

## Contract slice

`labelloop_api.management.management_router` requires trusted principal and
operations dependencies. The principal is structural against the existing auth
contract (`id`, `kind`, `role`, `permits`). No client request selects a principal,
workspace, endpoint, storage key, class mapping, arbitrary training arguments or
paid backend. Human admins mutate; human reviewers/admins read. Service/model
principals cannot use either human management surface, even with an admin role.
Shared middleware still owns session authentication, CSRF and denial auditing.
The router is **not** a replacement for that middleware.

| Method/path | Port | Result |
| --- | --- | --- |
| POST /projects | create_project | 201 resource |
| POST /projects/{project_id}/assets | ingest_asset | 201 resource |
| POST /projects/{project_id}/dataset-releases | freeze_release | 201 resource |
| POST /dataset-releases/{release_id}/variants | compile_variant | 202 shared job |
| POST /variants/{variant_id}/training-runs | start_training | 202 shared job |
| POST /models/{model_id}/evaluations | start_evaluation | 202 shared job |
| POST /projects/{project_id}/promotions | promote | 200 promotion decision |
| GET /projects | list_projects | bounded resource page |
| GET /projects/{project_id}/resources?kind=… | list_resources | bounded resource page |
| GET /resources/{kind}/{resource_id} | read | resource summary |
| GET /models/{model_id}/export | read_export | artifact identity/digests/static shape |

These read models are deliberately minimal fixture contracts, not a claim that
all accepted management screens can already render their full detail. Detail
fields and downloads must be bound to actual producer records, not fabricated.

Every mutation requires `Idempotency-Key` and an expected aggregate revision
(project creation pins zero). Release requests additionally pin every selected
annotation revision. `MutationContext` binds authenticated actor, operation,
route target and the canonical validated body into a fingerprint; this is an
input to the real transaction, **not itself an idempotency implementation**.

The real adapter must:

1. Resolve organization/workspace and resource ownership from authenticated
   database records. Validate every referenced ontology, assignment, source,
   release, profile, candidate, incumbent and qualification belongs to that
   authorized scope. Return non-oracular not-found responses for inaccessible IDs.
2. Reconcile the principal-scoped idempotency key under a unique database guard.
   Matching retries return the original result before stale-revision checks;
   changed fingerprints conflict. Never replay across actors or route targets.
3. Lock the relevant aggregate and check expected revisions. Freeze only actual
   eligible canonical revisions under their authoritative completeness and split
   policy. Do not accept human-review/eligibility assertions in management input.
4. Commit state, attributed append-only audit and shared queue intent atomically.
   A failure rolls all three back. Compile/train/evaluate return existing durable
   fenced job IDs; they do not spawn background tasks or an alternate scheduler.
5. Normalize uploaded original bytes through `ingest_canonical_asset`; preserve
   original provenance and source groups. Source URIs are provenance only, never
   server-side URL fetch instructions. Resource limits at ingress must cap the
   encoded HTTP body as well as the decoded image and pixel/decompression budget.
6. Resolve approved local training profiles server-side, derive shape from the
   recipe and call the delivered ML worker. Compile 320/640 directly from original
   sources, never resize the other derivative. Run source-frame evaluation and
   export through delivered adapters, retaining hashes and static signatures.
7. Delegate promotion to the shared qualification operation. Consume it if it
   lands first; never add a second promotion route or copy its gates. Null targets,
   adverse audits, missing support, critical-slice regressions and compromised
   final-test independence retain the incumbent. Rollback metadata stays atomic
   and per-recipe. The facade cannot turn `retained` into `promoted`.

## Generated contract

GitHub Actions' unit job generates and publishes the `management-contract`
artifact containing `management.openapi.json` and `management-client.ts`:

```sh
uv run python -m labelloop_api.management_codegen --output-dir <artifact-directory>
```

This is an **unbound management-only schema**, not the eventual combined app
OpenAPI. Generation is deterministic and uses the pinned FastAPI/Pydantic versions;
no new generator dependency is introduced. The TypeScript client uses fixed
same-origin `/api` routes, encoded IDs, credentials, CSRF and idempotency headers,
no-store caching and refused redirects. It supplies no authorization of its own.
Response types are compile-time types, not permission to treat server/model data
as trusted domain state.

The web-build job also generates the client under `apps/web/src/generated` before
web lint/typecheck and runs for Python changes as well as web changes. The CI
aggregator requires that job for Python changes, so a successful Python generator
alone cannot satisfy the client contract. No local suite is used as a substitute.

Next: retrieve the exact-head generated artifact, check the schema into
`apps/api/openapi/management.openapi.json` and the client into
`apps/web/src/generated/management-client.ts`, and add a checked-in drift
comparison. These remain the isolated management contract, not a final combined
app schema. Do not assert pending publication/drift/type checks have passed.

## Acceptance evidence still required

| Check | Contract preparation | Final integration evidence required |
| --- | --- | --- |
| Admin/reviewer/service isolation | Route matrix against injected principals | Real sessions, CSRF, service authentication and cross-resource reads/writes |
| Audit/revision/idempotency | Fingerprint/error/typed request cases only | Disposable Postgres parallel writers/retries; one event/job; immutable audit grants |
| Migration/role safety | No new migration or grant in this slice | Combined migration graph upgrade/downgrade/re-upgrade and runtime-role refusal tests |
| Canonical source/release | Uploaded bytes and revision pins in request | Actual normalized ingest, eligible freeze, exclusions and split membership |
| Shared queue | Typed async receipts only | Real producer, fenced lease/result publication, crash/retry recovery |
| Promotion | Delegating port, retained decision remains retained | Real qualification veto transaction plus independent 320/640 promotion/rollback |
| New project to export | No end-to-end PASS claimed | Actions CPU fixture creates project, ingests, freezes, compiles both sizes, trains, evaluates source frame and exports using real adapters |
| Generated API/client | Generation and fixture assertions commissioned | Exact-head artifact, checked-in combined schema/client, drift and TypeScript checks |

The fixture recording port does not store state or simulate a durable queue. Its
tests establish only transport/dispatch boundaries. They do not establish any of
the final integration checks above, independent domain gold or qualification.
No paid provider call, live database mutation or production deployment is needed
or authorized for this work.
