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
promotion/qualification payloads or principal names. Sentences are fixed code-owned
labels. Recorded review actor kind is retained; historical promotion actor kind is
`unknown`, not inferred as human. Review revision means the event's expected
revision; promotion revision means its registry revision.

The typed `AuditMetadataPage` includes a total from the same SQL statement snapshot
as its page. Pages contain at most 100 events ordered by UTC time, kind and UUID,
newest first. `after` is a visible event position (`kind/UUID`), not authorization:
a foreign, hidden or filtered-out event rejects without disclosing its metadata.
Each page is a fresh read, not a frozen cross-page history. Refresh from page one
for newly appended events. Optional `kind`, `since` and inclusive `through` filters
apply identically to `/audit-log/export`. Dates require explicit timezones and are
normalized to UTC; inverted ranges reject. JSON-lines exports are fully buffered
and capped at 1,000 events; overflow rejects with a narrower-range error, never a
silently truncated file or a partially streamed failure.

This port is **not yet mounted in AuditLog** and does not supply connection/budget
history, refused attempts, principal display-name resolution, search, record links
or the complete accepted UI projection. New real-session adapter controls and
both-recipe disposable PostgreSQL committed review/promotion pagination/export,
actor-recovery, reviewer-restriction and non-mutation controls await exact-head
Actions. No new event store, schema, dependency, spend or provider call. REA: reuse
immutable canonical review and registry event records, not another audit journal.

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
