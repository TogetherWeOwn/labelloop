# ADR 0001: Record architecture decisions

- Status: accepted
- Date: 2026-11-07

## Context

The build plan (`docs/plan.md`, v0.3) pins most contracts. Some choices are deliberately left to implementation: the queue library, the embedding model for reference retrieval and near-duplicate detection, and the web state/data libraries (plan §18).

## Decision

Record each such choice, and every departure from the plan, as a numbered ADR in `docs/adr/` in the PR that introduces it. An ADR states the context, the options considered, the decision and its consequences.

## Consequences

The plan stays the single reference. Departures from it are explicit and reviewable.
