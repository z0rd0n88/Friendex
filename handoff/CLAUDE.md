# Handoff Notes

This directory holds session handoff notes. Each handoff captures the state of work-in-progress so the next Claude Code session can resume without re-deriving context.

## Hard Rule: Sequential Numbering

**Every new handoff file MUST use the next integer in the sequence, zero-padded to two digits. No skips, no reuse, no out-of-order numbers.**

Filename format: `NN-YYYY-MM-DD-<kebab-case-slug>.md`

- `NN` — the next integer after the highest existing number in this directory.
- `YYYY-MM-DD` — the date the handoff is written.
- `<kebab-case-slug>` — short topic identifier.

Before creating a new handoff, list this directory, find the highest `NN`, and use `NN + 1`.
