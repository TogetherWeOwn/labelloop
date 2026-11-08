# ADR 0004: deny Platform activation without immutable vendor identity

Status: specified; implementation pending native QA, Applied Cryptography and
Security review. This does not accept ADR 0003's cryptographic construction.

## Context and evidence

Plan §§8.1 and 12.2 require verifying the intended personal/team workspace before
activating a full-owner, spend-capable Platform credential. The initial internal
port incorrectly compared a returned UUID with the local authorization namespace.
A fixture echoed the requested UUID; that proved neither vendor identity nor a
supported endpoint. Remove that port and fixture, not the identity gate.

REA angle: inspect the accepted official SDK/OpenAPI pin and reuse the safe
adapter boundary; do not infer a vendor capability or invent an endpoint.

Evidence: SDK 0.1.107, official OpenAPI at commit
`8af392188a631de9275039183b290a256e8f79ab`, SHA-256
`141c6c80558c733e362286920be8b7a73f93f5c43ce5dd93bf4dea3da8c816ed`.
The checked-in snapshot/provenance lives in `packages/platform`.

- Authenticated `GET /api/account/summary` returns username, account type,
  subscription/counts and memberships, but no immutable ID of the key's workspace.
- Authenticated `GET /api/api-keys` requires owner access, but returns key metadata,
  not workspace identity. Owner proof is necessary, not sufficient.
- Public `GET /api/users?username=...` allows anonymous access.
- Team membership IDs do not identify the workspace authenticated by the key.
  Handles have no documented immutability or non-reassignment guarantee.

## Decision

Keep distinct local `workspace_id` (authorization/AAD scope) and vendor
`platform_workspace_id` (immutable identity). The latter must be returned by a
documented, bearer-authenticated operation identifying the key's own workspace.
It is bound once at enrollment and must compare exactly on replacement. Never
compare vendor identity with the local UUID. Do not derive a UUID from a handle,
echo the request, infer it from memberships or use undocumented endpoints.

No supported operation at this pin can satisfy this requirement. The delivered
worker therefore always ends a scoped pending Platform intent in the terminal
state `identity_unverifiable`, recording the bounded outcome in the existing
immutable audit lineage. This applies to both enrollment and replacement and
preserves the prior active version, if one exists. It needs no decryption or
network call. There is no injectable reader that can declare a fake success.
The internal persistence primitive accepts trusted validation decisions for
state-machine testing and LLM activation; it is not a provider authorization API.
Privileged HTTP routes remain absent.

Do not add summary/key-list precondition calls now: no combination of their
responses can change this identity denial. No live fixture or live qualification
is claimed. Tests distinguish storage transitions from Platform validation and
cover audited denial, restart/replay, scoped lookup, unchanged prior keys and
PostgreSQL terminal/audit guards. No fixture fabricates a vendor workspace ID.

## Conditions for future activation (not implemented by this change)

Before enabling activation, review a new supported operation and contract-derived
fixtures through the bounded safe adapter. Implement an immutable enrollment
binding and uniqueness of vendor ID to one local workspace per deployment.
Replacement jobs and activation must use the same binding revision; stale or
mismatched bindings reject, never retarget. Require exact returned vendor-ID
equality and owner-access status for the same candidate key; never persist/log
key-list metadata. Preserve principal/scope/fingerprint audit lineage and add the
verified vendor ID only when it actually exists. Test mismatch, duplicate binding,
owner denial, missing/malformed identity, membership/public-profile substitution,
transport failure and stale validation. Handles are display/drift data only.

Engineering owns re-checking official SDK/OpenAPI identity support at every pin
bump and by 2026-12-08 at the latest. If no source exists, activation remains
denied; a third-party request requires the normal upstream publication gates.
Revising the evidence threshold is a security-policy decision, not an engineering
shortcut. Native reviews, exact-head Actions, credential and production gates
remain unchanged. No real credentials, calls, deployments or database mutations
are authorized by this ADR.
