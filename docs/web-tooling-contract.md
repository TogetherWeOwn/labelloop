# Web lint consumer compatibility

The web development runtime is Node 24, as documented in the README and used by
CI. The workspace uses pnpm 10.28.2 with `minimumReleaseAge: 4320` and
`ignoreScripts: true`. Neither this resolution nor its tests relax those controls.

## Repository-owned resolution

The workspace selects minimatch 11.0.4 **only** for
`@typescript-eslint/typescript-estree@8.72.1`. Other minimatch consumers are not
changed. The parent declares `^10.2.2`, so this is a repository-maintained
compatibility contract, not a claim of upstream-declared major-11 support.

The contract covers the parent's named CommonJS `minimatch` call with
`{ dot: true }`, reached by its optional `projectService.allowDefaultProject`
path. It does not promise compatibility for removed matcher APIs the parent does
not use. In particular, ordinary project-service lint alone does not cover this
optional path.

`apps/web/scripts/lint-consumer-contract.mjs` resolves the matcher from the
installed parent, not an unrelated direct dependency. It checks benign positive
and negative matches, dotfiles, directory boundaries, globstar, braces, extglobs,
negation, invalid pattern types, Unicode and case handling. It also parses files
through the actual project service with the allowlist unset, empty and enabled,
and checks that wide-glob rejection and project-overlap errors remain intact.
The matcher-level globstar case does not authorize globstar in the parent's
allowlist: the integration test separately requires that configuration to fail.

CI runs the same contract against the PR base's frozen toolchain and the proposed
toolchain on Node 24. Explicit matcher platform cases cover slash and backslash
semantics; these are not a claim of end-to-end Windows runtime qualification.
The normal lint, typecheck, unit, build and browser gates remain unchanged.

## Provenance and maintenance

The selected minimatch release is published from upstream commit
`f24eba95d759c794f9f2529a650b6f9418544228` and licensed BlueOak-1.0.0. Its new
`@isaacs/nonrecursive-glob-match` dependency is also BlueOak-1.0.0;
`brace-expansion` and `balanced-match` are MIT. Registry artifacts include built
CommonJS and ESM entry points: no prepare or install-hook exception is required.
The lockfile records the actual selected versions and SHA-512 integrities. No
upstream source is vendored or modified.

The repository's DevOps maintainer owns this resolution and its contract tests.
Any update to the named parent or matcher requires rechecking the actual consumer,
Node support, transitive packages and the baseline/candidate contract. A mismatch
is a failed gate, not permission to broaden fallback-program membership or disable
project-service lint.

Retire the scoped resolution when a stable upstream parent declares the selected
matcher major and passes the same contract plus the normal exact-head CI and
independent reviews. Remove the temporary base comparison and exact-version guard
as part of that reviewed retirement; retain useful consumer regression coverage.
Rollback restores the prior workspace configuration and lockfile together through
a reviewed change. This document does not authorize a release or deployment.

## Lockfile updates

CI uses native `pnpm install --lockfile-only` under the workspace policy and
uploads `web-lock-resolution`. It then performs a frozen install and requires the
resolved lockfile to match the committed file. A changed resolution is deliberately
red until the artifact is inspected and committed; a generated artifact is not
exact-head acceptance evidence. After committing it, all required checks must run
again on that exact head. No workflow writes to a branch or uses write credentials.
