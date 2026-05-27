---
name: baton-pass
description: Write session baton-pass notes to baton-pass/ in this repository. Triggers - "pass the baton", "write a baton-pass", "save state for next session", "baton-pass for <feature>", or before ending a working block whose context the next session needs to inherit.
---

# baton-pass

Sole authorized writer to `baton-pass/`. Enforces the directory's three hard
rules (see `baton-pass/CLAUDE.md`): three-digit sequential numbering per
directory, feature/epic subdirectories, append-only.

## Workflow

### 1. Determine scope

- **Feature/epic baton-pass** → goes in `baton-pass/<slug>/`.
  - Derive the slug from the branch name or phase identifier (e.g.,
    `feat/phase-3-domain-models` → `phase-3-domain`).
  - **List existing subdirectories first:** `ls baton-pass/ | grep -v '\.md$'`.
    If a close match already exists (case- or hyphen-insensitive), USE IT.
    Inventing `phase3-domain` when `phase-3-domain` already exists is the
    most common drift failure.
  - Only create a new subdirectory when no existing one matches. Confirm
    the name with the user if it's not derivable from a branch/phase.
- **Cross-cutting baton-pass** → goes at the top level of `baton-pass/`. Rare;
  reserved for seed entries, retrospectives spanning multiple features,
  and the like.

### 2. Pick a template

Three variants — choose by the *kind* of baton-pass this is, not the project
phase. See [Templates](#templates) below.

| Variant | Use when |
|---|---|
| **phase-closure** | A clean unit of work just finished; next session picks up the next phase. Most common. |
| **mid-investigation** | Paused mid-debug; the next session needs your hypothesis tree, what was ruled out, what to try next. |
| **blocked-on-external** | Waiting on CI / review / external answer / a human decision. Short; mostly "what we're waiting for and why." |

### 3. Compute the next number

```bash
ls baton-pass/<scope>/ 2>/dev/null \
  | grep -oE '^[0-9]+' \
  | sort -n | tail -1
```

- No output → `NNN = 000`.
- Otherwise → `NNN = printf '%03d' $((max + 1))`.
- **Always `max + 1`, never `count + 1`.** Deleted entries leave gaps that
  stay as gaps.
- Three-digit minimum; widens naturally past `999` (extremely unlikely).

### 4. Get today's date

```bash
date +%F
```

Always run this — never rely on the session's recollection of the date or
on the model's pretrained "current date." Long sessions go stale.

### 5. Compose the filename

`NNN-YYYY-MM-DD-<slug>.md` where `<slug>` is a 2–5 word kebab-case topic
identifier. The file slug describes *what this baton-pass is about*; the
subdirectory slug is the *scope* — they should not match each other.

### 6. Gather state

Before composing content, collect from the live repo (don't recall from
memory — verify):

- Current branch: `git branch --show-current`
- Worktree path: `git rev-parse --show-toplevel`
- HEAD: `git log -1 --format='%h %s'`
- Open PRs in scope: `gh pr list --state open --search '<scope>'`
- Issues referenced: `gh issue view <n>` for each
- Verification results if any gate ran this session — paste actual output,
  not a description of what should have happened.

### 7. Write the file

Use the matching template from [Templates](#templates). Keep baton-passs under
~150 lines — they are quick context, not full docs.

### 8. Update `baton-pass/INDEX.md`

After writing the baton-pass file, refresh `baton-pass/INDEX.md` so it points at
the latest entry per scope. The index is a quick map for the next session:
each scope has one row pointing at its highest-numbered baton-pass.

If `INDEX.md` doesn't exist, create it.

### 9. Completeness check

Before reporting "done," verify the baton-pass has:

- [ ] HEAD short SHA + subject (from `git log -1 --format='%h %s'`)
- [ ] Branch name (current, even if not yet pushed)
- [ ] At least one link: PR, issue, or doc path
- [ ] At least one concrete next step with a file path or command
- [ ] Absolute date in `YYYY-MM-DD` form — no relative phrases
- [ ] Filename uses `NNN` (three digits), not `NN`
- [ ] `INDEX.md` updated

A baton-pass missing the SHA or PR link is half-useful. The check costs ten
seconds.

### 10. Stop — do not commit by default

By default the skill **writes only** — it does not stage, commit, or push.
The user controls when baton-pass entries land.

**Exception:** if the user explicitly says "commit it", "include in the
commit", "save and push", or similar, commit (and push if requested) the
new baton-pass file + the `INDEX.md` update together as a single commit with
a message like `docs(baton-pass): <scope> — <one-line topic>`.

Never edit existing baton-pass entries — they are append-only.

## Templates

### phase-closure

```markdown
# Pass-Baton: <one-line topic>

**Date:** YYYY-MM-DD
**Scope:** <scope>
**Branch:** <branch-name>
**Worktree:** <path>
**HEAD:** <short-sha> <commit subject>

## Where things stand

<One paragraph. What was accomplished, what is currently waiting, and on
whom or what. End with the literal current blocking state.>

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

### mid-investigation

```markdown
# Pass-Baton: <one-line bug or behavior under investigation>

**Date:** YYYY-MM-DD
**Scope:** <scope>
**Branch:** <branch-name>
**Worktree:** <path>
**HEAD:** <short-sha> <commit subject>

## Symptom

<What is failing or behaving unexpectedly. Paste the actual error / output.>

## Hypothesis tree

- [ ] **H1:** <hypothesis> — <status: ruled out / partially confirmed / untested>
  - Evidence: <what we observed>
- [ ] **H2:** ...
- [ ] **H3:** ...

## What's been ruled out

- <Things that were tried and did not change the symptom.>

## Best next probe

1. <The single most informative experiment to run next, and why.>
2. <Fallback if the first probe is inconclusive.>

## References

- PRs: #<n>
- Failing test / repro: `<path>` or `<command>`
- Related code: `src/<path>:<line>`
```

### blocked-on-external

```markdown
# Pass-Baton: Waiting on <what>

**Date:** YYYY-MM-DD
**Scope:** <scope>
**Branch:** <branch-name>
**Worktree:** <path>
**HEAD:** <short-sha> <commit subject>

## Blocking signal

<What we're waiting for: a CI run, a review, an external answer, a
decision. Include the URL / PR number / person if applicable.>

## What's done and ready

<One paragraph: the work that's complete and parked, so the next session
can verify nothing has rotted before unblocking.>

## What to do when unblocked

1. <First action once the block clears.>
2. <...>

## Fallback if the block doesn't clear

- <Acceptable degraded path, if any.>

## References

- PRs: #<n>
- Issues: #<n>
- External: <URL / channel / person>
```

## Index format

`baton-pass/INDEX.md` structure — overwritten on every skill invocation:

```markdown
# Pass-Baton Index

Latest entry per scope. Read the linked file for full context. New sessions
should start here.

| Scope | Latest | Date | Topic |
|---|---|---|---|
| phase-2-config | [001](./phase-2-config/001-2026-05-15-pr7-ready.md) | 2026-05-15 | PR #7 ready for review |
| phase-3-domain | [000](./phase-3-domain/000-2026-05-15-phase-3-kickoff.md) | 2026-05-15 | Phase 3 kickoff |

*(top-level seed: [000-2026-05-15-start-baton-pass.md](./000-2026-05-15-start-baton-pass.md))*
```

One row per scope, pointing at the *highest-numbered* file in that
subdirectory. Older baton-passs in the same scope are not listed in the index
(they remain on disk and are reachable by reading the latest one and
following its `## References` upward).

## Gotchas

- **Scope ≠ branch.** Subdirectory slug is the *feature/epic* identifier,
  which may outlive a single branch. `phase-3-domain` covers both
  `feat/phase-3-domain-models` and any later `fix/phase-3-followup`.
- **No skill self-reference in baton-pass content.** Pass-Batons are for the
  next *engineering* session — never mention this skill, the `baton-pass/`
  directory rules, or meta-architecture. Those belong in
  `baton-pass/CLAUDE.md`.
- **Numbering is per-directory.** `baton-pass/phase-2-config/000-...` and
  `baton-pass/phase-3-domain/000-...` both validly start at `000`.
- **`max + 1`, not `count + 1`.** Deleted entries leave gaps that stay
  as gaps. The sequence is an append-only audit trail; the gap itself is
  information ("an entry used to live here").

## Future enhancement (not implemented)

A `SessionStart` hook in `.claude/settings.json` could automatically print
the latest baton-pass per scope into the next session's context, removing
the need for the user to point at `baton-pass/INDEX.md` manually. Held off
because `settings.json` has a known clobber issue when `claude.exe` is
running (see global `CLAUDE.md` → "Settings.json edits are clobbered").
Revisit once that's resolved.
