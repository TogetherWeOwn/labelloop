# Delivered API schema and annotation read consumer

This bounded contract follows the accepted [canonical reads](m3-annotation-reads.md).
It does not deliver the full S2 review API or the review screen. No new HTTP routes,
permissions, write authority, database tables, model transport or spend are introduced.

## Generation and drift

The production `app.openapi()` is exported to `apps/api/openapi.json`, using the
locked FastAPI/Pydantic versions, sorted JSON keys, UTF-8 and one trailing newline.
Importing the application and rendering its schema does not acquire authentication,
annotation, storage or provider connections. The public OpenAPI/docs routes remain
disabled. `test_openapi_contract.py` compares the checked-in bytes with the production
schema in the existing Actions unit job. Route/schema changes therefore require an
explicit snapshot update; adding a new route does not quietly retain an old client.

Generate source artifacts (not a test or CI substitute):

```sh
uv run --frozen python -m labelloop_api.export_openapi apps/api/openapi.json
pnpm --dir apps/web generate:api
```

`openapi-typescript` **7.10.1** generates `apps/web/src/api/schema.d.ts` from that local
snapshot. The browser uses `openapi-fetch` **0.15.0**, sharing the generated path,
parameter and response types rather than a second hand-maintained API interface.
Both direct versions and their transitive resolutions are locked. The Actions web
job regenerates the declarations and refuses a diff before lint/types/tests/build.
The OpenAPI snapshot selects both Python and web jobs, even on an API-only PR;
a regression checks the actual workflow area configuration to keep this edge intact.
No remote schema URL or provider credential is used during generation. Do not edit
the generated JSON/declarations directly or run CI gates locally.

Auth and annotation error documentation now reflects the redacted `{detail: string}`
boundary, including the custom `422 invalid_request`; it does not advertise FastAPI's
raw validation input/errors format. This is schema documentation only, not a change
to authorization or exception behavior. Authentication is enforced by the existing
deny-by-default middleware and principal dependencies, **not** by the generated client
or presence of a method in OpenAPI. Middleware authentication/CSRF policy is not
inferred by FastAPI's schema generator; consult the actual auth boundary before
adding consumers. In particular, login still requires the configured same origin,
and every session mutation requires its existing CSRF proof. This consumer adds no
login, logout or service-token transport helper.

## Browser read boundary

`readAnnotation(projectId, assetId, signal?)` and
`readAnnotationHistory(projectId, assetId, page?, signal?)` use only the existing
annotation GET paths behind the same-origin `/api` proxy. Both require the standard
hyphenated persisted UUID handles. Canonical keys, including slashes, Unicode and
long strings, stay unchanged in response data; they never become route components.
History accepts a bounded integer limit (1–100, default 20) and an optional exclusive
positive safe-integer `beforeRevision`, preserving empty terminal pages and nullable
cursors without synthesizing revisions.

The consumer takes no endpoint, header, token, method or redirect configuration.
Requests use the browser's same-origin session cookies, `cache: no-store`,
`redirect: error`, a 15-second timeout and optional caller cancellation. It does not
read/copy cookies into JavaScript, store credentials, add bearer tokens, log server
errors, retry failures, refresh sessions or redirect on authentication errors.
HTTP status maps to a small stable error code; malformed JSON/network failures map
to a generic error, never raw driver/backend text. The caller owns presentation and
out-of-order/cancelled-view handling; no screen or application state is changed here.

Generated TypeScript types are **not runtime response validation**. Successful
payloads remain server data, with free-form object attributes typed as unknown;
consumers must render text as text and validate it before any privileged state/gate
transition. Historical `recorded_training_eligible` / `recorded_gold_eligible` values
are not current eligibility or qualification decisions. Reading zero objects does
not establish a verified negative or human completeness. No automatic acceptance,
release, class mapping, split selection or other model-selected authority is added.

## Verification and remaining work

Python contract cases cover deterministic no-I/O export, exact snapshot drift,
UUID/bounded history schema, redacted error shapes, password write-only metadata and
continued refusal of public schema/docs. Browser consumer tests use an injected fetch
fixture, not live sessions, databases or providers: exact URL/options, machine tiers
and canonical key preservation, pagination, input rejection, safe failures and
cancellation. GitHub Actions supplies the execution evidence; these described cases
are not a claim that they have passed until exact-head run/job links are recorded.

Full review-view/proposal/reference assembly, expected-revision human and restricted
worker writes, completeness/exclusions, queues, adjudication/audit persistence,
transport/budget/credential integration, shell/sign-in and the accepted review UI
remain unfinished. Extend/regenerate this one contract as those routes land; do not
create a parallel client. M3 and acceptance checks 23/24 remain incomplete.
