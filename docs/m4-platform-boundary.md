# M4 Platform contract and transport foundation

This is an **offline boundary**, not live Platform qualification, durable synchronization,
cloud training orchestration, credential storage, or a budget authorization implementation.
It implements the transport portion of plan §§8.1–8.3, 9.2 and 12; downstream synchronization
and training controllers remain responsible for their domain gates and persistent records.

## Pins and provenance

- SDK: existing workspace pin `ultralytics-platform==0.1.107`; no dependency or lock changes.
  It supplies `httpx` (the existing lock resolves 0.28.1). No package install was needed.
- OpenAPI: `packages/platform/src/labelloop_platform/contract/openapi.json`, OpenAPI 3.2.0,
  API 0.1.107. `provenance.json` records the immutable official SDK commit, original raw URL,
  AGPL-3.0-only licence, and SHA-256. The package verifies a code-pinned digest on import.
- `python -I packages/platform/generate_contract.py` regenerates the bounded client route table
  `operations.json`. `--check` fails on drift. The existing Actions unit job also compares its
  bytes, verifies provenance, and exercises deliberate snapshot and generated-policy drift.
  No network fetch occurs during regeneration or CI. Changing a snapshot requires a reviewed
  provenance/digest change and regeneration; this is not a check of mutable upstream main.
- The official SDK source established the injected-client hook suppression and JSON POST 429
  retry behaviour. No third-party implementation code is copied; only the licensed contract
  is vendored. Offline exchanges are sanitized contract examples, **not live recordings**.

## Ports for consumers

`labelloop_platform` exports `PlatformPort`, `PlatformRequest`, `PlatformResult`, `Operation`,
`Outcome`, `StoragePort`, `StorageTarget`, `StorageResult`, `PlatformAdapter`, `SignedStorage`
and `Throttle`. Existing `client_type()` remains a bootstrap compatibility probe; the raw
SDK is no longer in `__all__`.

```python
from labelloop_platform import Operation, PlatformRequest

request = PlatformRequest(
    Operation.READ_DATASET,
    path={"owner": "approved-workspace", "dataset": "frozen-release"},
)
result = platform_port.execute(request)
```

Only trusted controller code constructs these requests. Do not deserialize model proposals
into operation selection, path fields, storage host policy or training authorization. The
wire checks validate the snapshot's structural subset (types, required fields, references,
combinators, ranges, patterns and enums), not every OpenAPI/JSON Schema extension and not
application semantics. A successful response is still untrusted remote data. Synchronization
must validate class/split/geometry membership and training must validate resolved arguments
before either changes canonical state. GPU availability's upstream schema is intentionally
unconstrained; an empty object does not establish an available GPU.

Mutation requests require a stable `operation_id`. The ID is **not** sent as a made-up remote
idempotency header. Persist it, its canonical request digest and write intent before dispatch,
then persist the result and returned remote IDs. Reads need no operation ID. One client
serializes its SDK capture state and write latch; independent clients may proceed concurrently.

## Transport policy

- Fixed `https://platform.ultralytics.com` origin, explicit reviewed operation enum/table,
  exact path parameter names, ASCII resource segments and contract-owned query names.
  No caller-controlled server URL, SDK extras, redirect following, ambient credentials,
  environment proxy, or localhost wake. Ingest is session-only, with explicit class mapping,
  no `targetSplit`, remote `sourceUrl`, or connected/on-premise reference.
- New datasets are private detection datasets. Training requests require explicit GPU and
  dataset-version capture. Full pinned argument and spend checks belong to the training
  consumer and must occur before dispatch; this package grants no permission to spend.
- The injected owned `httpx.Client` supplies connect/pool 10 s and read/write 60 s timeouts.
  SDK retries are zero, `retry_methods` is empty, and the underlying HTTP transport has no
  connection retries. The pinned private SDK request primitive is the only SDK seam used;
  this deliberately avoids broad generated resource wrappers and is covered by fixtures.
- The owned client drives the httpx transport directly, avoiding implicit client cookies,
  default response hooks and URL logs. Context-local filters suppress httpx/httpcore wire
  diagnostics, including DEBUG response headers, while a transfer is active. Our only log
  contains outcome and numeric status; exceptions, paths, raw payloads and headers are never
  logged. Results hide success payloads in repr, and signed targets redact their repr.
  Trusted injected transports must not add their own raw-wire logging or retries.
- Close each adapter/storage port on worker shutdown. Only infrastructure code injects a
  transport; it is not a model- or user-configurable network capability.

## Backoff, uncertain writes and durable integration

`Throttle` uses sliding 60-second windows, keyed by credential SHA-256 and category, with
limits default 100, training/upload 10, predict/export 20 and download 30. Clients share the
process default throttle; explicit instances support fixture clocks. 429 returns `rate_limited`
and a monotonic `retry_at`, honors numeric and HTTP-date `Retry-After` without shortening it,
and blocks that key/category until then. Nothing sleeps or automatically repeats, including
POST. Invalid headers use one second. Consumers schedule future work, not tight polling.
A multi-process deployment must serialize by key/category or supply equivalent shared
admission before dispatch; this in-process throttle is not a distributed rate limiter.

| Result | Required consumer action |
|---|---|
| `succeeded` | Validate the payload, save IDs and reconcile domain invariants |
| `rate_limited` | Schedule no earlier than `retry_at`; explicit rejection can be redispatched |
| `paid_paused` (402) | Persist project paid-stage pause; never automatically retry |
| `reconcile_required` | Read the known remote resource/status before deciding any new mutation |
| `unavailable` (read 503) | Terminal for this attempt; no internal retry |
| `malformed` / `transport_error` (reads) | Fail the attempt without copying raw error text |
| `rejected` | Stop; inspect trusted request/policy or sanitized status |

409 always requires reconciliation. Mutation timeouts, read failures after dispatch, 408,
5xx (including 503), invalid successful response envelopes, and unexpected statuses such as
redirects are uncertain. They cannot prove whether the remote mutation happened. 503 is never
internally retried; a possibly accepted mutation first needs reconciliation. The same client's
write latch returns the prior result without a second dispatch, and rejects reuse of the ID
with a different request digest. It has no reset API. **The latch is not durable idempotency**:
a restarted/new client must recover persisted intents before submitting mutations. A completed
or failed transport response alone does not complete a dataset release or training run.

## Signed storage

`SignedStorage` holds no Platform key and uses a separate owned transport. Deployment-owned
exact storage hosts must be supplied (no wildcards; localhost, private IPs, local suffixes and
the Platform host are rejected). Server-returned URL/headers are still validated: HTTPS only,
no userinfo/custom port/fragment, no redirects, and only content and storage-specific signed
headers. Authorization, Cookie, Host and proxy authorization are rejected. Never share the
Platform client with storage or derive the trusted host inventory from model output. Trusted
DNS and correct deployment host inventory remain infrastructure prerequisites.

PUT is create-only in the inspected contract: 412 and uncertain PUT outcomes require a new
signed URL/session after reconciliation, not a blind repeat. The storage port never retries.
GET returns bytes, not a persistent artifact identity; consumers hash and verify artifacts.
Storage targets and response bytes must not be put in canonical manifests or logs.

## Verification boundary

Actions runs the existing full suite, licence import gate and secret scanner. New tests cover
snapshot/regeneration drift, fixed origin, no SDK wake/retries, explicit timeouts, malformed
requests/responses, secret-safe errors and DEBUG logs, signed-storage isolation, 402/409/503,
uncertain-write latching, and 429/time/category/key isolation using recorded offline exchanges.
No local full suite, package install, paid request, live workspace operation, secret acquisition,
or deployment is needed or claimed. Actual encrypted credential wiring remains separate.
