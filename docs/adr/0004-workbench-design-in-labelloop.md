# ADR 0004: Keep the workbench design in labelloop

- Status: Accepted
- Date: 2026-11-14

## Context

The plan said the web UI is built from `TogetherWeOwn/two-design-kit` tokens and
components. The workbench parts of that kit (components J1–J22, layout patterns P6
and P7, the tool-surface voice rules, the box-handle target exception and the
workbench tokens) were written for labelloop. The owner decided on 2026-11-14 that
two-design-kit is the kit for the TWO product only, and that every other product
keeps its design in its own repository under `docs/design/`.

## Decision

Move the workbench design, unchanged, from kit tag v0.3.1 (`4eb4181`) into
`docs/design/`: `components.md`, `templates.md`, `voice.md`, `accessibility.md` and
`tokens/README.md`. Each file names the kit tag and file it came from. These copies
are now the source of truth for labelloop; changes are made here, not in the kit.

The vendored `docs/design/mockups/src/tokens.css` stays where it is (the web app
imports it) and is recorded as byte-identical to kit v0.3.1 `dist/css/tokens.css`.
Base Ember Hours components (ids A–I) and base accessibility and voice mechanics
are cited at kit v0.3.1; nothing links to the kit at runtime.

## Consequences

The kit can delete its workbench content without breaking labelloop. A later kit
release does not change labelloop until someone copies a value here on purpose,
with its new citation.
