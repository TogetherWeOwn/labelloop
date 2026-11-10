# labelloop v1 screen designs

These are the specs the review and v1 management UI is built from ([plan §13.1](../plan.md#131-main-screens) and [§15](../plan.md#15-implementation-milestones)). Design readiness before M3 implementation does not mean every screen ships in M3. Build only what is here. If something you need is missing, ask the UX Designer on your card rather than inventing it.

**Design system:** [TogetherWeOwn/two-design-kit](https://github.com/TogetherWeOwn/two-design-kit), Ember Hours theme, **v0.3.0 or later**. That release adds the workbench tokens (label provenance, image well, box overlay sizes), components **J1–J22**, layout patterns **P6/P7** and the tool-copy rules in voice.md §7. Until v0.3.0 is tagged, the additions are in kit PR [#7](https://github.com/TogetherWeOwn/two-design-kit/pull/7), stacked on [#8](https://github.com/TogetherWeOwn/two-design-kit/pull/8) (the 24 px box-handle target exception, accessibility.md §4). Vendor `dist/css/tokens.css` and `dist/ts/tokens.ts` as the kit README describes. Use tokens only: no raw colours, sizes or durations. Component ids (A1, J4, …) refer to the kit's `docs/components.md`.

## Screens

The review screen comes first. The column below describes shipping scope, not when the designs must be ready.

| # | Screen | Spec | Mockups (1280) | Shipping scope / additions |
|---|---|---|---|---|
| 01 | Image review | [01-image-review.md](screens/01-image-review.md) | `review`, `review-draw`, `review-audit`, `review-ready` | M3 |
| 02 | New project | [02-new-project.md](screens/02-new-project.md) | `new-project` | v1 management |
| 03 | Dataset releases | [03-dataset-releases.md](screens/03-dataset-releases.md) | `releases` | v1 management |
| 04 | Size and crop recipes | [04-size-and-crop-recipes.md](screens/04-size-and-crop-recipes.md) | `recipes` | v1 management |
| 05 | Training runs | [05-training-runs.md](screens/05-training-runs.md) | `runs` | v1 management (local) · M4 (Platform) |
| 06 | Evaluation | [06-evaluation.md](screens/06-evaluation.md) | `evaluation` | v1 management |
| 07 | Model registry | [07-model-registry.md](screens/07-model-registry.md) | `models` | v1 management · M5 (canary, drift) |
| 08 | Connections | [08-connections.md](screens/08-connections.md) | `connections` | M4 |
| 09 | Costs | [09-costs.md](screens/09-costs.md) | `costs` | M3 (LLM ledger) · M5 |
| 10 | Audit log | [10-audit-log.md](screens/10-audit-log.md) | `audit` | M5 |

### Design readiness and shipping gates

The designs for screens 02–07 were prepared for M3 readiness, as required before UI implementation by [plan §18](../plan.md#18-decisions-taken-in-v03-and-open-items-v03). Their base management UI ships with v1 management, not as part of the M3 review slice. M3 remains the shared shell, sign-in and roles, Image review (01), and the shared Costs surface for the LLM ledger, alongside the review and LLM work in [plan §15](../plan.md#15-implementation-milestones).

The full base management screens must ship before declaring v1 complete, but do not add exit gates to M3–M6. Authenticated management routes must nevertheless be available before demonstrating the new-project workflow in M6 acceptance check 22 ([plan §16](../plan.md#16-required-acceptance-checks)); route readiness is not a claim that all management screens are complete. The existing M4 Platform additions, M5 canary/drift additions, Costs and Audit log scope, and milestone acceptance checks remain unchanged. This separates design readiness from delivery sequencing without changing the screen contracts in [plan §13](../plan.md#13-application-interface-and-internal-contracts).

Each mockup has `mockups/png/<name>-1280.png` (1280 × 800 viewport) and `<name>-390-full.png` (390 wide, full page); review mockups also have `<name>-1280-tall.png` so the whole inspector shows. Fixture names, numbers and the shelf image are invented.

## Rules that apply to every screen

1. **Provenance is always visible.** Anything a model made or judged says so in words, wherever it appears: on a box, in a table, in history, in a count. Proposed, auto-accepted and reviewed never share a look (kit components.md §J, the provenance table). Only a person reviews; a model proposes or auto-accepts.
2. **One ember surface per view.** It is the page's primary action. Accepting a model's proposal is never the ember action.
3. **Money and spend:** nothing paid starts from one click. J20 shows the estimate, the button names it ("Start · estimate $1.38") and a line says which limits are enforced; an estimate is never worded as a cap. When paid steps are paused, every page's header says so (J1 paused variant: "Paid steps paused" in `state.danger` in place of the meter, on the Costs mockup).
4. **Secrets:** only J18 fingerprints, ever.
5. **Honest numbers:** a metric names its set and size; estimates say "Estimate"; unsupported classes show the reason, never a number.
6. **Times:** UTC in tables and audit (labelled), viewer-local with the zone in sentences.
7. **No toasts.** Results go in place (notices, history lines) and in the one polite status region.
8. **Live data** (runs, compiles, queues) refreshes every 30 s while visible, by text swap, with a freshness line; never animated (kit accessibility.md §6).

## Shell and navigation

- **J1 header** on every screen: product mark · project switcher · sections: Review (with queue count) · Releases · Recipes · Runs · Evaluation · Models · Costs · Audit log · spend status · account menu (Connections, account settings including shortcut preferences, sign out).
- New project is reached from the project switcher ("New project").
- Patterns: Image review uses **P7** (review canvas); every other screen uses **P6** (workbench page).
- The AGPL source offer is in the footer on every page: the full footer on P6, on P7 a one-line footer inside the viewport height above 820 px; at 820 px and below the review page scrolls and the footer follows the inspector (kit templates.md shell rule): "labelloop is free software under the AGPL-3.0. Get the source code." (plan §12.3).

## Roles (plan §12.2)

| | Reviewer | Admin |
|---|---|---|
| Image review | yes | yes |
| Exclude an image | asks an admin | yes |
| New project, classes, recipes | read only | yes |
| Releases, runs, evaluation | read only | yes |
| Promote, roll back | no | yes |
| Connections | no ("Only an admin can manage connections.") | yes |
| Costs | read only | yes |
| Audit log | review events only | everything |

A control the viewer's role can't use is not rendered. When a whole page is read-only, one line under the H1 says so.

## Shared states

Every screen renders: loading (structure first, no shimmer), empty (C3: an honest sentence and one next step), error (what happened and what to do, no codes in the sentence), stale data (freshness line in warn), read-only, and narrow (390). The per-screen specs list their own.

## Regenerating the mockups

The mockups are HTML rendered from `mockups/src` using the kit's `tokens.css` (copied from kit PR #7). From `docs/design/mockups/src`:

```sh
./fetch-fonts.sh                       # OFL fonts, not committed
node build-review.cjs && node build-pages.cjs
# needs playwright + axe-core installed somewhere: REPO=<dir with node_modules>
REPO=… node shot.cjs review.html ../png/review tall
REPO=… node axe.cjs *.html             # exits 1 on any WCAG 2.0–2.2 A/AA or best-practice violation
```

The mockup CSS (`workbench.css`) is a reference for spacing and composition, not production code. It uses kit variables only.
