# Operational view fixture boundary

## Delivered surface

`apps/web/src/operations/` implements the accepted Costs, Audit log and M5
canary/drift registry extensions in `docs/design/screens/{09-costs,10-audit-log,07-model-registry}.md`.
It consumes the checked-in Ember Hours tokens and accepted P6, J13, J17, C6,
A1/A10/A11/A12/A14 composition. There are no new charts, thresholds or design tokens.

- `CostsView` is the **one reusable ledger** for project, iteration, run and review
  batch scope, including LLM review. M3 should import it, not build another ledger.
  Money uses the delivered BudgetGate integer micro-USD contract. Confirmed actuals,
  reserved estimates and completed-but-unconfirmed billing stay distinct. Refused
  attempts remain visible without consuming a reservation. Monthly and iteration
  meters count confirmed charges plus outstanding estimates; filters affect the
  ledger/export, not the project policy. Limits are never called billed-cost caps.
- `AuditLog` calls a query/export port, cancels superseded queries and paginates on
  request. The server must authorize **before** filtering, counting, paginating or
  exporting. `role` is a presentation hint, never authorization. Sentences are
  server-redacted display projections; raw provider payloads are not accepted.
- `RecipeOperations` is a read-only registry extension, not a second registry.
  `ModelRelease` mirrors the delivered Python release fields. It shows complete
  deployment and rollback records, evidence set/version/role/sample sizes,
  supported/unsupported classes, fresh-group canary membership, and drift-paused
  classes/sources. It does not calculate qualification or offer promotion,
  canary enablement, rollback or drift resume commands. Existing registry commands
  retain their owner and atomic release semantics.
- `PaidPause` can be reused by the M3 shell; the shell must retain its global
  "Paid steps paused" status on every page, not just Costs. No 402 is retried.

All three modules expose loading, empty, error, stale and read-only states.
A stale Costs record disables commands. Cost command failures, including uncertain
writes, require a refresh rather than automatic retry. Policy writes carry the
expected revision; autonomous spending requires an in-place confirmation of the
exact draft limits. The server still checks every invariant and permission.
`CostsView.readId` identifies a successful authoritative read, not a record revision:
advance it after each successful read, including unchanged revisions. Ordinary reads
of the same project/revision preserve dirty drafts, focus and the ledger filter.
Only a read following a confirmed or uncertain command clears its recovery gate and
resets the editor to authoritative values. Project, revision or role changes reset
the record-bound content; writes remain revision-pinned. Do not advance `readId` on
a failed read or merely to retry a command. Background reads must retain the ready
record rather than replace it with the initial loading boundary.

Audit pagination/results and pending exports belong to one port, role and freshness
lifetime. Replacing a port (including a project/session authorization change),
advancing `updatedAt`, or changing stale status restarts that lifetime before old
rows can render under new freshness. Keep the port stable within a scope; replace
it whenever its authorization/project scope changes. Filters survive a lifetime
change, but cursors, rows, export status and pending query/export work do not.
The server must still reject revoked scopes/cursors independently. Within a lifetime,
next-page requests retain accumulated rows and the mounted pagination control;
`aria-disabled` plus an activation guard prevents repeat requests without dropping
keyboard focus. Page errors retain those rows and offer an explicit retry of the
same cursor, never an automatic retry. Initial-query errors still use the full error
boundary.

## Authenticated audit metadata — integration pending

`GET /projects/{project_key}/audit-log` reads recorded review and promotion metadata
only. Human admins can read both kinds; reviewers can read reviews only. The server
selects permitted sources before filters, counts, paging and export; service tokens
cannot use either read. It never returns raw decisions, reasons, corrections,
promotion/qualification payloads. Nullable `actor_name` resolves the current auth
directory name only by exact principal ID, never a legacy actor string equal to a
username. Human reviews require a human directory entry; model/system reviews
require a service entry. Unknown promotion provenance may resolve an exact ID's
current name but remains `unknown`. Missing/incompatible identities retain the
original actor ID and null name. Disabled accounts keep their historical display
link; a renamed account shows its current name, not a claimed historical snapshot.
Only names attached to visible events can match search: this is not a principal
directory endpoint. Sentences are fixed code-owned labels. Recorded review actor
kind is retained; historical promotion actor kind is
`unknown`, not inferred as human. Review revision means the event's expected
revision; promotion revision means its registry revision.

The typed `AuditMetadataPage` includes a total from the same SQL statement snapshot
as its page. Pages contain at most 100 events ordered by UTC time, kind and UUID,
newest first. `after` is a visible event position (`kind/UUID`), not authorization:
a foreign, hidden or filtered-out event rejects without disclosing its metadata.
Each page is a fresh read, not a frozen cross-page history. Refresh from page one
for newly appended events. Optional `kind`, `since`, inclusive `through`,
`actor_kind` and `search` filters apply identically to `/audit-log/export`.
`kind` and `actor_kind` can repeat to select a set (OR within a field, AND
between fields). Single values remain compatible and omitted fields mean all
authorized values. The query validates every member and bounds input to three
kinds/five actor kinds; duplicates do not duplicate records. Page totals, cursor
lookup and export all consume that same filtered population, never browser-side
filtering after paging. New multi-selection controls await exact-head Actions.
`actor_kind` selects recorded `human`, `model`, `system` or `unknown` categories;
a shared actor identifier never changes that provenance. Search accepts 1–128
characters and matches a case-insensitive literal actor-ID/current-name substring
or an exact event/record UUID (hyphenated or hex). `%`, `_` and `/` have no wildcard
meaning. It does not search private payloads, decisions, reasons or inferred record
labels. Authorization still selects sources first; counts and cursors use
the filtered population. Dates require explicit timezones and are normalized to
UTC; inverted ranges reject. JSON-lines exports are fully buffered
and capped at 1,000 events; overflow rejects with a narrower-range error, never a
silently truncated file or a partially streamed failure.

The port also includes admin-only `Budget` metadata from the existing immutable
budget journal. Its recorded actor kind is `human` or `service`; `service` is now
an additional exact `actor_kind` filter, not an inferred model/system category.
Names still require exact compatible directory identities. Budget `record_id` is
the canonical project UUID; `revision` is the recorded policy revision, not a
ledger freshness token or operation revision. The static sentence discloses no
action, private record, operation/claim ID, policy/pricing or provider payload.
Native and text UUID journals share normalized hex ordering/cursor/search keys,
while public IDs remain UUIDs. Reviewer restrictions apply before every operation,
including budget-name search, counts and export. Nullable `refused` is `true` only
for the writer's explicit `approval_exhausted` event; other events remain unknown,
not successful. Budget/refusal controls passed
[CI at 0f5d8ce](https://github.com/TogetherWeOwn/labelloop/actions/runs/42577331537),
including unit and both-recipe PostgreSQL smoke. The synthetic refusal row proves
projection/recovery, not actual paid-attempt emission or complete refused history.

This port is **not yet mounted in AuditLog** and does not supply connection
history, complete refused attempts, record links or the complete accepted UI projection.
Search/filter and UUID fixture controls passed
[CI at a3fcca5](https://github.com/TogetherWeOwn/labelloop/actions/runs/42568773035),
including [unit](https://github.com/TogetherWeOwn/labelloop/actions/runs/42568773035/job/129001106944)
and both-recipe [PostgreSQL smoke](https://github.com/TogetherWeOwn/labelloop/actions/runs/42568773035/job/129001106973).
Current-name resolution/search controls passed unit and both-recipe PostgreSQL
smoke at [fd66017](https://github.com/TogetherWeOwn/labelloop/actions/runs/42570767808),
with **2417 unit passed, 81 skipped**. Overall CI failed separately because the
checksum-pinned MinIO download returned HTTP 504 in dev-stack; this is not an
overall gate PASS. No new event store, schema, dependency, spend or provider call. REA: reuse
immutable canonical events and exact auth identities, not another audit journal.

### Session-backed display adapter

`sessionAuditPort(project)` now implements the supplied `AuditPort` through the
strict session transport. It maps only server-returned rows, preserves server
counts/cursors, uses kind-qualified event IDs, and leaves record links null.
Missing current names fall back to the recorded actor ID. Unknown actor kind stays
unknown even when a current name resolves; the existing neutral tag marks the
missing provenance. Model/system/service actors share the Services display
category while retaining exact `recorded_kind`. Nullable refusal remains nullable;
only an explicit true receives refusal styling. Export retains the complete
original metadata JSON lines, never just displayed pages or inferred principal types.

The existing People filter maps to `human`, Services to `model/system/service`,
and both mean no actor-kind restriction (including unknown history). Unknown
history is not classified by a matching current name. Date controls translate exact
calendar dates to inclusive UTC day bounds, including the final microsecond; malformed
or inverted ranges reject. Empty selections and connection-only requests fail
closed before network access, rather than turning into unfiltered queries or
invented empty history. Mixed kinds select the available Review/Promotion/Budget
journal only. Connection events, empty-selection UX, a dedicated unknown filter,
real record navigation and authenticated shell mounting remain unfinished; this
adapter is not complete AuditLog acceptance. Keep its identity stable within a
project/session lifetime and replace it when either scope changes.

[CI at f55fe5d](https://github.com/TogetherWeOwn/labelloop/actions/runs/42586297134)
passed web-build (including transport lint/types/tests), unit, both-recipe ML smoke,
queue/migration cycles/all four parents and dev-stack. CodeQL and gitleaks also
passed that exact transport head. First adapter head `463fa17` failed web lint:
structured Actions annotation identified an unsafe `any` argument after array
narrowing. The boundary now validates each kind as `unknown` before string use,
with null/numeric-member controls; adapter tests were not reached on that head.
Next head `a747de3` passed web lint/types but failed invalid-filter export tests:
validation threw synchronously before the promised export rejection. The export
adapter is now async so the existing view's rejection handler receives every
validation failure without a synchronous event-handler exception. Existing negative
controls retain the no-network requirement. Corrected adapter/unknown-state tests
await new-head Actions; no local tests or builds ran. No new layout, component, token,
event store, dependency, provider or spend operation. REA: reuse the supplied
AuditLog port, session transport and existing neutral tag, not another audit journal.

## Authenticated ledger read port — integration pending

`GET /projects/{project_key}/costs?limit=50&after=<operation_id>` is an admin-session
read over the existing durable budget tables. The response model is published in
the application's generated `app.openapi()` contract as `BudgetPage`. It explicitly
labels USD/micro-USD, current policy revision/limits and pause reason, and returns
at most 100 operation projections plus `next_after`. Actuals remain nullable;
confirmed zero is not unconfirmed billing. Exposure uses the existing core entry
calculation, including retained child exposure. Raw intent, provider evidence,
configuration and pricing payloads are not returned. Missing/unconfigured scopes
are unavailable, not manufactured empty budgets; malformed entries fail closed.

Every page takes the budget writers' existing head lock through outer commit.
Ordering is by immutable operation identity, not timestamp or cost. `after` is only
a position within the requested project, never authorization. Each page is a new
consistent read, not a frozen cross-page snapshot: new operations can appear before
a prior cursor. `policy_revision` is not a ledger revision or freshness token.
Consumers must refresh from page one to recover the current complete collection.

This read port is **not yet mounted in CostsView**. It does not supply refused-attempt
history, scoped totals, operation display labels/links, policy-write/resume commands,
or a complete `CostsRecord`. No paid operation is dispatched. Session, bounded
paging, redaction, rollback and disposable PostgreSQL recovery controls passed
[exact-head Actions at a90a230](https://github.com/TogetherWeOwn/labelloop/actions/runs/42563179534);
no local tests ran. REA: reuse the durable ledger codec and core
exposure property, not another budget engine.

## Not delivered: complete authenticated integration

Main currently has no operational read APIs. `contracts.ts` contains **projection
ports, not generated OpenAPI contracts**. Do not treat the TypeScript types or the
recorded UI fixtures as trusted network validation, live provider evidence or a
server-side security gate. No fetch adapter is installed and no backend endpoint
is silently claimed by this implementation.

The accountable integrator must bind these operations after their owners deliver:

| Operation needing an API contract | Producer / integration responsibility | Required conditions |
|---|---|---|
| Read project policy, pause status and durable ledger including refused attempts; scope by iteration/run/batch | Durable budget/dispatch owner + API integrator | Project authorization, integer micro-USD, attributed actuals or explicit unconfirmed, consistent snapshot, pagination for real history |
| Save limits / autonomous policy and explicitly resume paid stages | Budget owner + authenticated API integrator | Admin session, CSRF, optimistic revision, dry-run/estimate enforcement, durable audit, no automatic 402 retry |
| Audit read and JSON-lines export with identical filters | Audit persistence/API integrator | Authenticated principal, server-enforced reviewer restriction to review events, redaction/fingerprints only, bounded queries/cursors, stable UTC ordering |
| Registry state plus final-test lifecycle, qualification evidence, canary and drift projections | Qualification owner + model registry/API integrator | Exact release/pipeline identity, evidence provenance and independent sets, per-class support, recipe isolation, complete known-good rollback metadata |
| Record/evaluation/audit-sample navigation | M3 shell + API integrator | Bind the record links to real authorized routes; fixture links are illustrative |

No new URL or command endpoint is selected here. Publish/validate OpenAPI through
the existing API owner before installing live adapters. Generated responses must
be runtime-validated, authorization must remain server-side, and stale revision,
permission, malformed payload, revoked session and cross-project negative tests
must land with integration. Evidence refresh belongs to the shared visible-page
30-second poll/freshness mechanism, not an independent scheduler in each module.

## Fixture entry and verification

`operational-fixtures.html` is an isolated evidence harness, **not** the normal
application entry or an authenticated product route. The normal `App.tsx` and M3
shell/auth work are untouched. `vite.operations.config.ts` produces a separate
`dist/operations/` artifact. Do not deploy that artifact as the application.

Query options: `view=costs|audit|models`,
`state=ready|loading|empty|error|stale|readonly`; Costs additionally supports
`scope=iteration|run|review_batch`, `state=unset|editable|command-error|background-refresh`.
`editable` simulates policy changes in React memory only, with no provider, API,
credential or persistent configuration writes. Every page says it is a fixture.
Fictional people, records, hashes and sample counts are licence-clean invented
values adapted from the accepted mockups, not evidence of real qualification.
Audit `state=scope-swap|freshness-refresh|deferred-page` supplies deterministic deferred
ports for mounted lifecycle and long-table pagination regressions, not new product
controls. Costs `background-refresh` completes an unchanged-revision read without
moving focus; `command-error` retains the separate uncertain-command recovery case. Both recipes evaluate the
same complete source ground truth (780 objects); Almond milk is absent and unsupported
in both. The 640 fixture's qualified claim explicitly excludes that class.

Full verification runs on GitHub Actions only. The `web-build` job performs a
clean frozen install, lint/types/Vitest, both builds, pinned gitleaks bundle scan,
and Playwright keyboard/axe/screenshot checks at 1280 and 390. Axe JSON, PNGs,
HTML report and failure traces are uploaded as `operational-ui-evidence` for the
exact head. Screenshots still need human-equivalent UX design QA; axe alone does
not prove keyboard workflow or design fidelity. QA/Security/native review and
required checks are not waived. No paid calls, live Platform smoke or deployment.

## Dependency controls

pnpm 10.28.2 recognizes `minimumReleaseAge: 4320` and `ignoreScripts: true` in the
workspace policy (read back with `pnpm config get`). Existing workspace membership
and the Rollup override are preserved. Root/web manifests have no lifecycle hooks;
no pnpm hook files are tracked. Added packages are exact-pinned Playwright 1.64.1
(published 2026-10-14) and axe-core 4.12.1 (2026-06-10), with lockfile integrity.
The Playwright package chain reports no install hooks. axe-core has development
prepare/build hooks; they are not needed for its published prebuilt `axe.source`
and are suppressed. No script exception is enabled. Chromium installation is an
explicit reviewed Actions step, not an implicit install hook. Frozen installation
and builds are acceptance evidence only once that exact-head job passes.

Native age filtering governs new resolutions, not retrospective safety of the
existing lock; script suppression is not a sandbox and does not suppress pnpm hook
files. The existing lock's packages are retained, not claimed newly audited. No
security-update exception, package-manager upgrade or paid service is introduced.

REA angle: reuse delivered budget/registry contracts and accepted mockup source;
no copied third-party implementation, new policy engine or replacement scheduler.
