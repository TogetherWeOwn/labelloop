# Accessibility: workbench exception

labelloop follows the Ember Hours accessibility rules (WCAG 2.2 AA; [TogetherWeOwn/two-design-kit](https://github.com/TogetherWeOwn/two-design-kit) **v0.3.1** (`4eb4181`), `docs/accessibility.md`). The one workbench-only rule is below, moved unchanged from that file's §4 "Target size".

## Box-handle target size

**One exception: box handles on a workbench canvas** (the resize handles on a selected label box) keep a 24 × 24 px hit area (`size.target-handle`), the WCAG floor, centred on the handle. Hit areas never overlap, because handles appear only when the selected box is big enough on screen: corner handles when it is at least 24 px in both directions, edge handles when it is at least 48 px. A smaller box shows no handles (a 7 × 3 px price tag at 320 shows none) until the reviewer zooms in. Only the selected box has handles, and they sit above every other box. With 36 px areas, boxes under 36 px on screen would lose every handle and boxes under 72 px their edge handles, and many labels are that small. Every handle action has an equivalent that needs no handle: arrows and Alt+arrows move and resize the selected box, and its coordinates can be typed (SC 2.5.7). No other control uses this exception.
