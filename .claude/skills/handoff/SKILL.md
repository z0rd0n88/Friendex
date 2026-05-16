---
name: handoff
description: Sole authorized writer to the project's handoff/ directory — creates a new session handoff note under the correct feature/epic subdirectory with the next sequential number, or initializes the handoff architecture on first use.
---

# handoff

This skill owns every write to `handoff/`. It enforces three invariants the
directory's `CLAUDE.md` codifies as hard rules:

1. **Sequential numbering** — filename prefix `NN` is `max(existing NN) + 1`,
   zero-padded to at least two digits, per-directory.
2. **Feature/epic subdirectories** — handoffs about a feature or epic live in
   `handoff/<kebab-case-name>/`, never at the top level.
3. **Append-only** — existing handoff files are never edited or renumbered.

## When to invoke

Trigger phrases: "hand off this session", "write a handoff", "create handoff
notes", "handoff for phase N", "save state for the next session".

Also invoke proactively when the user signals the end of a working block whose
state the next Claude Code session will need (open PRs in flight, partial
implementations, blocked work waiting on an external signal).

## Workflow

1. **Identify scope.** Ask one short question if unclear: is this a
   feature/epic handoff (→ subdirectory) or a cross-cutting one (→ top level)?
   For feature handoffs, derive the subdir slug from the branch name or phase
   identifier (e.g., `feat/phase-3-domain-models` → `phase-3-domain`).

2. **Resolve target directory.**
   - Feature/epic: `handoff/<slug>/`. `mkdir -p` if it does not exist.
   - Cross-cutting: `handoff/` top level (rare — most handoffs are scoped).

3. **Compute the next number.**
   ```bash
   ls handoff/<slug>/ 2>/dev/null | grep -oE '^[0-9]+' | sort -n | tail -1
   ```
   - If no output → `NN = 00`.
   - Otherwise → `NN = printf '%02d' $((max + 1))`.
   - Two-digit minimum; widen to three only if `NN ≥ 100` (unlikely).
   - Never reuse a deleted number. Always `max + 1`, not `count + 1`.

4. **Compose filename.** `NN-YYYY-MM-DD-<slug>.md` where:
   - `YYYY-MM-DD` is today's date.
   - `<slug>` is a 2–5 word kebab-case topic identifier — *not* the same as
     the subdirectory slug. Subdir = scope; file slug = what this handoff is
     about within that scope.

5. **Gather state.** Before writing, collect:
   - Current branch and worktree path.
   - HEAD commit SHA + subject.
   - Open PRs / issues relevant to the scope (`gh pr list`, `gh issue view`).
   - Verification command results if any gate ran this session.

6. **Write the file** using the template below. Keep it under ~150 lines —
   handoffs are quick context, not full documentation.

7. **Stop.** Do not stage, commit, or push. The user controls when handoff
   entries land. Do not edit any existing handoff file.

## Template

```markdown
# Handoff: <one-line topic>

**Date:** YYYY-MM-DD
**Scope:** <feature or epic name>
**Branch:** <branch-name>
**Worktree:** <path>
**HEAD:** <short-sha> <commit subject>

## Where things stand

<One paragraph. What was accomplished, what is currently waiting, and on
whom or what. End with the literal current blocking state, e.g. "PR #7 is
ready for review; CI green on Python 3.11 + 3.12.">

## Next steps

1. <Concrete next action with file paths and/or commands.>
2. <...>

## Open questions / risks

- <Items the next session must resolve before proceeding, if any.>

## References

- PRs: #<n>
- Issues: #<n>
- Docs: `docs/<file>.md` §<section>
- Code: `src/<path>:<line>`
```

## Gotchas

- **Date stays absolute.** Use today's actual `YYYY-MM-DD`. Never write
  "today", "yesterday", or a weekday name — handoff entries are read out of
  context, possibly weeks later.
- **Scope ≠ branch.** The subdir slug is the *feature/epic* identifier, which
  may outlive a single branch. Phase 3 work might span `feat/phase-3-domain-models`
  → `fix/phase-3-followup`; both belong in `handoff/phase-3-domain/`.
- **No skill self-reference.** Handoff content is for the next *engineering*
  session — it should not mention this skill, the handoff directory's rules,
  or meta-architecture concerns. Those live in `handoff/CLAUDE.md`.
- **Numbering is per-directory.** `handoff/phase-2-config/00-...` and
  `handoff/phase-3-domain/00-...` are both valid sequence heads; the `00` is
  not globally unique.
