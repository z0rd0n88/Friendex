# Architecture Decision Records

This directory records **significant architectural decisions** for Friendex — one file
per decision, numbered sequentially (`NNNN-kebab-title.md`).

An ADR captures the *context* and *reasoning* behind a decision so future contributors
understand **why** the codebase is shaped the way it is, not just what it does. When a
decision is reversed, the new ADR's frontmatter lists what it **Supersedes**, and the old
decision is annotated with a pointer to the superseding ADR rather than deleted — the
audit trail is the point.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](./0001-per-guild-markets.md) | Per-guild market isolation (multi-tenancy) | Accepted |
| [0002](./0002-sqlite-fk-enforcement.md) | SQLite foreign-key enforcement via PRAGMA | Accepted |

Decisions made before this directory existed live inline in
[`docs/02-target-architecture.md`](../02-target-architecture.md) as the "Open Questions
& Resolutions" table; ADRs supersede individual rows there as they are revisited.
