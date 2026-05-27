# baton-runner — Reference

Operational detail for [SKILL.md](SKILL.md). The manager follows this; each unit
receives only the slice it needs (its prompt template). The manager never opens
diffs, batons, or gate logs — it threads *paths*.

## Worktree & branches

**One worktree for the whole run** (the workflow is strictly sequential, so a
single worktree has no contention). Pick a short `<run-id>` (e.g.
`br-<date>-<slug>`):

```bash
git -C <repo-root> fetch origin --prune
git -C <repo-root> worktree add <repo-root>/.claude/worktrees/<run-id> \
  -b feat/<run-id>/phase-1 origin/main
```

All units operate inside that worktree. Phases share one linear, accumulating
history — phase N's agent sees phase N-1's code because the files are physically
there. "Stacked branches" are just labels on that history:

| Phase | Branch (created in the SAME worktree) | PR base |
|---|---|---|
| 1 | `feat/<run-id>/phase-1` | `main` |
| N | `feat/<run-id>/phase-N` (`git switch -c` from phase-(N-1) tip) | `feat/<run-id>/phase-(N-1)` |

**The manager opens ready-for-review PRs and merges nothing.** Push the phase
branch, then `gh pr create --base <prev-branch> --head feat/<run-id>/phase-N
--body-file <tmp>` (no `--draft` — open as ready, matching the harness default).
The body states: "machine-gated (`scripts/gate.sh`) + agent-reviewed; **not yet
human-reviewed**", plus `Refs #<issue>` when the phase maps to one. The user
merges the stack **in order** afterward. A `gh` failure is
a fatal pause. Note in the final summary: post-run edits to an early PR require
rebasing the downstream stack.

Write all commit messages and PR bodies to a temp file and use `-F` /
`--body-file` — WSL arg-mangling silently drops text after a `:` otherwise.

## Run log & state

Directory `baton-runner/<run-id>/` (committed as the run's audit trail).

`STATE.md` — single source of truth for resume; keep current after every
transition:

```markdown
# baton-runner run <run-id>
status: RUNNING | PAUSED | DONE | FAILED
worktree: <abs path>
phase: <N of TOTAL>  unit: WORK|REVIEW|FIX  review_iter: <k of 3>
current_baton: <path>          # last baton produced
units_used: <n>                # against global ceiling
pause_reason: <text or ->
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }
phases:
  - id: phase-1  spec: <issue#|file|inline>  readiness: READY|THIN|BLOCKED
    unit_agent: python-pro        # agent for ALL units (work/review/fix); project default
    branch: ...  pr: <url|->  digest: baton-runner/<run-id>/digest-phase-1.md
    units: <n>  state: DONE
  - id: phase-2  spec: ...  state: RUNNING ...
```

`log.md` — append-only, one UTC-stamped line per action (spawn, return status,
baton path, VERDICT, commit SHA, PR URL, budget counter, pause/resume).
Progress line format: `phase 3/6 · FIX iter2 · VERDICT ISSUES · units 17/75`.

## The objective gate — `scripts/gate.sh`

A committed, deterministic runner so verification is fixed and reproducible, not
improvised per spawn. The **review unit** runs it first; the manager never does.

```
scripts/gate.sh baton-runner/<run-id>/gate-phase-<N>-iter-<k>/
```

It runs `uv run pytest`, `ruff check .`, `ruff format --check .`,
`mypy src/friendex`, tees each to the log dir, prints `GATE: PASS|FAIL`, and
exits non-zero if any check fails. A green self-report with a red gate exit means
the unit is not done.

## Unit prompt templates

Every spawn: `model: opus` (forced — overrides the agent's own default). **All
units (work, review, fix) spawn as the phase's unit-agent, `python-pro` by
default** — it carries the `Skill` tool, so it can run the `tdd`, `baton-pass`,
`code-review`, and `ecc-security-review` skills every contract needs. Fill the
`<...>`.

### Containment contract (append to EVERY unit prompt)

```
CONTAINMENT — you may read the repo but WRITE only inside this worktree: <path>.
Never touch ~/.claude, other worktrees, the main checkout, or system paths.
Do NOT run any mutating git command, push, or gh — the manager owns all git and
remote actions. You may run read-only git (diff/log/status) and tests/lint.
If you need a new dependency, DECLARE it in your baton; never add it silently.
If you need anything outside this contract, return STATUS: NEEDS_USER and stop.
```

### Return contract (append to EVERY unit prompt)

```
RETURN CONTRACT — your final message MUST be exactly these lines:
  STATUS: COMPLETE | INCOMPLETE | NEEDS_USER | FATAL
  BATON: <path to the baton-pass file you wrote/updated>
  NOTES: <=3 lines: done / remaining / blocking question
Write/update the baton via the baton-pass skill BEFORE returning. Update it
incrementally — after each acceptance criterion and at least every ~10 tool
calls — so it is always a current resumable checkpoint. If you exceed ~50 tool
calls OR ~10 files touched (checked only at a stable point, never mid-edit), OR
cannot finish well, STOP and return INCOMPLETE with precise remaining-work
notes. Never sacrifice correctness to "finish".
```

### Work unit

```
You implement ONE work-unit. Worktree: <path>.
Scope (your ONLY scope): <inline spec | `gh issue view <n>` | file path>.
Acceptance criteria (the bar you are held to): <criteria list>.
Continuity (what already exists — honor it, don't duplicate or contradict):
  <accumulated phase-exit digest paths, or "none — phase 1">.
[If continuation:] Resume from this baton (your full context): <baton path>.

Use the `tdd` skill. For EACH acceptance criterion: write the test first, run it,
and RECORD THE ACTUAL FAILING (RED) OUTPUT in your baton, then implement to green.
Honor the repo CLAUDE.md (e.g. invoke ecc-python-patterns for Python). Implement
exactly the criteria — no scope creep.
<CONTAINMENT CONTRACT>
<RETURN CONTRACT>
```

### Review unit (independent instance — fresh context, not the work unit)

```
You review the work just done. Worktree: <path>. Do NOT change product code.
Work baton (what was claimed + the RED evidence): <baton path>.
Acceptance criteria / intent to verify against: <criteria / spec ref>.

1. Run: scripts/gate.sh baton-runner/<run-id>/gate-phase-<N>-iter-<k>/
   If it exits non-zero, VERDICT is ISSUES — record the failures as findings;
   no deep review needed on broken code.
2. If green: run the `code-review` skill and the `ecc-security-review` skill over
   the diff (`git diff <base>...HEAD`).
3. Verify each acceptance criterion is genuinely met AND its tests are real
   (would fail if the implementation were reverted/mutated — not tautological).
   Flag any criterion lacking RED evidence in the baton.
4. Flag every newly added dependency for the user's visibility.

Write a review baton via baton-pass: findings by severity (CRITICAL/HIGH/MEDIUM/
LOW) with file:line + a concrete fix each. Set VERDICT CLEAN only if the gate is
green, there are no CRITICAL/HIGH findings, and intent is met.
ON CLEAN, also write the phase-exit digest to
  baton-runner/<run-id>/digest-phase-<N>.md
(<=40 lines: public surface added — modules + key signatures — and decisions/
conventions the next phase must honor).
RETURN: STATUS line, then `VERDICT: CLEAN | ISSUES`, then BATON + NOTES.
<CONTAINMENT CONTRACT (no product-code edits)>
```

### Fix unit

```
You implement the findings from a review. Worktree: <path>.
Review baton (your scope — fix exactly these, nothing more): <review baton path>.
Use the `tdd` skill: add/adjust tests proving each finding is resolved (capture
RED first), then fix. Write a baton summarizing what was fixed and anything
deliberately deferred + why.
<CONTAINMENT CONTRACT>
<RETURN CONTRACT>
```

## Spec readiness (pre-flight)

A spec is buildable only with (1) a clear outcome, (2) **testable acceptance
criteria**, (3) explicit scope/non-goals. Classify each phase:

- **READY** → proceed.
- **THIN** (goal clear, criteria missing/weak) → manager drafts proposed
  acceptance criteria and gets the user to confirm them at signoff. Never invent
  silently.
- **BLOCKED** (fundamental ambiguity/contradiction) → raise as an open question;
  the phase cannot start until resolved.

## Unit agent

This project runs **every** unit — work, review, and fix — as the `python-pro`
agent. python-pro carries `Read/Write/Edit/Bash/Glob/Grep` **and `Skill`**, so it
can edit files, run Bash, and invoke the `tdd`, `baton-pass`, `code-review`, and
`ecc-security-review` skills every contract requires. (The `Skill` tool was added
to this project's `python-pro` specifically so it can serve as a baton-runner
unit — without it the agent could not run `/tdd` or `/baton-pass`.)

- **Independence is preserved by instances, not types.** Each unit is a fresh
  spawn with its own context, so the review unit is still an independent reviewer
  even though it shares the `python-pro` agent type with the work unit — it only
  ever sees the work baton + the diff, never the implementer's reasoning.
- **Override.** The user may still nominate a different agent per phase at
  signoff. Any alternative must support file edits + Bash **and the `Skill`
  tool**; if it lacks `Skill`, fall back to `python-pro` (or `general-purpose`)
  or get explicit confirmation for an adapted contract (inline TDD + manual baton
  write).
- **Model.** Spawns still force `model: opus` regardless of the agent's default.
- Record the choice as `unit_agent` on the phase in `STATE.md`.

## Heuristic context split & resilience

The manager can't meter a subagent's context, so resilience comes from
right-sizing (pre-flight) + incremental baton checkpoints + a *countable* bail
budget (≈50 tool-calls / ≈10 files), not from the agent predicting its ceiling.
On `INCOMPLETE`: spawn a continuation seeded only with that baton path (fresh
context), same template, until `COMPLETE`. Three `INCOMPLETE`s with no progress
(baton notes unchanged) = a fatal error.

## Commits (manager only)

One commit per completed unit (work and each fix), after the unit returns.
Transient red is acceptable — the phase's review→fix loop heals it before the
draft PR. Conventional commits, scoped to the phase; write the message to a temp
file and `git commit -F <file>`:

- work: `feat(phase-N): <work-unit summary>`
- fix:  `fix(phase-N): address review findings (iter k)`
- fold the `baton-runner/<run-id>/` log/state updates into the same commit.

## Failure handling

When a phase exhausts the 3-iteration cap (or a unit returns `FATAL`):

1. **Preserve everything** — no autonomous `git reset`, branch deletion, or
   force-anything. Preserve and explain, don't tidy.
2. **Write a failure baton**: what each iteration tried, the standing review
   findings, the last gate output path, and the best hypothesis for the block.
3. **Offer the user three choices**, act only on their pick:
   - **Guide & resume** → user adds direction; spawn a fresh fix unit seeded with
     the failure baton + guidance (iteration counter resets).
   - **Waive & proceed** → user explicitly accepts remaining findings; record the
     waiver in the phase's PR body + log; close the phase.
   - **Abandon/rollback** → ONLY here may the manager `git reset` the phase
     branch, and only because the user said so.

## Fatal errors (→ PAUSE; status PAUSED, or FAILED if unrecoverable)

- A required skill (`baton-pass`/`tdd`/`code-review`/`ecc-security-review`) or
  `scripts/gate.sh` is missing in the worktree.
- Test infrastructure cannot run at all (not "tests fail" — that is normal work).
- A unit returns `FATAL`, or `INCOMPLETE` ≥3× with no progress.
- Review can't reach `CLEAN` within the 3-iteration cap.
- A global/per-phase budget ceiling is hit.
- `gh pr create` fails, or a merge/rebase conflict needs non-mechanical judgment.
- Any unit reports it must act outside the containment contract.

On fatal: set `STATE = PAUSED` (or `FAILED` if unrecoverable), record the reason
and the exact resume point, show the user, and stop. Resuming re-reads `STATE.md`.
