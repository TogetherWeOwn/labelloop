# ADR 0003: shared typed provider credential envelopes

Status: proposed, pending native Applied Cryptography and Security review.

## Context

Plan §12.2 requires encrypted-at-rest provider keys, public-only API sealing,
per-type worker private-key custody and fingerprint-only public views. We need
one implementation for LLM and Platform secrets, not two cryptographic protocols.

REA angle: reuse the standard `cryptography` AESGCM and RSA-OAEP APIs and the
existing transaction-bound SQLAlchemy store idiom; no custom cipher or key-wrap
algorithm, new scheduler or copied vendor implementation.

## Construction

- Every seal draws a fresh random 256-bit AES data key and 96-bit nonce using the
  cryptographic library/OS RNG. A data key encrypts exactly one provider secret.
- AES-256-GCM authenticates a canonical ASCII JSON header containing a domain
  separator, format version, project, exact authorized workspace, connection ID,
  credential version, secret type, wrapping-public-key digest and fingerprint.
- RSA-OAEP uses SHA-256 for both OAEP and MGF1. The same header is the OAEP label.
  RSA public/private keys must be 3072–8192 bits; current ephemeral tests use 3072.
- Public-key identity is SHA-256 of DER SubjectPublicKeyInfo. The two type keys
  must differ. A public ring refuses private PEM input and identical type keys.
- A record stores only the authenticated header, nonce, ciphertext including GCM
  tag, and wrapped data key. Public projections expose identity and fingerprint
  (last four printable ASCII characters and 12 SHA-256 hex characters), not the
  envelope. Input length is 8–4096 printable non-space ASCII bytes.
- Format version is explicitly 1 and unsupported formats fail closed. Credential
  version is monotonic and authenticated; it is not an algorithm selector.

## Capability and integration boundary

Only worker adapters import the shared private primitive. Their constructors read
exactly one designated environment field, never an alternate key or provider-key
fallback. API configuration reads only `LABELLOOP_LLM_WRAP_PUBLIC_PEM` and
`LABELLOOP_PLATFORM_WRAP_PUBLIC_PEM`. Worker custody fields are respectively
`LABELLOOP_LLM_UNWRAP_PRIVATE_PEM` and `LABELLOOP_PLATFORM_UNWRAP_PRIVATE_PEM`.
No key-generation CLI, private-key installation, compose secret placement or real
credential creation/rotation is delivered or authorized by this ADR.

Python imports/types are not a sandbox. Someone controlling a worker's code and
its private key can use that capability; someone controlling API code but holding
only public halves cannot decrypt database records. Deployment must ensure the
API, core and ML workers never receive a private half, and each secret worker
receives only its own half. The import contract prevents accidental cross-layer
use; environment-custody tests demonstrate the cryptographic negative controls.

`AuthorizedScope` is trusted code configuration derived from authenticated policy,
not something decoded from a model response, arbitrary owner slug or job payload.
Worker lookups bind all project/workspace/type fields before accessing envelopes.
Job payloads contain connection ID and version only; workers resolve grants from
trusted policy. No endpoint, model, class, split or spending authority is encoded
in a secret. This code grants no authorization to call providers.

Privileged HTTP routes remain absent. Auth principal/role/workspace enforcement
will consume the authentication integration separately. Store actor IDs are audit
ports, not authentication assertions; do not expose the internal stage/validation
methods to untrusted request bodies. An admin integration must authorize the exact
project/workspace identity before sealing/staging it. Database engines should use
`hide_parameters=True`; API errors must use a stable refusal mapping, not SQL or
third-party exception text.

## Rotation and durable lineage

Staging appends a version and `validation_requested` event under a savepoint,
with a compare-and-swap on latest/active versions. The old active key stays active.
A Platform worker unwraps the pending version and invokes the trusted fixed-origin
adapter's GET-workspace port. Only an exact returned UUID match activates it.
Exceptions, wrong IDs and malformed replies reject the intent without persisting
provider response text. The delivered fixture reader is offline and represents
the port, not an invented vendor endpoint. The pinned Platform adapter must wire
its researched GET operation before live use; this module makes no HTTP calls.
LLM activation checks envelope possession locally; model/provider qualification
is a separate fixture-probe gate and never implied by a successful unwrap.

Activation compares the active version captured by the intent. A racing successful
rotation makes another pending validation stale; neither stale nor replayed
validation can roll back a newer key. Terminal intents are single-use. PostgreSQL
triggers reject envelope rewrites, terminal-intent changes, scope reassignment,
version rollback and audit update/delete. Migration downgrade deliberately drops
these tables: it destroys credential/audit records and requires backup/explicit
operational approval if ever used beyond a disposable CI database.

Migration revision `0004_provider_credentials` is reserved separately from auth
revision `0003_authentication`. On the current delivered base it follows 0002 for
independent CI. The integrator must reconcile the single Alembic head and attach
after 0003 when authentication merges, without rewriting either feature's DDL.
No shared/staging/production database mutation is part of this change.

## Leakage and limitations

`SecretValue` has redacted repr/string and no JSON serialization. Provider callback
exceptions become a generic refusal. The logging filter redacts the exact plaintext
in formatted message, args, traceback and stack; adapters must install it on every
provider handler. Arbitrary encodings, HTTP debug logs, exception-local capture and
third-party instrumentation require integration-level suppression/redaction: this
library cannot prevent a malicious caller from copying bytes passed to a provider.
Provider callbacks are trusted worker code, never model-selected functions. No
prompts, datasets, exports or browser assets consume `SecretValue`.

Python/cryptography cannot guarantee zeroization of immutable byte buffers, keys
or library-internal copies. Worker memory compromise is outside encrypted-at-rest
protection. Backup restores must preserve envelopes, wrapping-key identity and audit
lineage; restoring a DB without its privately held matching type key cannot decrypt.
Private wrapping-key rotation/re-wrapping and key custody provisioning are not
implemented here and must receive their own authorized operational design.

## Verification and dependency provenance

Tests use ephemeral RSA private keys generated in disposable process memory and
inert non-provider strings. SQLite tests exercise independent persistence/state;
Actions-only PostgreSQL tests qualify the DDL guards and upgrade/downgrade/re-upgrade
inside a random disposable schema. No paid calls, real provider/wrapping keys or
network-enabled credential fixtures are used.

`cryptography==50.0.2` wheels were uploaded 2026-09-30. uv 0.13.6 applies P3D upload
age to the added cryptography chain (cryptography/cffi/pycparser); existing pins are
unchanged. A global filter could not resolve the already delivered Platform SDK
pin, so broad retrospective workspace policy is not claimed. `no-build=true`
rejects third-party source builds, including build-backend dependencies. The
container explicitly invokes `uv build --wheel --package` for named first-party
Hatchling workspace packages, then installs the resulting wheels with `--no-deps`.
This is the reviewed first-party build exception; it does not disable `no-build`
for dependency installation. The existing manifests use `hatchling.build` with
only source-package selection and no custom build hooks. This exception lasts
while those reviewed backends/manifests are unchanged; backend or hook changes
require a new review. Wheel-only installation does not guarantee package safety.
Clean frozen installation and all test/build qualification run on GitHub Actions,
not on the shared host. Native Applied Cryptography acceptance is required before
this proposal can be marked accepted.
