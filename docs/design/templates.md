# Layout patterns P6 and P7

The two workbench layout patterns and the rules for tool screens. Moved unchanged from [TogetherWeOwn/two-design-kit](https://github.com/TogetherWeOwn/two-design-kit) **v0.3.1** (`4eb4181`), `docs/templates.md`: the P6 and P7 rows of "Layout patterns", the "Tool screens (P6, P7)" shell note and "Tool surfaces (P6, P7)". Only links were repointed. Component ids are in [components.md](components.md); P1–P5, T9 and the base shell (A16 skip link, B1, B3) are the kit's at v0.3.1.

## Patterns

| Pattern | 1280 | 390 | Used by |
|---|---|---|---|
| **P5 Admin dense** | current admin layout, kit tokens, `size.row.admin` | tables scroll inside their own container, never the page | Admin |
| **P6 Workbench page** | J1 header · page header (H1 at `font-size.h3`, two-tone, no serif) · `minmax(0,1fr)` + rail (`layout.rail.default`) or two equal columns, 40 px gap, in the container | one column; the rail follows the main column unless it holds the page's decision | Tool screens (labelloop) |
| **P7 Review canvas** | J1 header · sticky tool row (`size.toolbar`) · `minmax(0,1fr)` stage + inspector (`layout.inspector`, 380). Full viewport height; stage and inspector scroll independently; the decision dock (J12) sticks to the inspector bottom; the footer closes the viewport (at least `space.7`, grows with its text; shell rule below) | one column: stage, then inspector; drawing tools hidden with a note; the dock becomes a fixed bottom bar | Image review (labelloop) |

**Tool screens (P6, P7)** use the workbench header (J1) in place of B1; B1 never appears on a tool screen. Shell: skip link (A16) · J1 · `<main id="main">` · `<footer>` in place of B3: a source line (licence offer + version, `text.tertiary`). On P6 it is a normal footer after the content; on P7 it sits inside the viewport height under the stage and inspector above `breakpoint.compact-max`: at least `space.7` high, never a fixed height, so wrapped text (zoom, text spacing; kit accessibility.md §8) grows the footer and the stage and inspector take the height that is left. At 820 px and below the page scrolls, the footer follows the inspector, and the page keeps bottom padding equal to the fixed J12 bar so the bar never covers the footer. Every tool screen keeps the footer landmark (kit accessibility.md §7). One top bar; at 390 the review canvas adds the decision dock as its bottom bar (J12) and nothing else does.

## Tool surfaces (P6, P7)

Workbench screens follow the kit's T9 admin restraint: no serif accent, no gradient, H1 at `font-size.h3`, one ember surface per view. Screen specs are in [screens/](screens/) and cite the ids in [components.md](components.md). Rules that differ from site pages:

1. **The decision comes first at both widths.** On P7 that is the selected object's proposal and the decision dock; on P6 the page's one primary action sits in the page header.
2. **Tools are desktop-first, not desktop-only.** At 390 a tool screen keeps every decision a person can make by tapping (accept, keep, mark uncertain, change class) and hides only what needs a pointer and space (drawing, redrawing, split, merge), with a one-line note saying so.
3. **Independent scroll on P7 only.** The review canvas never scrolls away while the inspector scrolls. Every other tool page scrolls as a page.
4. **Sticky on tool screens:** the J1 header, the P7 tool row and the J12 dock. Nothing else.
