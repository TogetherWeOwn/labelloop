# labelloop v1 screen designs

These are the specs the M3 web UI is built from (plan §13.1, §15 M3 and M5). Build only what is here. If something you need is missing, ask the UX Designer on your card rather than inventing it.

**Design system:** [TogetherWeOwn/two-design-kit](https://github.com/TogetherWeOwn/two-design-kit), Ember Hours theme, **v0.3.0 or later**. That release adds the workbench tokens (label provenance, image well, box overlay sizes), components **J1–J22**, layout patterns **P6/P7** and the tool-copy rules in voice.md §7. Until v0.3.0 is tagged, the additions are in kit PR [#7](https://github.com/TogetherWeOwn/two-design-kit/pull/7). Vendor `dist/css/tokens.css` and `dist/ts/tokens.ts` as the kit README describes. Use tokens only: no raw colours, sizes or durations. Component ids (A1, J4, …) refer to the kit's `docs/components.md`.

## Screens

Build in this order. The review screen comes first.

| # | Screen | Spec | Mockups (1280) | Milestone |
|---|---|---|---|---|
| 01 | Image review | [01-image-review.md](screens/01-image-review.md) | `review`, `review-draw`, `review-audit`, `review-ready` | M3 |
| 02 | New project | [02-new-project.md](screens/02-new-project.md) | `new-project` | M3 |
| 03 | Dataset releases | [03-dataset-releases.md](screens/03-dataset-releases.md) | `releases` | M3 |
| 04 | Size and crop recipes | [04-size-and-crop-recipes.md](screens/04-size-and-crop-recipes.md) | `recipes` | M3 |
| 05 | Training runs | [05-training-runs.md](screens/05-training-runs.md) | `runs` | M3 (local) · M4 (Platform) |
| 06 | Evaluation | [06-evaluation.md](screens/06-evaluation.md) | `evaluation` | M3 |
| 07 | Model registry | [07-model-registry.md](screens/07-model-registry.md) | `models` | M3 · M5 (canary, drift) |
| 08 | Connections | [08-connections.md](screens/08-connections.md) | `connections` | M4 |
| 09 | Costs | [09-costs.md](screens/09-costs.md) | `costs` | M3 (LLM ledger) · M5 |
| 10 | Audit log | [10-audit-log.md](screens/10-audit-log.md) | `audit` | M5 |

Each mockup has `mockups/png/<name>-1280.png` (1280 × 800 viewport) and `<name>-390-full.png` (390 wide, full page); review mockups also have `<name>-1280-tall.png` so the whole inspector shows. Fixture names, numbers and the shelf image are invented.

## Rules that apply to every screen

1. **Provenance is always visible.** Anything a model made or judged says so in words, wherever it appears: on a box, in a table, in history, in a count. Proposed, auto-accepted and reviewed never share a look (kit components.md §J, the provenance table). Only a person reviews; a model proposes or auto-accepts.
2. **One ember surface per view.** It is the page's primary action. Accepting a model's proposal is never the ember action.
3. **Money and spend:** nothing paid starts from one click. J20 shows the estimate, and the button names the amount. When paid steps are paused, every page's header says so.
4. **Secrets:** only J18 fingerprints, ever.
5. **Honest numbers:** a metric names its set and size; estimates say "Estimate"; unsupported classes show the reason, never a number.
6. **Times:** UTC in tables and audit (labelled), viewer-local with the zone in sentences.
7. **No toasts.** Results go in place (notices, history lines) and in the one polite status region.
8. **Live data** (runs, compiles, queues) refreshes every 30 s while visible, by text swap, with a freshness line; never animated (kit accessibility.md §6).

## Shell and navigation

- **J1 header** on every screen: product mark · project switcher · sections: Review (with queue count) · Releases · Recipes · Runs · Evaluation · Models · Costs · Audit log · spend status · account menu (Connections, account settings including shortcut preferences, sign out).
- New project is reached from the project switcher ("New project").
- Patterns: Image review uses **P7** (review canvas); every other screen uses **P6** (workbench page).
- The AGPL source offer is in the footer on every P6 page and in the account menu on P7: "labelloop is free software under the AGPL-3.0. Get the source code." (plan §12.3).

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
