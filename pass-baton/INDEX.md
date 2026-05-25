# Pass-Baton Index

Latest entry per scope. Read the linked file for full context. **New sessions should start here.**

| Scope | Latest | Date | Topic |
|---|---|---|---|
| phase-3-domain | [001](./phase-3-domain/001-2026-05-15-phase-3-pr11-ready.md) | 2026-05-15 | Phase 3 code-complete; PR #11 open, awaiting review |
| phase-4-domain-funcs | [003](./phase-4-domain-funcs/003-2026-05-23-phase-4-review.md) | 2026-05-23 | Independent review — VERDICT CLEAN (gate green, mutation-verified); 2 MEDIUM + 2 LOW non-blocking findings; digest written |
| phase-5-orm | [003](./phase-5-orm/003-2026-05-24-fk-decision-closed.md) | 2026-05-24 | FK enforcement decision closed — PRAGMA foreign_keys=ON chosen (ADR-0002); PR #36 open |
| phase-6-repos | [002](./phase-6-repos/002-2026-05-24-6a-fk-migration-review.md) | 2026-05-24 | Sub-unit 6a independent review — VERDICT CLEAN (gate green, all 4 ACs RED-able); 1 MEDIUM (0002 upgrade redundant vs ADR narrative) + 2 LOW, none blocking |

*Top-level seed: [`000-2026-05-15-start-pass-baton.md`](./000-2026-05-15-start-pass-baton.md) (empty placeholder; establishes the sequence head).*

---

## How to read this

- Each row points at the **highest-numbered** pass-baton in that scope's subdirectory.
- Older pass-batons in the same scope are reachable by following the `## References` section of the latest one (pass-batons link backward as they evolve).
- The index is overwritten by the `pass-baton` skill on every write. Do not hand-edit.
