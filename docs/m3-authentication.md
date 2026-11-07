# M3 authenticated API foundation

Implements plan §12.2's single-organization, Argon2id, server-side-session boundary.
This is not deployment, credential provisioning, review API integration or M3 completion.
No default account, public registration, provider credential, bootstrap password, token
issuer endpoint or environment-password fallback exists.

## Request boundary

Only `GET /healthz`, `GET /readyz` and `POST /auth/login` are anonymous. All other
paths, including unknown routes, documentation and future routes, pass through the
same authentication middleware. Interactive docs and the public OpenAPI route are
disabled; `app.openapi()` remains available for subsequent generated-client work.
Database failure is a generic 503, never an authentication bypass.

`POST /auth/login` accepts only `name` and `password`. Configure `AUTH_ORIGIN` to the
exact browser origin (scheme, host and port, without trailing slash). No origin means
no browser login. The login route requires a matching Origin, including on first
login, to prevent login CSRF. Deploy behind HTTPS. There is deliberately no insecure
cookie switch for development; use an HTTPS development frontend/proxy when wiring
sign-in. This slice does not create or configure that proxy.

A successful login returns a per-session `csrf_token`, and sets a random 256-bit
`__Host-labelloop-session` cookie: Secure, HttpOnly, SameSite=Strict, Path=/, no Domain.
A separate `__Host-labelloop-csrf` cookie carries the CSRF proof with the same Secure,
SameSite=Strict, Path=/, no-Domain and eight-hour settings, but deliberately without
HttpOnly. After a reload the same-origin client reads that cookie to construct the
CSRF header; it never reads the session credential. The proof alone cannot authenticate.
Only SHA-256 hashes of the session and CSRF tokens are stored server-side. Absolute lifetime is
eight hours; there is no sliding renewal. On cookie-authenticated unsafe methods,
the exact configured Origin and matching `X-CSRF-Token` are both mandatory. CSRF
proofs from a different session fail. `POST /auth/logout` revokes the current session
and deletes both cookies; `GET` cannot log out. `GET /auth/me` returns only id, name,
kind and role. Auth responses, including credential, CSRF and database refusals, are
`Cache-Control: no-store`.

Each successful login removes up to 100 expired sessions before inserting the new
session, in the same transaction. Cleanup uses the expiry index and skips rows
locked by concurrent cleaners. This amortized cleanup needs no scheduler; expired
rows may remain while there are no successful logins, but do not grow during that
idle period. Active sessions and the separate immutable audit history are untouched.

Passwords use pinned `argon2-cffi`, Argon2id with 64 MiB, three iterations and four
lanes. Successful verification upgrades parameters when necessary. Known and unknown
accounts both perform verification. Five failed attempts lock a known account for
15 minutes, with transactional row locking. Requests during the lockout do not
extend its deadline, even with a correct password. At expiry the next failed login
starts a fresh failure count; a successful login resets it. Bad/unknown/disabled/
locked/service logins share the same 401 response. Add perimeter admission/rate controls before
Internet exposure: per-account lockout is not global request-rate or DoS protection.

## Principals, permissions and storage

Human roles are `admin` and `reviewer`. Both can use the review dependency; only an
admin passes the admin dependency. Service roles are exactly `worker-core`,
`worker-ml`, `worker-llm`, `worker-platform`; none pass either human dependency.
Service bearer tokens are random opaque values stored by hash, with an explicit
expiry; the service and browser credential tables cannot be interchanged. Supplying
both a cookie and Authorization fails rather than choosing a principal. Disabling
a principal, changing their role or deleting their token takes effect on the next
request. Service authentication always maps to the core `model` actor, never the
privileged `system` policy actor.

`0003_authentication` adds principals, sessions, service-token hashes and append-only
auth events. No canonical annotation or model record is modified. Downgrade removes
these authentication tables, so all existing sessions and audit history are lost;
back up before an authorized rollback. There is no startup auto-migration.

Login successes/failures, logout and authenticated permission/CSRF refusals record
the resolved principal (or null for unknown logins). Events never contain supplied
names, passwords, tokens, headers, request paths or bodies. Validation errors return
a fixed `invalid_request` response: the framework's default raw-input echo is not
safe for credentials. Session/credential issuance, administrative account
provisioning/rotation, audit retention and deployment DB grants remain separately
gated operational work. Nothing in this slice authorizes creating a real account
or token. Tests seed synthetic credentials only.

## Consuming review API contract (not implemented here)

Use `require_reviewer` on human review writes and `require_admin` on project-policy,
connections, budget and promotion writes. Bind `actor_id` and `actor_kind` from
`current_principal`, never from JSON. Request DTOs must forbid identity, review-tier
and eligibility fields; decision-specific code computes these states. Continue to
call the existing canonical guards and expected-revision transaction path.

There are no annotation writes in this slice. Consequently tests establish the
request/permission boundary and worker exclusion, not a completed end-to-end proof
of acceptance check 11. Persisted review writes, worker-specific operation scopes,
authenticated review/audit binding, generated OpenAPI/client, LLM budget enforcement,
provider keys/redaction and the UI remain subsequent M3 work. Acceptance checks
23/24 and live model/annotation-quality qualification are not discharged.

## Verification

GitHub Actions is the CI of record. `apps/api/tests/test_auth.py` uses real isolated
SQLite transactions and HTTPS TestClient requests, with synthetic principals only:
cookie/hash contract, route default-deny, admin/reviewer/worker matrix, request actor
injection, secret-safe validation, origin/CSRF, logout, expiry, disablement, current
roles, credential-type separation, generic lockout, reload-to-logout CSRF recovery,
no-store rejection responses and bounded session cleanup. The dev-stack Actions job
runs Alembic against PostgreSQL, including the auth migration. Its isolated smoke
checks also start six concurrent logins on separate connections, asserting the
five-failure threshold, immutable lock deadline, expiry reset, audit count and
successful-login cleanup. These are synthetic fixtures in `migration_smoke`, not
production-database tests. No paid call or local suite is needed or authorized for
this implementation.
