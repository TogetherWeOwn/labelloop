# Workbench tokens

The tokens labelloop's workbench uses that are not part of the shared Ember Hours base. Values are copied from [TogetherWeOwn/two-design-kit](https://github.com/TogetherWeOwn/two-design-kit) **v0.3.1** (`4eb4181`); the *Kit source* column names the file each one came from, and the *Use* text is from that tag's `docs/tokens.md`. Nothing links to the kit at runtime.

**Shared base tokens** (grounds, text, lines, state, focus, type, space, radius, motion) are copied with the same provenance: [`mockups/src/tokens.css`](../mockups/src/tokens.css) is byte-identical to kit v0.3.1 `dist/css/tokens.css` (sha256 `42a80525774dd7ac886097a91d1dd20ca838c3a4eb6d62d22adc48c2f26d86cd`), and holds every value in the tables below as `--two-*` custom properties. Use tokens only: no raw colours, sizes or durations.

## Colour: image well and label provenance

| Token | CSS | Value | Kit source | Use |
|---|---|---|---|---|
| `color.well` (primitive) | `--two-color-well` | `#121212` | `tokens/color.tokens.json` | Primitive behind `bg.well`. |
| `color.machine` (primitive) | `--two-color-machine` | `#7FB2FF` | `tokens/color.tokens.json` | Primitive behind `label.auto`. |
| `color.bg.well` | `--two-color-bg-well` | `#121212` | `tokens/roles.tokens.json` | Behind source images, crops and previews. Neutral, so the warm grounds don't tint pixels. Never behind text. |
| `color.label.proposed` | `--two-color-label-proposed` | `#F2C14E` (= `color.warn`) | `tokens/roles.tokens.json` | A model's proposal nobody has decided: the estimate that matters on a workbench. |
| `color.label.auto` | `--two-color-label-auto` | `#7FB2FF` (= `color.machine`) | `tokens/roles.tokens.json` | Auto-accepted under a qualified policy; machine evidence only. Workbench only; never a link or "online". |
| `color.label.reviewed` | `--two-color-label-reviewed` | `#5FD39A` (= `color.ok`) | `tokens/roles.tokens.json` | Reviewed or adjudicated by a named person. |
| `color.label.uncertain` | `--two-color-label-uncertain` | `#F6F1EA` (= `color.text.1`) | `tokens/roles.tokens.json` | Marked uncertain; always with the hatch and the "?" glyph. |
| `color.label.rejected` | `--two-color-label-rejected` | `#FF7A6B` (= `color.danger`) | `tokens/roles.tokens.json` | Rejected; history and diffs. Also flagged targets in recipe previews. |
| `color.label.ghost` | `--two-color-label-ghost` | `#A89A8E` (= `color.text.3`) | `tokens/roles.tokens.json` | The previous box in a proposed change. |
| `color.label.halo` | `--two-color-label-halo` | `#121212` | `tokens/roles.tokens.json` | 1 px on both sides of every box stroke. |
| `box.dash.proposed` / `uncertain` / `ghost` / `auto` / `reviewed` | `--two-box-dash-*` | `6 4` / `2 3` / `3 3` / `none` / `none` | `tokens/roles.tokens.json` | SVG `stroke-dasharray` in screen px. Pattern carries the tier in greyscale. |

Each tier is a word, a glyph and a stroke pattern as well as a colour ([components.md](../components.md), the provenance rule). Stroke colours are checked against the halo (`bg.well`), not against the image: the halo is what guarantees 3:1 on any pixels.

## Size and layout

| Token | CSS | Value | Kit source | Use |
|---|---|---|---|---|
| `size.target-handle` | `--two-size-target-handle` | 24 px | `tokens/dimension.tokens.json` | Hit area round each box handle; the one exception to the house minimum ([accessibility.md](../accessibility.md)). |
| `size.box.stroke` / `halo` / `handle` / `tab` | `--two-size-box-*` | 2 / 1 / 8 / 22 px | `tokens/dimension.tokens.json` | Box overlay (J4), screen px at any zoom. Handle hit areas use `size.target-handle`. |
| `size.crop.tile` | `--two-size-crop-tile` | 112 px | `tokens/dimension.tokens.json` | Largest native-crop tile on a review screen (J6). |
| `size.toolbar` | `--two-size-toolbar` | 52 px | `tokens/dimension.tokens.json` | Tool row (P7). |
| `size.header.compact` | `--two-size-header-compact` | 60 px | `tokens/dimension.tokens.json` | J1 header height at every width (shared base). |
| `size.row.admin` | `--two-size-row-admin` | 40 px | `tokens/dimension.tokens.json` | J13 table rows (shared base). |
| `layout.rail.min` / `default` / `seat` / `max` | `--two-layout-rail-*` | 320 / 360 / 380 / 420 px | `tokens/dimension.tokens.json` | P6 rail uses `default` (shared base). |
| `layout.inspector` / `queue` | `--two-layout-inspector`, `--two-layout-queue` | 380 / 300 px | `tokens/dimension.tokens.json` | P7 review inspector and queue panel. |
| `breakpoint.compact-max` | `--two-breakpoint-compact-max` | 820 px | `tokens/dimension.tokens.json` | The one layout switch, inclusive (shared base). |
| `breakpoint.review-mobile` / `review-desktop` | `--two-breakpoint-review-*` | 390 / 1280 px | `tokens/dimension.tokens.json` | The two review widths: 390 × 844 and 1280 × 800. |

## Contrast: workbench pairs

From kit v0.3.1 `tokens/contrast.json`, ratios as that tag's `docs/tokens.md` §1.9 reports them (WCAG 2.x relative luminance; text floor 4.5, non-text 3). To use a new pair, compute it and add it here first.

| Foreground | Background | Ratio | Floor | Kind |
|---|---|---|---|---|
| `color.label.proposed` | `color.bg.canvas` | 11.24 | 4.5 | text |
| `color.label.proposed` | `color.bg.surface` | 10.66 | 4.5 | text |
| `color.label.proposed` | `color.bg.raised` | 9.97 | 4.5 | text |
| `color.label.proposed` | `color.bg.overlay` | 9.01 | 4.5 | text |
| `color.label.proposed` | `color.bg.well` | 11.16 | 3 | non-text |
| `color.label.auto` | `color.bg.canvas` | 8.73 | 4.5 | text |
| `color.label.auto` | `color.bg.surface` | 8.27 | 4.5 | text |
| `color.label.auto` | `color.bg.raised` | 7.74 | 4.5 | text |
| `color.label.auto` | `color.bg.overlay` | 6.99 | 4.5 | text |
| `color.label.auto` | `color.bg.well` | 8.67 | 3 | non-text |
| `color.label.reviewed` | `color.bg.canvas` | 10.13 | 4.5 | text |
| `color.label.reviewed` | `color.bg.surface` | 9.60 | 4.5 | text |
| `color.label.reviewed` | `color.bg.raised` | 8.98 | 4.5 | text |
| `color.label.reviewed` | `color.bg.overlay` | 8.11 | 4.5 | text |
| `color.label.reviewed` | `color.bg.well` | 10.05 | 3 | non-text |
| `color.label.uncertain` | `color.bg.canvas` | 16.79 | 4.5 | text |
| `color.label.uncertain` | `color.bg.surface` | 15.92 | 4.5 | text |
| `color.label.uncertain` | `color.bg.raised` | 14.89 | 4.5 | text |
| `color.label.uncertain` | `color.bg.overlay` | 13.45 | 4.5 | text |
| `color.label.uncertain` | `color.bg.well` | 16.67 | 3 | non-text |
| `color.label.rejected` | `color.bg.canvas` | 7.41 | 4.5 | text |
| `color.label.rejected` | `color.bg.surface` | 7.02 | 4.5 | text |
| `color.label.rejected` | `color.bg.raised` | 6.57 | 4.5 | text |
| `color.label.rejected` | `color.bg.overlay` | 5.94 | 4.5 | text |
| `color.label.rejected` | `color.bg.well` | 7.36 | 3 | non-text |
| `color.label.ghost` | `color.bg.well` | 6.85 | 3 | non-text |
| `color.focus.ring` | `color.bg.well` | 8.97 | 3 | non-text |
| `color.bg.canvas` | `color.label.reviewed` | 10.13 | 4.5 | text |
