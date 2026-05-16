# Handoff Notes

This directory holds session handoff notes. Each handoff captures the state of work-in-progress so the next Claude Code session can resume without re-deriving context.

## Hard Rule: Sequential Numbering

**Every new handoff file MUST use the next integer in the sequence, zero-padded to two digits. No skips, no reuse, no out-of-order numbers.**

Filename format: `NN-YYYY-MM-DD-<kebab-case-slug>.md`

- `NN` — the next integer after the highest existing number in this directory.
- `YYYY-MM-DD` — the date the handoff is written.
- `<kebab-case-slug>` — short topic identifier.

Before creating a new handoff, list the target directory, find the highest `NN`, and use `NN + 1`. Numbering is **per-directory** — each subdirectory has its own sequence starting at `00`.

## Hard Rule: Subdirectories for Features and Epics

**Every handoff that pertains to a feature or epic MUST live in a feature/epic-named subdirectory of `handoff/`.** The top level of `handoff/` is reserved for cross-cutting items (this `CLAUDE.md`, optional index files, seed entries). Feature-scoped handoffs at the top level are forbidden.

Subdirectory naming: kebab-case identifier matching the feature/epic, e.g. `handoff/phase-2-config/`, `handoff/phase-3-domain/`, `handoff/auth-refactor/`.

## Hard Rule: Only the `handoff` Skill Writes Here

**No tool, agent, or human edits may write directly to `handoff/` or any subdirectory of it.** All additions go through the project-scoped `handoff` skill so the invariants above (sequential numbering, feature-scoped subdirs, append-only) are enforced uniformly.

- Editing `handoff/CLAUDE.md` (this file) is permitted — rules can evolve through normal PR review.
- Editing existing handoff entries is **not** permitted. Handoffs are an append-only log; superseding information goes in a new, higher-numbered entry.
