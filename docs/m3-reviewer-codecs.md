# M3 reviewer codecs: offline integration contract

Implements the transport-free portion of plan §5.3. `LLMReviewer` defines
`prepare(ReviewBundle) -> PreparedReview` and
`parse(bytes, http_status=200) -> ReviewReply`. The concrete codecs are
`OpenAIChatReviewer` and `AnthropicMessagesReviewer` in `labelloop_llm.reviewer`.
Neither codec sends a request, reads an environment variable, imports a provider
SDK, loads a credential, selects an endpoint, retries or writes application state.
The existing fixture-default package bootstrap is unchanged.

## Prepared evidence

A review bundle contains a pinned core `ReviewContext`, the current annotation
revision, exact class definitions, geometry policy and identified image evidence.
It requires exactly one full image, bound by SHA-256 to the canonical asset.
Zero teacher detections do not remove that image from the request. Target crops,
context crops and approved references can accompany it. Full-image source
coordinates remain authoritative; a crop is recognition evidence, not a new
coordinate frame. Image IDs must be unique and match the context's evidence set
exactly, so a patch cannot cite a reference omitted from the request.

The caller must obtain the revision for that asset from authoritative storage,
authenticate the project/access scope, exclude holdout references, verify evidence
approval, and decode/orientation-normalize images before constructing the bundle.
`AnnotationRevision` has no asset identity of its own; this codec does not replace
that repository join. Hash matching checks bytes, not image decoding, dimensions,
media type or visual validity. Reject invalid assets upstream, not at a provider.

Bounds: 128 class definitions, 16 images, 4 MiB per image, 8 MiB combined raw image
bytes, 128 KiB serialized evidence text, and an integer output cap in 1..16,384.
Images are embedded base64, never remotely fetched URLs. These are application
bounds, not claims that every provider accepts them. A provider's lower limits
must be enforced by its separately probed configuration. Prepared bodies and image
bytes are excluded from default dataclass representations; do not log `.body`.

The system instruction asks for the existing core patch schema and treats all
image/class/reference/policy content as data. That instruction is not a security
gate: core validation remains mandatory. Chat requests use JSON-object mode;
Messages requests ask for JSON text without assuming strict-schema support.
No tool definitions, streaming, automatic fallback, cache controls or model
selection defaults are introduced. Each caller supplies an exact model ID.

## Normalized results

- Responses are bounded to 1 MiB. Malformed JSON, duplicate envelope keys, wrong
  model identity, multiple Chat choices and unsupported response modes fail closed.
- Only a normal final stop may return a patch. Token-limit truncation discards the
  patch even if its JSON happens to be complete. Refusals return no patch.
  Messages `stop_sequence` is conservatively incomplete because no stop sequence
  is requested. Tool use, paused turns and fallback blocks are unsupported.
- Messages text blocks are concatenated; thinking blocks are not used as labels.
  Neither codec executes tools or forwards model-selected actions anywhere.
- Completed text must pass `core.review.decode_patch`, including its action,
  field, flag, number and size constraints. It is still untrusted: the worker must
  pass it through `validate_patch`/`CorrectionLoop` against authoritative context.
  In particular, syntactic success does not establish class membership, source or
  revision identity, geometry validity, human approval or release eligibility.
- Failure codes contain no provider text. HTTP 402 raises `insufficient_credits`;
  every other non-200 response raises `provider_error`. The future worker must
  persist the project-wide 402 pause and never retry it. This codec has no retry
  mechanism and does not claim to enforce the persistent pause itself.

## Usage and budget boundary

Uncached nonnegative integer input/output counts normalize to core `TokenUsage`.
Missing/malformed usage, inconsistent totals, nonzero cache/reasoning/audio counts,
unknown nonzero billing categories and multi-iteration usage remain `None`.
These require reconciliation or an explicit richer price adapter; they must not be
priced as free or squeezed into the two-rate `TokenPrices` contract.

The dispatch worker must commit a ledger estimate within all applicable limits,
with authenticated approval when required, **before any network I/O**, then commit
the single-use dispatch intent. Preserve the full estimate for unconfirmed cost,
transport failure, malformed responses or unsupported usage. Do not automatically
retry an uncertain paid request. A valid refused/truncated reply can still carry
billable usage. Parser failure does not prove zero cost.

Network transport and provider SDK integration remain disconnected. Any eventual
transport needs bounded HTTP reads before materializing the full body, redacted
errors, no automatic paid retry, no redirects to model-selected destinations,
project/provider authorization, and the worker-only secret decryption boundary.
The offline byte limit cannot undo an oversized allocation made by a transport.

## Fixture probes and limits of evidence

`packages/llm/tests/test_reviewer.py` synthesizes licence-clean PNG pixels and both
wire envelopes without keys or requests. Each codec has a vision-request probe,
verified-negative flow and missed-object correction flow through the real core
validator. The latter requires reinspection rather than granting completeness
immediately. Negative probes cover truncation, refusal, malformed and privileged
patches, unsupported tools, model substitution, usage uncertainty and resource caps.
GitHub Actions discovers these tests through the existing workspace unit job.

These are **synthetic codec fixtures**, not recorded live provider responses and
not evidence that a configured model supports vision, JSON mode, a particular
output-token parameter or this evidence size. Before a configured model is used,
the authenticated, budget-gated worker must run and persist a real fixture probe
for its pinned provider/model/configuration. No such live probe runs in CI.
No annotation quality, discovery independence, promotion qualification, paid
operation safety or secret-handling acceptance is claimed complete by this slice.

Remaining M3 work includes proposal/discovery workers, runtime fixture admission,
authenticated review persistence and expected-revision writes, durable budget
serialization and dispatch, encrypted keys/redaction, adjudication/audit wiring,
and the review UI from the approved designs. This module is unused scaffolding
until those boundaries are connected. Rollback removes it and its internal core
dependency; no schema migration or running application path changes.
