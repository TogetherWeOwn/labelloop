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
| 02 New project | Six inspectable steps, Proposed catalog/reference provenance, individual accept commands, scope/guide fields and save/error copy, approved-version summary, dependency explanation | Reviewer controls absent, individual class acceptance payload, step navigation, unapproved/proposed entries prevent approval |
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

The browser harness exercises 1280 and 390 widths, both roles and the shared
states with WCAG A/AA and best-practice axe rules, skip-link keyboard behavior,
page-overflow checks, screenshots, and a separate command-payload capture mode.
That mode has **no server connection and never claims to change a canonical
record**. A separate dispatch-contract fixture exercises `ManagementModule`
itself with explicitly simulated server provenance: successful capture, reviewer
control hiding, fixture/stale refusal and an uncertain command that stays disabled
until records refresh. No callback in either harness performs a real mutation.
`management-evidence/results.json` records the exact PR head supplied
by Actions. `recipe-dom.json` is checked against `full_frame_transform` by
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
- Complete exact-head Actions evidence, independent fresh-context review and
  native UX, QA and Security stages. Full browser/axe results are pending until
  observed in Actions; screenshots generated by mockups are not this evidence.
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

REA angle: reuse the delivered canonical models, compiler geometry, local-run and
atomic registry contracts plus the accepted kit/screen fixtures. No replacement
engine, scheduler, state store or qualification policy is needed.
