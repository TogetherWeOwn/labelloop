# Platform UI modules (fixture-only)

`apps/web/src/features/platform` implements the accepted Connections and Platform
portion of Training runs from `docs/design/screens/08-connections.md` and
`05-training-runs.md`. It uses the checked-in Ember Hours token snapshot and
scoped versions of the specified P6, A1/A4/A11/A12/A14, J13/J18/J20 compositions.
No navigation, sign-in, local training view or application shell is replaced.

## Mount boundary

Import `PlatformModule` with `role`, `page: 'connections' | 'runs'`, and a stable
`PlatformReadPort`. The shell must key the mount by authenticated principal and
project (and remount on port replacement) so data cannot survive a scope switch.
Reviewer Connections does not mount the reader at all. Runs are read-only for
reviewers; management controls are not rendered.

The only shipped port is `fixturePort()`. Its `source: 'fixture'` record is always
labeled as a preview. This module deliberately has **no command port**: key entry,
validation, workspace selection, policy changes, start, cancel, retry and resume
are disabled. It cannot cause spend even if a view caller supplies `admin`.
The role prop is presentation only, never authentication or authorization.

Future live integration must use authenticated server records and authorization,
validate responses, expose fingerprint metadata only, retain exact returned
workspace IDs/slugs, use persisted operation status, bind approval to the dry-run
and ledger estimate, and enforce monthly/iteration/operation limits on the server.
Do not convert connection validation into training permission. That work belongs
to the auth/adapter/ledger integrator; it is not delivered here. Secret replacement
and its audit event, cancellation confirmation, links/artifact download and the
final live end-to-end smoke remain integration work, not simulated successes.

Reads refresh every 30 seconds while visible, avoid overlapping requests, abort
on unmount, preserve prior data with an error/stale notice after refresh failure,
and never echo exception text. Freshness ages independently of read completion,
including a hanging second request, and is rechecked when the tab becomes visible. Fixture timestamps are fixed, so their stale label
is intentional. Paid-stage pause is supplied explicitly by the project snapshot;
a 402 has no automatic mutation/retry path. The shell owns its global J1 paused
header; the isolated module also shows an in-place pause notice.

## Verification on GitHub Actions

The existing `web-build` job runs lint, typecheck, Vitest, production build and a
separate fixture build. `platform-fixture.html` is a test-only Vite input, not part
of the normal build or an application auth route. The browser script covers:

- 1280/390 Connections and Platform runs; loading, empty, error, stale, check
  failure, paused and over-limit snapshots;
- reviewer disclosure/control gates after a deliberately delayed read, keyboard
  activation and run selection, freshness during a hanging refresh;
- WCAG A/AA and best-practice axe results without suppressions, including every
  open dry-run panel (normal, over-limit and paused) before it is closed;
- no mutation requests; synthetic credential sentinels and credential-shaped
  values absent from emitted bundle text;
- uploaded screenshots in `platform-ui-evidence` for the exclusive UX reviewer.

No full suite or build qualification is run locally. No paid or live Platform
request is needed. The preview query role/state switches exist only in the
separate fixture entry; never import that entry into the authenticated app.

## Dependency controls

pnpm 10.28.2 supports and reports `minimumReleaseAge: 4320` and
`ignoreScripts: true`. The lock update adds only pinned `@playwright/test` 1.56.1,
`playwright`/`playwright-core` 1.56.1, optional Darwin `fsevents` 2.3.2 and
`axe-core` 4.11.0. Integrity values are recorded in `pnpm-lock.yaml`. Registry
metadata shows no install hooks for Playwright packages; axe's source-development
`prepare` runs husky/patch but is not needed from the published artifact and is
not allowed. Root/web scripts have no install/prepare/pack hooks and no pnpm hook
file exists. Existing native esbuild/fsevents hooks are not granted exceptions;
all dependency/root hooks remain disabled. No build-script exception is added.

Resolution used the three-day policy and `--lockfile-only --ignore-scripts`; clean
frozen installation and explicit builds are qualified on Actions, not inferred
from that resolution. Frozen locks do not retroactively audit release ages.
Playwright browser installation is an explicit CI-only command; no host package
installation or browser download runs on the shared development host.

REA angle: reuse delivered core budget semantics, accepted screen/token assets,
and the existing native Actions web job; do not create a scheduler, replacement
authentication system, copied third-party implementation or live adapter.
