# Management UI fixture slice

## Status and integration boundary

This is an **incomplete, fixture-backed implementation**, not a v1 completion or
live integration claim. The production entry and health page are unchanged.
`ManagementModule` supplies the six accepted management views without navigation,
authentication, a job queue, a ledger, or another persistent store. The separate
`management-fixture.html` entry is a browser-test harness, not the M3 shell.

The shared shell will provide the authenticated role, navigation, source footer
and generated management client. A `ManagementPort.read` returns canonical
records; `execute` is optional and is absent from recorded fixtures. The module
also refuses commands for a reviewer, fixture provenance, stale/failed reads, or
an already-running command. This is presentation defense, **not server-side
authorization**. The facade must authenticate/authorize every read and command,
validate inputs and expected revisions, reconcile uncertain mutations and keep
all canonical, split, qualification and budget gates authoritative.

Platform training belongs in the `RunsView.platform` slot; canary/drift evidence
belongs in `ModelsView.extensions.render(recipe_id)`. These views do not own
Connections, Costs, Audit, review, credentials, paid commands or spend policy.
The operational module and generated client must be adapted at integration; no
second registry, status poller or full-screen navigation should be mounted.

## Implemented surfaces and evidence targets

| Accepted screen | Fixture implementation | Contract/browser coverage |
| --- | --- | --- |
| 02 New project | Six inspectable steps, Proposed catalog/reference provenance, individual accept commands, revision-pinned class field editing, dataset/source/reference file-selection and assistant draft request capture and reference-link metadata entry, scope/guide/source-metadata fields with explicit save/discard and retained conflicts, recorded import row validation, read-only source dimensions/hash/group/assignment facts, approved-version summary, dependency explanation | Reviewer controls absent, class/reference payloads and separate acceptance, retained failed/conflicting edits, keyboard step navigation, import errors do not approve classes, missing source details stay unavailable, metadata alone cannot authorize pair decisions |
| 03 Dataset releases | Accepted-label provenance, distribution table, changed images, exclusions, gate/build/quarantine/identical/frozen states, in-place freeze confirmation | Frozen release exposes no edit/freeze, compiler reason visible, freeze pins expected revision |
| 04 Size/crop recipes | Shared-scale J22 frames, recorded affine/box geometry, image-level exclusion consequences, locked recipe versioning, compilation/artifact surfaces | Browser rectangles cross-checked with the Python compiler geometry, 640 frame twice 320, compile pins recipe IDs |
| 05 Local training | Local-only run table and selected details, explicit start/cancel panels, fixed-shape development selection, settings mismatch table, retention and checkpoint hashes | Start pins release/recipe/base hash, size not user-overridden, cancel explicit, no paid start |
| 06 Evaluation | Original-frame set+n, recipe separation, unsupported classes without numeric fallback, J14 bars plus text/table, slice support and lost coverage, provider diagnostics, locked-test reuse confirmation | Unsupported sentinel metrics suppressed, recipe switch, explicit development-reuse acknowledgement |
| 07 Base registry | Per-recipe champion/candidate, complete atomic deployment contract, server qualification reason, history, promotion and reasoned rollback confirmations | No mutation on opening a panel, exact release/revision payload, rollback reason required, independent recipe targeting |

All views have reviewer/read-only treatment. The module boundary provides static
loading, error/retry and stale data states; empty states belong to each collection.
Visible records refresh every 30 seconds without progress animation. Only status
or freshness changes enter the single polite status region, not every progress
number. Recorded fixtures always identify their fixed timestamp as recorded,
not as fresh live observations. Results are in place, never toasts.

Setup detail projections are optional until the producer delivers them. A missing
import report is not a successful import; missing source details never turn
collection totals into invented image records. The recorded source digests are
invented demonstration values, not hashes of delivered files. Validation errors
and source metadata are inspectable only: they cannot accept classes, approve a
step, assign a split or resolve a near-duplicate pair. Dataset ingestion, file
upload, hash computation and source-pixel delivery are still unconnected.

The browser harness exercises 1280 and 390 widths, both roles and the shared
states plus all six setup steps with WCAG A/AA and best-practice axe rules,
keyboard step activation, skip-link keyboard behavior,
page-overflow checks, screenshots, and a separate command-payload capture mode.
That mode has **no server connection and never claims to change a canonical
record**. A separate dispatch-contract fixture exercises `ManagementModule`
itself with explicitly simulated server provenance: successful capture, reviewer
control hiding, fixture/stale refusal and an uncertain command that stays disabled
until records refresh. Lifecycle fixtures additionally cover port replacement
before a read and during a command, a refreshed freeze confirmation, first-result
arrival after an empty evaluation, retained conflicting form edits, and explicit
save-before-approval sequencing. No callback in any harness performs a real
mutation. `management-evidence/results.json` records the checked-out commit:
Actions explicitly checks out the PR head and verifies it before setting the
artifact identity, rather than labelling a synthetic merge tree as the head. `recipe-dom.json` is checked against `full_frame_transform` by
`scripts/check_management_geometry.py`. No paid/provider calls occur.

## Fixture provenance and design fidelity

The Ember Hours tokens are vendored from `two-design-kit` **v0.3.0**, commit
`916ed1ee253e107904def1b0bdaf8fb3393b473b`, `dist/css/tokens.css`, unmodified.
Composition and components implement `docs/design/screens/02` through `07` and
P6/A1/A11/A14/J13–J22. No new palette, tokens or design language is introduced.
J14 uses the approved neutral current/candidate marks; J15 uses the approved
provenance roles, with all quantities also in text/tables.

`shelf-fixture.svg` is generated from the checked-in, licence-clean invented
scene in `docs/design/mockups/src/fixture.cjs`; it is not a customer image.
All management metrics, classes, hashes, principals and release counts are
invented demonstration records, not independent gold or actual training results.
The displayed price-tag geometry follows the actual source box (42 × 20 px):
14 × 6.67 at 640, and 7 × 3.33 at 320. This avoids rounding it into a different
retention decision. The browser only draws recorded derived coordinates.

The mockup's partial-class promotion example is **not implemented as a new
qualification rule**. Delivered `PromotionPolicy.qualify` rejects an unsupported
class. The fixture therefore shows the current model retained; only an explicit
qualified result from the authoritative server can expose promotion. Any future
subset-release policy must be separately specified and reviewed, not inferred by
this UI. This is a fidelity/integration question for the design and API owners.

## Published detail-source alignment

The facade at `fe95fbb292b06486040b531b01461ecd1507ba35` publishes
`apps/api/openapi/management.openapi.json` and the generated
`apps/web/src/generated/management-client.ts` with 19 operations, including
`readProject`, `readFreezePlan`, `readDatasetRelease`, `readRecipePreview`,
`listTrainingProfiles`, `readTrainingRun`, `readEvaluation` and `readRegistry`.
Its `docs/management-contract.md` is the integration map. Publication/drift/type
verification was pending at publication; this is not evidence of mounted routes,
working authentication, queue binding or persistence. Consume those generated
types/operations at final integration, not a hand-copied or second network client.

The existing fixture command captures are deliberately **not wire payloads**:

- Start selects an immutable, server-approved training profile ID and the
  variant revision; the fixture's epoch/base-hash fields are not dispatch authority.
- Compile dispatches one request and idempotency key per recipe with the dataset
  release revision; it must refresh revisions between commands as needed.
- Freeze consumes authoritative ontology, assignment and annotation membership
  from `readFreezePlan`; the server rechecks them under the transaction lock.
- Missing reports, thresholds and check results remain unavailable/unqualified.
  Geometry, checkpoints, evaluation and atomic registry data reuse core types.

Setup writes and final-test/promotion/rollback retain their documented producers.
The fixture class editor captures only editable name/aliases/parent/exclusions and
mirror-safe fields. It pins the inspected draft ID, revision and permanent class
ID, retains failed/conflicting edits, blocks navigation/acceptance while open,
and cannot write example counts or approval provenance. Saving does not accept a
Proposed class. Its existing A11 fields, A1 controls and A14 notices introduce no
new tokens/layout system. Server-owned definition, parent-ID/list normalization,
approval invalidation and actual persistence still require the generated setup
contract; the current 19 operations do not include class editing. No generic
`save_setup` or `edit_class` capture is a wire payload. The fixture port is not a
replacement persistence adapter, auth session or queue.

The reference-link form uses the accepted A11 metadata fields and existing A1/A14
controls only. It requires a class in this draft, a passive HTTP(S) source URL
without embedded credentials, permitted use, view, lighting and scale, with an
optional distinct confusion-class ID. It never fetches the URL or embeds its
pixels. Save pins the draft ID/revision; failed/conflicting entries remain until
explicitly discarded, and navigation/approval cannot race an entry. Only the
refreshed record can supply a new reference ID and Proposed provenance; acceptance
is a separate action using the returned revision. The `add_reference` capture is
not in the published 19-operation wire contract. Ownership, holdout exclusion,
licence validation, approval invalidation, ingestion and actual persistence remain
server responsibilities, not claims made by this form. Uploads remain unconnected.
Reference approval is also unavailable on published/stale/disconnected records.

Declared source metadata keys reuse the existing setup fields and revision-pinned
`save_setup` capture. Only the declaration is editable; dimensions, hashes, group
IDs, split previews and assignment revisions are never submitted. Saving cannot
approve a step or decide a pair, and failed/conflicting declarations are retained.
Scope, guide and source fields can now be explicitly discarded even without a
revision conflict; discard never autosaves. The real typed key normalization,
transitive grouping, cross-split quarantine and approval invalidation still belong
to the producer. This capture is not a generated setup command or working ingestion.

The accepted “Draft with the assistant” action now opens an explicit fixture-only
request capture using the existing A1 controls, A14 notices and facts layout. It
pins the inspected scope, draft ID and revision. Opening/discarding never sends a
command; a changed revision, unapproved scope, paid-pause, reviewer, published,
stale or disconnected state refuses capture. Navigation, editing and approval
cannot race the pending request. A deterministic lifecycle fixture returns one
Proposed row; its acceptance is a separate command at the refreshed revision.
`draft_assistant_classes` sends no class definitions, provider, endpoint, secret,
split, approval or spend settings. It is not in the generated wire contract and
must not be connected to paid generation as-is. Actual permitted-provider,
validated-proposal ingestion, dry-run consent, budget, revision locking and
approval invalidation remain server responsibilities. No LLM/provider call,
model-output parser, real ingestion, persistent store or automatic approval is
implemented by this capture. Its lifecycle, keyboard and axe cases passed at
`dc65c59`; this is fixture behavior evidence, not working generation.

Dataset file selection uses the accepted Classes import action and existing
A11 field, A1 controls, A14 notices and facts layout. Opening, changing or
explicitly discarding a selection never sends a command. Capture pins the
inspected draft ID/revision and includes only the selected file name and byte
count; no file contents, path, class definitions, class IDs, splits, endpoint,
approval or spend settings are submitted. Empty/missing selections refuse capture.
There is no FileReader, archive parser, file-byte read, network upload or format
validation. Filename and size are untrusted metadata, not evidence of valid data.
This deliberately does not impose a made-up supported format or archive-size policy.

Failed/conflicting selections remain visible, an in-flight capture cannot change
files or be discarded, and navigation/editing/approval cannot race the request.
Lifecycle fixtures return either a rejected row report without changing classes,
or one Proposed class with separate acceptance at the refreshed revision. Neither
path changes source facts or split membership. These are deterministic invented
responses, not ingestion or server validation. `request_dataset_import` is not a
generated wire command: actual upload transport, archive safety/limits, ontology
mapping, ownership/licensing, split isolation, revision checks, approval invalidation
and persistence remain with the producer. Do not wire this metadata-only capture
to a real importer as-is. Its browser/keyboard/axe/no-file-read/no-upload cases
passed on Actions at `a6f51ea`; this is not working import evidence.

Source-image selection uses the accepted Sources upload action with the same
A11 fields, A1 controls, A14 notices and J13 table. Multiple files retain their
order and individual byte counts, including same-name files; the browser does not
merge them by filename or infer content identity. Opening, changing, clearing and
explicitly discarding never dispatch. Capture pins the inspected draft/revision
and sends only file names and byte counts. Missing selections or any empty file
refuse the entire capture. Failed/conflicting selections remain visible; pending
requests block selection changes, discard, metadata editing, navigation and approval.
No file bytes, image decoding, hashes, upload, source IDs, groups, splits or labels
are produced. The deferred fixture acknowledges capture without fabricating source
facts or changing totals, pair decisions, split membership or approvals. Original
pair pixels are still unavailable, so relationship decisions remain disabled.

`request_source_upload` is not a generated wire command or a real ingest request.
Transport, supported formats/limits, ownership/licensing, safe image normalization,
immutable original storage, hashes, transitive grouping, cross-split quarantine,
revision locking and approval invalidation remain with the producer. Selection
metadata must not be wired to an importer as-is. Its keyboard/axe, failure,
revision, deferred-response, read-only and no-file-read/no-upload cases passed
on Actions at `bbc1d40`. No new tokens, upload adapter, client, shell or persistent store is added.

Reference file selection reuses the existing Evidence form and A11/A1/A14 fields
and controls. The upload mode requires a class in the inspected draft, a non-empty
file selection, declared source, permitted use, view, lighting and scale, and an
optional different confusion class. It captures only filename, byte count and
those metadata fields, pinned to the inspected draft ID/revision. Source text is
passive provenance, not a fetched URL or a new asset identity. Opening, changing,
clearing and explicitly discarding do not dispatch; failed/conflicting entries
retain both file selection and fields. Pending capture prevents field changes,
discard, navigation, linked entry and approval races. A returned fixture reference
is Proposed and needs separate acceptance using the refreshed revision.

`request_reference_upload` is not a generated wire operation or a working upload.
No file bytes, decoder, image preview, hash, storage, retrieval index, approval,
provider or spend authority is supplied. Transport, supported formats/limits,
licensing, authenticated ownership, holdout exclusion, safe normalization,
proposal validation, revision locking, approval invalidation and persistence
remain producer responsibilities. Do not connect this metadata capture to an
importer as-is. Its browser failure/conflict/deferred-response, separate-acceptance,
read-only, keyboard/axe/overflow and no-read/no-upload cases passed on Actions at `bbc1d40`.
No new tokens, components or second client/store/shell are introduced.

At `c38b6ad`, no Actions runs registered: the PR was conflicted after the Platform
fixture module landed on main. The restack retains both modules' package scripts,
browser checks and required artifacts, the Platform build sentinels, and the
management job's exact-head checkout verification. Only one browser install is
needed. Neither module is mounted as a second production shell. Main's separately
reviewed scanner changes are inherited unchanged; this conflict resolution adds
no scanner ignore, waiver or gate reduction. No source-selection pass is inferred
from this repair; all changed behavior requires new exact-head Actions evidence.

## Unknown setup evidence alignment

The producer's `9cbc85100b7cbe87c09f3ecee434c50b07313799` management-only generated
contract makes absent bootstrap evidence explicit. The fixture projection now
preserves nullable scope name/representation/intended use/specificity, class
reference support/exclusions/mirror safety/approval, reference bank, structured
guide, approved steps and source group/metadata-key/split summary evidence. Null
is displayed as **Unknown**, never an empty bank, zero support, false mirror safety,
Proposed provenance or an approved step. Known empty/false/zero remain distinct.

`annotation_guide` is required stored free text, displayed separately from nullable
structured policy. The browser never parses it into extent, target-size,
completeness or approval defaults. Source counts and pending pairs remain visible
as descriptive facts, not a readiness pass or evidence of a complete locked split.
The fixture port conservatively disables setup commands while this required
evidence is unknown, retaining navigation and inspection. Existing pending edits
remain visible and discardable if a refresh loses evidence; they cannot save or
approve over that refresh. This introduces no wire adapter or typed setup-write
implementation. Do not fabricate these fields when mounting the generated client.

New unit cases cover each independently missing field and distinguish known empty,
false and zero. New browser cases exercise all six steps at 1280/390 for both roles,
keyboard, axe, overflow and no dispatch, plus loss of evidence during an open edit.
These new cases are pending Actions verification. The real `read_project` adapter
remains unmounted; auth, typed setup writes, approval invalidation, publication,
queue and qualification composition still belong to their existing producers.
No raw asset URI/object key, provider policy, data parser, network client, tokens,
components, shell or persistence store is added here.

## Remaining acceptance and final wiring obligations

These remain open; merging fixture components does not discharge them:

- Mount all six screens once in the M3 authenticated shell, reuse its project
  selection, navigation, paid-paused header, one polite status region and footer.
- Bind records/commands to the generated management facade and prove real
  command success, authorization, expected-revision conflict and error paths.
  The typed port is not a generated network client or a backend implementation.
- Finish project dataset import (row errors), assistant-draft ingestion, class
  editing, reference upload/link metadata, source upload/hash facts and real
  near-duplicate image comparison using approved producer contracts. No synthetic
  source comparison can authorize a group decision; its buttons remain unavailable.
- Finish source-pixel delivery and record/variant artifact download routes. Server
  data deliberately never displays the invented fixture scene as a live preview.
- Wire changed/excluded image drilldowns to the existing review shell, the
  Platform run extension and the operational canary/drift extension. Do not mount
  their standalone fixture screens as competing application screens.
- Complete exact-head Actions evidence and native UX, QA and Security stages.
  At `bbc1d40`, [CI/ci-ok](https://github.com/TogetherWeOwn/labelloop/actions/runs/42444426869),
  [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42444426763) and
  [required gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42444426741)
  passed. The [web job](https://github.com/TogetherWeOwn/labelloop/actions/runs/42444426869/job/128620278082)
  passed lint/types, 46 web unit cases (31 management, 11 Platform, four health),
  all three builds, both modules' browser checks, **174 management browser cases**
  and the six-box compiler cross-check. [Management artifact](https://github.com/TogetherWeOwn/labelloop/actions/runs/42444426869/artifacts/13602308575),
  [Platform artifact](https://github.com/TogetherWeOwn/labelloop/actions/runs/42444426869/artifacts/13602493276).
  Source/reference selection is now verified; unknown-setup alignment is not yet.
  At `a6f51ea`, [Actions run `42441797093`](https://github.com/TogetherWeOwn/labelloop/actions/runs/42441797093),
  [web job `128612421496`](https://github.com/TogetherWeOwn/labelloop/actions/runs/42441797093/job/128612421496)
  passed lint, types, 35 web unit cases, both builds, all **166 browser cases**
  (including dataset selection/assistant/source metadata lifecycles, keyboard/axe)
  and the six-box compiler DOM cross-check. [Browser artifact `13601484984`](https://github.com/TogetherWeOwn/labelloop/actions/runs/42441797093/artifacts/13601484984).
  CI/ci-ok, [CodeQL](https://github.com/TogetherWeOwn/labelloop/actions/runs/42441796799)
  and [required gitleaks](https://github.com/TogetherWeOwn/labelloop/actions/runs/42441796800)
  passed on that head. No scanner waiver was needed.
  These results predate source file selection and do not verify its new cases.
  A supplemental fresh-context review of `4272a28` was completed with eight
  findings addressed in `752a914`; it is not native approval. Screenshots
  generated by mockups are not browser evidence.
- Demonstrate original-frame immutability and per-recipe commands through the
  real facade. UI affordances alone do not establish canonical state transitions.

## Supply-chain and verification

`pnpm@10.28.2` supports `minimumReleaseAge: 4320` and `ignoreScripts: true` in the
workspace config. Root and web manifests contain no lifecycle install hooks, and
no pnpm hook file is present. Browser-only dependencies are pinned to
`@playwright/test@1.56.1` and `axe-core@4.11.0`, matching the parallel UI modules.
No install-script exception was added. The explicit Playwright browser install
runs only on the disposable Actions runner. Existing lock entries are not a
retrospective provenance/age audit; these controls do not prove package safety.

Only lockfile resolution was run locally. Frozen installation, lint, types,
unit tests, build, browser/axe and compiler cross-check run on GitHub Actions.
No local full suite or CI mirror is permitted. Artifact output is ignored by git.
The web job pins Ubuntu 24.04 because the rolling Ubuntu 26.04 runner is not
supported by the pinned Playwright 1.56.1 browser/dependency installer. Browser
checks and failure-on-missing-evidence remain required, not skipped.

REA angle: reuse the delivered canonical models, compiler geometry, local-run and
atomic registry contracts plus the accepted kit/screen fixtures. No replacement
engine, scheduler, state store or qualification policy is needed.
