# Handoff Notes

This directory holds session handoff notes. Each handoff captures the state of work-in-progress so the next Claude Code session can resume without re-deriving context.

The current sequence head per scope is recorded in [`INDEX.md`](./INDEX.md). The next session should read that file first.

## Hard Rule: Sequential Numbering

**Every new handoff file MUST use the next integer in the sequence, zero-padded to three digits. No skips, no reuse, no out-of-order numbers.**

Filename format: `NNN-YYYY-MM-DD-<kebab-case-slug>.md`

- `NNN` — the next integer after the highest existing number in the same directory, zero-padded to three digits (`000`, `001`, …, `099`, `100`, `101`, …). Three-digit padding from the start keeps lexicographic sort agreeing with chronological order past the `99→100` boundary.
- `YYYY-MM-DD` — the date the handoff is written. Always today's actual date (not "today", a weekday name, or a relative phrase).
- `<kebab-case-slug>` — short topic identifier (2–5 words).

Before creating a new handoff, list the target directory, find the highest `NNN`, and use `NNN + 1`. Use **max + 1**, not **count + 1** — deleted entries leave gaps that must stay as gaps (the sequence is an append-only audit trail).

Numbering is **per-directory** — each subdirectory has its own sequence starting at `000`.

## Hard Rule: Subdirectories for Features and Epics

**Every handoff that pertains to a feature or epic MUST live in a feature/epic-named subdirectory of `handoff/`.** The top level of `handoff/` is reserved for cross-cutting items (this `CLAUDE.md`, `INDEX.md`, seed entries). Feature-scoped handoffs at the top level are forbidden.

Subdirectory naming: kebab-case identifier matching the feature/epic, e.g. `handoff/phase-2-config/`, `handoff/phase-3-domain/`, `handoff/auth-refactor/`. **Before creating a new subdirectory, the writer must list existing ones and prefer an existing match** to prevent drift like `phase-3-domain` vs `phase3-domain` vs `domain-phase-3`.

## Hard Rule: Only the `handoff` Skill Writes Here

**No tool, agent, or human edits may write directly to `handoff/` or any subdirectory of it.** All additions go through the project-scoped `handoff` skill so the invariants above (sequential numbering, feature-scoped subdirs, append-only, INDEX kept current) are enforced uniformly.

- Editing `handoff/CLAUDE.md` (this file) is permitted — rules can evolve through normal PR review.
- Editing existing handoff entries is **not** permitted. Handoffs are an append-only log; superseding information goes in a new, higher-numbered entry.
- `INDEX.md` is maintained by the skill on every write; do not hand-edit.
