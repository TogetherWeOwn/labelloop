# Paid-stage pause acknowledgement backend contract

The router in `labelloop_api.budget_resume` is **UNMOUNTED**. Tests mount it only
locally. Product integration owns normal mounting, generated OpenAPI and web
transport. No live operation or paid resumption is demonstrated by these fixtures.

## Wire contract

`GET /projects/{project_id}/paid-stage-pause`

Requires a valid human-admin server-side session. Bearer/service, reviewer,
missing and expired sessions are denied. Response model `PauseMetadata`:

```json
{
  "project_id": "example-project",
  "policy_revision": 1,
  "pause_reason": "insufficient_credits",
  "state_token": "v1.<64 lowercase hexadecimal characters>"
}
```

`pause_reason` is null, `insufficient_credits`, or `estimate_overrun`.
`state_token` is null unless a paused state passes conservative resume admission.
It is server-derived from complete durable state, not a credential, approval,
retry instruction or dispatch grant. Read again after a conflict; no automatic
retry is implied. The read exposes no operations, provider identifiers, prices,
request payloads, approvals, billing evidence or secrets.

`POST /projects/{project_id}/paid-stage-pause/resume`

Requires that same session, configured same-origin `Origin` and matching
`X-CSRF-Token`. Strict, extra-forbidden model `ResumeCommand` accepts **only**:

```json
{
  "expected_policy_revision": 1,
  "paused_state_token": "v1.<exact token from the metadata read>"
}
```

Revision must be an integer (not a boolean or coerced string), between 1 and
2,147,483,646. Token must be exactly 67 characters with the version/pattern above.
Project identity is a nonempty path string bounded to 256 characters. POST bodies
are capped at 1,024 bytes, including streamed/chunked bodies. No caller-selected
actor, operation, provider, price, policy, approval, reason or resulting state.

Success model `ResumeResult` contains only project identity and the next positive
policy revision:

```json
{"project_id": "example-project", "policy_revision": 2}
```

All responses, including validation/authentication/conflict/unavailable errors,
are `Cache-Control: no-store`. Public errors are redacted:

| Status | Detail | Meaning |
|---|---|---|
| 401 | `authentication_required` | No eligible valid session; bearer credentials never qualify |
| 403 | `permission_denied` / `csrf_denied` | Ineligible role or failed same-origin/session-CSRF proof |
| 404 | `budget_not_configured` | Project has no budget head, including unknown projects |
| 409 | `resume_conflict` | Stale/replayed/unpaused/in-flight/otherwise inadmissible command |
| 413 / 422 | `invalid_request` | Oversize or malformed/extra/coerced input, without input echo |
| 503 | `authentication_unavailable` / `resume_unavailable` | Authentication/storage failure or unsupported durable evidence |

## Trusted service ports

- `BudgetResumeStore.paused_state(project_id, *, actor) -> PausedState`
- `BudgetResumeStore.resume(project_id, *, actor, expected_revision, state_token) -> ResumeReceipt`
- `BudgetGate.acknowledge_pause() -> BudgetGate`
- `AcknowledgedPause(project_id, pause_reason, entries)` is trusted domain
  reconstruction evidence, never accepted as HTTP input.

`BudgetStore._gate` reads explicit journal acknowledgement events after restart.
Ordinary workers do not need to use the new subclass. The existing head lock
orders competing policy, outcome and acknowledgement writers. A successful
command changes only head revision/pause and one new immutable actor event.
Failures roll back the entire transition. No migration is required.

The policy remains unchanged. All original operation/child state, exposure,
approval, provider/privacy permission, price version, dispatch identity and
billing evidence remain authoritative. Reserved/dispatched/unconfirmed parents
and dispatched/unconfirmed children block admission. Insufficient-credit rows
are terminal-for-now, not silently settled to zero. `approval_exhausted` remains
paused; the command never tops up a review approval or resumes drift.

## Verification

- Pure-domain regressions: `packages/core/tests/test_budget_resume.py`.
- Disposable PostgreSQL races/restart/rollback:
  `apps/api/tests/test_budget_resume_store.py`.
- Real session/CSRF + production-store router fixture:
  `apps/api/tests/test_budget_resume_router.py`.
- Existing Actions budget PostgreSQL step runs both dedicated modules as well as
  existing ledger regressions. No provider I/O, paid calls, live/shared DB or
  dependency installation is part of local implementation verification.

See [the acknowledgement ADR](adr/0004-explicit-paid-pause-acknowledgement.md).
