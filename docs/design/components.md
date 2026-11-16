# Workbench components

The workbench components (J1–J22) that labelloop's screens are built from. Moved unchanged from [TogetherWeOwn/two-design-kit](https://github.com/TogetherWeOwn/two-design-kit) **v0.3.1** (`4eb4181`), `docs/components.md` §J; only links were repointed to this directory. This copy is now the source of truth for labelloop; the kit no longer owns it.

Ids outside J (A1 button, A4 tag, A9 segmented control, B1 header, C3 empty state, I1 admin table and the rest) are Ember Hours base components, as defined in the kit at v0.3.1, `docs/components.md` §A–I, together with the states every interactive component has (hover, focus-visible, active, disabled, loading). Values are tokens: see [tokens/README.md](tokens/README.md).

## Workbench (tool surfaces)

Components for dense tool screens where people judge images and model output: labelloop's review, release, training and registry screens. They follow every Ember Hours base rule (tokens only, one ember surface per view, state as word + shape + colour, 36 px controls, 12 px text floor) and add the parts a tool needs. Layout patterns P6 and P7 are in [templates.md](templates.md). Workbench copy follows [voice.md](voice.md).

**The provenance rule.** Anything made or judged by a model shows who made it, in words, every time it is shown: a badge, a tab, a history line or a column. A model's output never looks like a person's decision. Three tiers matter most and must never be confused:

| Tier | Word | Glyph (J3) | Box stroke (J4) | Colour role | Means |
|---|---|---|---|---|---|
| proposed | "Proposed" | dashed ring | dashed `box.dash.proposed` | `label.proposed` | A model suggested it; nobody has decided. |
| auto-accepted | "Auto-accepted" | hollow diamond | solid + 4 corner brackets | `label.auto` | A qualified policy accepted it. Machine evidence only; no person looked. |
| reviewed | "Reviewed" (or "Adjudicated", "Yours") | filled disc with a check | solid + filled tab | `label.reviewed` | A named person decided it. |

Two more states exist: **uncertain** ("Uncertain": dotted ring with a question mark, dotted stroke over a hatched fill, `label.uncertain`) and **rejected** ("Rejected": ring with ×, `label.rejected`, history and diffs only). Greyscale test: with colour removed, the five are still told apart by stroke pattern, corner marks, tab fill and glyph.

### J1. Workbench header

B1 for tools. 60 px (`size.header.compact`) at every width, `elevation.sticky`, bottom hairline. **Anatomy:** product mark + name · project switcher (a `bg.surface` button, `radius.m`, opens a listbox of projects) · section nav (pills as B1, 36 px, a mono count after a section that has a queue: "Review 37") · end cluster: spend status (J17 mini meter + mono "$3.10 of $40", links to Costs) · avatar + "Name · Role". **At ≤ 820 px:** mark · project switcher · avatar · Menu (B2 sheet holds the sections). **States:** current section (`aria-current="page"`); spend over 80 % (mini meter `state.warn`) or paused (the spend text reads "Paid steps paused", `state.danger`); no project yet (switcher reads "New project"). **A11y:** `<nav aria-label="Project">`; counts have hidden words ("37 images to review"); the switcher is named "Project: Shelf audit. Switch project".

### J2. Provenance badge

A4 tag with a leading 12 px J3 glyph. Variants `proposed`, `auto`, `reviewed`, `uncertain`, `rejected`: text in the tier's colour role, outline that colour at 45 %. **Words:** "Proposed", "Auto-accepted", "Reviewed", "Adjudicated", "Yours" (a box the viewer drew, before they leave the image), "Uncertain", "Rejected". Detail goes beside the badge, not in it ("Proposed · class change"). **Never:** a badge without its glyph; "Verified", "Approved" or "Confirmed" for anything a model decided.

### J3. Provenance glyph

16 px (12 inside a badge), drawn on a 16-unit grid, `aria-hidden="true"`; the word next to it carries the meaning. proposed: dashed ring · auto: hollow diamond · reviewed: filled disc with a check cut out in `bg.canvas` · uncertain: dotted ring with "?" · rejected: ring with ×. Forced colours: glyphs use `currentColor` and stay visible.

### J4. Box overlay

**Job:** draw labels on an image so provenance, selection and proposed changes read at a glance, on any pixels.

**Anatomy, per box:** a 1 px `label.halo` line on both sides of a 2 px stroke (`size.box.stroke`, `size.box.halo`, both screen px, never scaled with zoom), so every stroke holds 3:1 on any image. Stroke pattern and colour by tier (table above). Auto-accepted adds 4 corner brackets (5 px, 9 px long). Uncertain adds a 45° hatch at 35 % of `label.uncertain`. **Tab:** `size.box.tab` (22 px) above the top-left corner, opaque `bg.canvas` ground (text never sits on pixels), 1 px outline in the tier colour, J3 glyph + mono object number; reviewed tabs are filled `label.reviewed` with `bg.canvas` text. **Tab placement:** above the box; below it when the box touches the top edge; to the right, vertically centred, for boxes smaller than the tab; end-anchored for boxes at the right edge. Tabs never leave the image well.

**Tab modes** (one control, key L): Numbers (glyph + number; the default above 8 boxes) · Names (adds the class, or the proposal: "Soy milk → Almond milk", "Almond milk · new box", "Split into 2", "New · Price tag") · Off. The selected box always shows Names.

| State | Looks |
|---|---|
| proposed class change | box as proposed; tab names the change |
| proposed new geometry | the current box as a ghost (`label.ghost`, `box.dash.ghost`, no tab) and the proposed box in proposed style |
| proposed split | the current box as a ghost and each part in proposed style; one tab "Split into 2" |
| proposed new object | proposed style; tab "New · {class}" |
| selected | a 2 px `focus.ring` outline 4 px outside the box, 8 px handles (`size.box.handle`, `text.primary` with a halo) on corners and edges, each with a 24 px hit area (`size.target-handle`; the box-handle exception to the 36 px house minimum in [accessibility.md](accessibility.md)). Corner handles show only when the box is at least 24 px on screen both ways, edge handles at 48 px, so hit areas never overlap; a smaller box shows none until zoomed; tab gets the focus outline |
| drawing | solid `focus.ring` stroke with handles (never dashed, so it can't read as proposed) |
| hover (pointer) | stroke and tab step to the selected outline at 50 %; no movement |
| hidden by filter | not drawn; the legend count still shows it |
| tiny (box under 6 screen px) | drawn at its true size plus a side tab, so it can always be found and selected |

**A11y:** the overlay is `aria-hidden`. Every box is also an item in the crop strip (J6) and the object order, and keyboard selection follows that order (J/K). Dragging always has a non-drag alternative: arrow keys move the selected box 1 px (Shift 10 px), Alt + arrows resize, and the inspector takes typed coordinates (WCAG 2.5.7). Forced colours: strokes and tabs fall back to `CanvasText`, selection to `Highlight`.

### J5. Image well and provenance legend

**Image well:** `bg.well` (neutral, so pixels read true), `radius.m`, the image fitted by default. A mono zoom note bottom-right on a `bg.canvas` chip ("1 screen px = 2.2 source px"). Zoom steps: Fit · 50 · 100 · 200 · 400 %; 0 = fit, 1 = 100 %, Z = zoom to the selected box; Space + drag or the scrollbars pan. Never smooth upscaled pixels above 100 % (`image-rendering: pixelated`), so reviewers see real pixels.

**Legend:** a chip row (A10) directly under the well: one pressed-toggle chip per tier with glyph, word and mono count ("Proposed 5"), then "Showing all 14 objects" or the filter result. It is both the legend and the filter. A polite status announces the filter result.

| State | Looks |
|---|---|
| loading | well at its final aspect ratio, `bg.well`, no shimmer; tabs and legend counts appear with the image |
| image failed | well keeps its size; centred in it, a `bg.canvas` panel (`radius.m`, `space.4` padding) holds the `text.primary` sentence "This image didn't load." + ghost "Try again" (text never sits on `bg.well`); decisions are blocked |
| no boxes | "No boxes on this image yet." in the legend row; the well shows the image only |

### J6. Crop strip and crop tile

A horizontal list of every object on the image as native-pixel crops, in decision order (proposed, uncertain, auto-accepted, reviewed; then by number). Tile 60 × 72 px crop on `bg.well`, `radius.s`, 2 px inset ring in the tier colour; caption: glyph + number, then the class (ellipsis allowed). Current tile: `focus.ring` outline. Scrolls inside itself. Each tile is a button whose name reads "Object 4 Soy milk, Proposed". **Larger crops** (inspector): "Native" at 1:1 when it fits, otherwise the largest scale that fits; never upscaled more than 4×; the scale is always printed ("Native · 1:1", "Context · 0.61×").

### J7. Reference views

Approved reference images for a class, side by side with the crop being judged. A group per class: class name (15 px semibold) · mono role ("Proposed class", "Current class", "Often confused with") · up to 3 thumbnails on `bg.well`, `radius.s`, each `role="img"` named with class and view ("Almond milk 1 L reference, angled"). Footer mono line: "Approved references only · evaluation images are never shown here". Empty: "No approved references for Almond milk yet." + link "Add references".

### J8. Proposed edit card

**Job:** one model proposal, its reason and its evidence, with equal-weight choices.

**Anatomy:** `bg.surface`, `radius.m`, a 3 px inset left edge in `label.proposed` · line 1: J3 glyph + who proposed ("LLM review proposes", "The detector proposes") + mono time · line 2: the change as a sentence, old value struck through *and* replaced in words ("Change class: ~~Soy milk 1 L~~ → Almond milk 1 L") · the model's reason as a blockquote, plain text only, with the mono caption "Model's reason · not checked by a person" · evidence links (references, crops) · actions.

**Actions:** ghost "Accept change" (A) · ghost "Keep {current}" (R) · link button "Mark uncertain" (U). **Accept and keep are the same weight on purpose:** a primary Accept would nudge people to rubber-stamp the model. Never ember here.

| Kind | Sentence |
|---|---|
| class | "Change class: ~~Soy milk 1 L~~ → Almond milk 1 L" |
| geometry | "Redraw the box: 9 px taller at the top" (+ "Show both boxes" toggle) |
| new object | "Add a missing Soy milk 1 L at the right edge (clipped)" |
| split / merge | "Split into 2 boxes" / "Merge objects 6 and 7" |
| delete | "Delete: looks like a reflection" (accepting needs a reason when the box was reviewed by a person) |
| duplicate | "Same object as 3" |

States: decided (the card collapses to a history line, J9); stale (the object changed since the proposal: warn notice "This proposal was made on revision 4; the box has changed since." and only Keep or Mark uncertain are offered); malformed (never shown; it is logged and counted).

**A11y:** a `section` labelled by its "… proposes" heading; the blockquote is text, never HTML or links from the model; shortcuts in `aria-keyshortcuts`.

### J9. History list

`<ol>` of events for an object, image or release, newest last. Each line: J3 glyph of the tier the event produced (none for neutral events) · one sentence naming the actor (a person by name; a model by role plus a mono version: "Detector yolo26n · r0007 proposed Soy milk 1 L, score 0.62") · mono time. Hairlines between lines. Always ends with the current truth when it matters: "No person has reviewed this object."

### J10. Class picker

A combobox in a popover anchored beside the box (or under the Class field). Title "Class for the new box" / "Change class" · search field (matches names and aliases) · listbox options: 36 px reference thumbnail · class name · mono sub-line (reference match score, matched alias) · check on the active option. Opens ranked by reference match, never alphabetically by default. Footer: primary sm "Add {class}" + ghost "Cancel" · link button "None of these fits · mark uncertain". Reviewers can't create classes here; an admin edits the class catalog. **One ember while open:** Add is the only ember action while the picker is open. The decision dock (J12) demotes its primary to a ghost button for that time (same label, same shortcut held back until the picker closes) and restores it on close or cancel. **A11y:** ARIA combobox + listbox pattern; Enter commits, Esc cancels and returns focus to the box.

### J11. Tool picker and shortcuts

Tool picker: A9 segmented group of pressed buttons (Select V · Box B · Split S · Merge M), icon + word; the key shows in the tooltip and the shortcut sheet, and in `aria-keyshortcuts`. **Kbd hint:** mono 12 px key cap, hairline-strong outline, `radius.s`, inside buttons whose shortcut matters at that moment ("Accept change A"). **Shortcut sheet:** opened by ? or the keyboard icon button; a `bg.surface` panel listing every key in a `<dl>`. Single-key shortcuts are off while a text field has focus, and can be turned off in account settings (WCAG 2.1.4).

### J12. Decision dock

The bottom of the review inspector: `elevation.sheet`, `hairline-strong` top edge, sticky inside the rail (at ≤ 820 px it becomes a B4-style bar fixed to the bottom of the screen). Mono line: scope + progress ("This image · 5 of 14 to decide") · the image's one primary action ("Accept image", Enter) + ghost "Skip" · reason line when the primary is unavailable · link button for the rare exit ("Exclude this image…"). Unavailable primary = quiet pill with `aria-disabled` and the reason in a described-by line ("Decide the 5 proposals first. Uncertain is a decision."). While the class picker (J10) is open, the primary shows as a ghost button and Enter belongs to the picker; it returns to primary when the picker closes.

### J13. Workbench table

I1 at workbench density, for any list of records: `size.row.admin` rows, `<table>` with a hidden caption, mono uppercase headers on `bg.surface`, tabular numbers right-aligned, UTC-labelled time columns, named row actions ("Roll back 640 to m-0005"), state as tags. Wide tables scroll inside a focusable region named for the table. Selected or "in use" row: `bg.raised`.

### J14. Comparison bars

Current against candidate, per row: label (140 px) · two 8 px bars on one track (current `text.tertiary` above, candidate `text.primary` below; width = value) · the candidate value in mono with ▲ / ▼. Legend above names both models by id. Unsupported rows show the reason in words instead of bars. The bars are `aria-hidden`; a visually hidden sentence list carries the data, and "Show as a table" opens the full numbers. Never ember or state colours for a series.

### J15. Distribution bar

A 10 px stacked bar for parts of a whole (splits, sources): `text.secondary` · `text.tertiary` · `line.control`, keyed above the table; row bars scale to the largest row. Always sits in a table row that holds the numbers. For provenance mixes, the segments use the `label.*` roles and the key names the tiers in words.

### J16. Delta

A change against a baseline: up / down icon + signed number, coloured by whether it is good (`state.ok`) or bad (`state.danger`), not by sign; "–" for none. A hidden word says the direction ("up", "down"). Same rule as E3 movement.

### J17. Budget meter

A15 with thresholds: fill `text.secondary` to 80 %, `state.warn` from 80 %, `state.danger` at 100 % or when paid steps are paused. Always paired with "$3.10 of $40" in mono. Estimates carry the warn tag "Estimate"; actuals never do.

### J18. Secret fingerprint

How a stored key is shown, ever: lock icon · mono "ul_••••9f3c" (prefix + last 4) · "sha256 1a2b3c4d" (first 8 of the hash) · who added it and when. `bg.canvas`, `radius.m`, hairline-strong outline. There is no reveal, no copy and no "show key" control. Replacing a key is a write-only field that clears on save and reports the new fingerprint.

### J19. Job status tag

A4 tags for long work (runs, compiles, syncs, evaluations): neutral "Queued", neutral "Running" (with "41 of 100 epochs" beside it and an A15 bar; never a spinner or pulse), `ok` "Completed", `danger` "Failed" (reason in words beside it), neutral "Cancelled", `warn` "Paused" (with why: "out of credits"). Status changes are announced politely once.

### J20. Spend confirm panel

Paid work never starts from one click. The start button opens this panel in place (no modal): `bg.raised`, `line.control` outline, `radius.m`. A facts list of exactly what will run (release, recipe, base model with hash, hardware, epochs, images) · an estimate tile with the `warn` "Estimate" tag, the price basis and the billing rule ("Billed for time used, even if cancelled; at least 15 minutes") · the month's total after this run against the limits · a ceiling line that says what is enforced and what is not ("The run won't start if its estimate is over $5 per operation, or if this month's spend, plus estimates reserved for queued work, plus this estimate would pass the $40 limit. Time-based billing can come in above the estimate; the actual cost shows as unconfirmed until the provider's bill is matched to this run.") · primary "Start · estimate $1.38" (the button names the estimate, never a cap) + ghost "Cancel". Never word the amount as a maximum ("up to", "at most", "capped at") unless the server enforces that exact amount for this run. Over a limit: no primary; a danger notice says which limit and links to Costs.

### J21. Setup steps

H2 join steps as a side nav for multi-step setup: `<ol>` in a card, ember mono number, title, one-line status, a tag ("Done" ok, "Now" neutral). Current step `aria-current="step"`. Steps can be revisited; a later step never unlocks by skipping an approval.

### J22. Recipe preview frame

An output square at a fixed display scale shared by every frame in the view (so a 320 frame looks half the size of a 640 one): `bg.well`, padding drawn as a 135° hatch of `bg.overlay` / `bg.raised`, the derived image, J4 boxes without tabs, and flagged targets as a 3 px `label.rejected` box. Caption: recipe name · output size · picture size · padding · scale, then flag tags with a sentence ("2 below 4 px · Both price tags become 7 × 3 px. This image is excluded from the 320 dataset unless you recrop."). `role="img"` with the geometry in words.
