# Running Friendex phases with `baton-runner` (cloud session edition)

> **Note:** The Friendex phased build is **complete as of 2026-05-28**. This guide
> is preserved as reference documentation for how the build was orchestrated with
> `baton-runner`. It remains relevant for future multi-phase work in this or other
> projects that use the same `baton-runner` skill.

> **Audience:** you (the project owner) driving a multi-phase Friendex build from
> a cloud Claude Code session — claude.ai/code in a browser, a remote/sandboxed
> Claude container, or a fresh local session that needs to behave like one
> (clean environment, no implicit dotfiles, signoff via chat).
>
> **What this guide is not:** the skill's internal contract. The control-flow
> rules for the manager session live in
> [`.claude/skills/baton-runner/SKILL.md`](../../.claude/skills/baton-runner/SKILL.md)
> and the operational detail in
> [`.claude/skills/baton-runner/REFERENCE.md`](../../.claude/skills/baton-runner/REFERENCE.md).
> Read those *once* if you want to understand the machinery; come back here for
> the human-facing workflow.

## Executive summary

`baton-runner` is the user-invoked Opus orchestrator we use to ship Friendex's
phase plan (`docs/04-migration-plan.md`) one phase at a time. The session you
start becomes a **manager** that spawns implement → review → fix subagents,
threads file paths between them, commits per unit, and opens one draft PR per
phase. All state is durable on disk (`baton-runner/<run-id>/STATE.md`), so the
run is **resumable across sessions** — including from a clean cloud session
that has never seen the work before. You merge the resulting draft PRs by hand,
in order, then tick the relevant box on issue #2.

A cloud session is no different from a local one *in protocol*, but you need to
prepare its sandbox so the prerequisites resolve: a writable clone, `gh` auth,
`uv`, the project's Skill tooling, and the four required skills the units
invoke (`tdd`, `baton-pass`, `code-review`, `ecc-security-review`). The bulk of
this guide is that preparation, the first message you send the manager, the
signoff handshake, and what to do when something pauses.

## Contents

1. [When (and when not) to use baton-runner](#1-when-and-when-not-to-use-baton-runner)
2. [Cloud session prerequisites](#2-cloud-session-prerequisites)
3. [Pre-flight: getting your phase list READY](#3-pre-flight-getting-your-phase-list-ready)
4. [Kicking off: the first message to the manager](#4-kicking-off-the-first-message-to-the-manager)
5. [The signoff handshake](#5-the-signoff-handshake)
6. [Reading progress without breaking the manager](#6-reading-progress-without-breaking-the-manager)
7. [Resuming a run from a fresh session](#7-resuming-a-run-from-a-fresh-session)
8. [After the run: merging the stack and updating issue #2](#8-after-the-run-merging-the-stack-and-updating-issue-2)
9. [Failure handling — your three choices](#9-failure-handling--your-three-choices)
10. [Cloud-specific gotchas](#10-cloud-specific-gotchas)
11. [Scheduled / cron tooling — what it can and can't do for this loop](#11-scheduled--cron-tooling--what-it-can-and-cant-do-for-this-loop)
12. [Appendix A — minimum cloud-session bootstrap checklist](#appendix-a--minimum-cloud-session-bootstrap-checklist)
13. [Appendix B — anatomy of a `baton-runner/<run-id>/` directory](#appendix-b--anatomy-of-a-baton-runnerrun-id-directory)

---

## 1. When (and when not) to use baton-runner

Use it when:

- You have **one or more phases** of the migration plan to ship in a single
  session block (e.g. Phase 10, or Phase 11 split into 11a/11b/11c).
- Each phase has clear acceptance criteria — `READY` per the skill's spec
  readiness check — or you're willing to draft criteria at signoff.
- You want machine-gated, agent-reviewed draft PRs at the end (one per phase),
  ready for you to merge in order.

Skip it for:

- One-off bug fixes, doc edits, or refactors with no phase boundary. Use a
  normal worktree + PR; baton-runner overhead is wasted.
- Anything where you'd want to inspect / steer mid-edit. The manager
  intentionally never opens diffs or baton contents — that's how it stays
  context-cheap. If you need to drive the implementation hand-on-keyboard,
  drive it directly.
- Phases that are still `BLOCKED` per the skill's readiness check. Resolve the
  ambiguity in the spec first; otherwise pre-flight will pause for you anyway.

History as a calibration point: Friendex Phases 4 → 9 each shipped through
`baton-runner`. The heaviest single run was Phase 8 at ~20 units across 8
sub-phases / 8 stacked PRs (≈3.5h wall time). The lightest was Phase 9 at 2
units, one-shot CLEAN, single PR. Plan accordingly when sizing the remaining
work.

## 2. Cloud session prerequisites

Whether the session is `claude.ai/code` in a browser, a hosted Claude container,
or a fresh local terminal, it needs the following resolvable from the working
directory **before** you invoke the skill. Treat this as a hard gate — every
one of these is a fatal-pause trigger if missing once units start.

| Need | Why it matters | Quick verification |
|---|---|---|
| A clone of `Friendex` with `origin` set | All git/PR mechanics run from this clone | `git remote -v` shows `z0rd0n88/Friendex` |
| Write access to a fresh branch | Each phase pushes its own branch | `git push --dry-run origin HEAD` succeeds |
| `gh` CLI authed against `z0rd0n88/Friendex` | Manager opens draft PRs via `gh pr create` | `gh auth status` shows logged-in to GitHub |
| `uv` ≥ 0.4 on PATH | `gate.sh` runs `uv run pytest`/`ruff`/`mypy` | `uv --version` |
| Python ≥ 3.11 available to `uv` | Project pins `python>=3.11` | `uv run python -V` |
| `.claude/skills/baton-runner/` resolves | The manager invokes its own skill | `ls .claude/skills/baton-runner/SKILL.md` |
| `.claude/skills/baton-runner/scripts/gate.sh` exists & executable | Review unit runs the gate | `test -x .claude/skills/baton-runner/scripts/gate.sh` |
| `baton-pass`, `tdd`, `code-review`, `ecc-security-review` skills available | Every unit invokes them | They appear in the session's skill list |
| `python-pro` agent available with the `Skill` tool | Default unit-agent for work/review/fix | The agent list includes `python-pro` |
| `DISCORD_TOKEN` is *not* required | We never run the bot; tests cover everything | — |

A few cloud-specific notes:

- **Filesystem persistence.** Some cloud sandboxes evict the workspace between
  sessions. If yours does, the run becomes effectively single-session: you must
  finish or hit `STATE = PAUSED` and commit/push the `baton-runner/<run-id>/`
  directory before the sandbox is torn down. The directory is auto-committed
  alongside each unit's work commit, so as long as one phase completes (and
  pushes), resumption is possible.
- **Secrets.** Cloud sessions don't read your local `~/.secrets`. Ensure `gh`
  auth is established via whatever mechanism that environment supports (GitHub
  device code flow, an injected `GH_TOKEN`, etc.) **before** spawning the first
  unit — once units start, a missing `gh` is a fatal pause.
- **Network.** `uv sync` will hit PyPI on first run; cold installs in
  bandwidth-constrained sandboxes can take a few minutes. Run `uv sync` once
  manually before invoking the skill so the first unit doesn't burn its bail
  budget on cold-cache installs.

## 3. Pre-flight: getting your phase list READY

`baton-runner`'s pre-flight checklist (in `SKILL.md`) classifies each phase as
`READY`, `THIN`, or `BLOCKED`. You can short-circuit a lot of mid-flight
friction by walking the same checks yourself before invoking:

1. **Pick the phases.** Open `docs/04-migration-plan.md` and identify the
   contiguous block you want to ship. Estimates for each phase live in the
   migration plan's complexity and calendar tables.
2. **Confirm the issue tracker.** Each phase typically maps to a checklist
   item on issue #2 (`gh issue view 2 --json body`). Note the issue number;
   the manager threads it through the draft PR body as `Refs #2`.
3. **Right-size each phase.** The skill targets ≤ ~5 acceptance criteria /
   ~10 files per unit. If a single migration-plan phase is heavier than that
   (e.g. Phase 11 Cogs, Phase 17 Hardening), pre-split it into sub-units
   (`11a`, `11b`, …) in your kickoff message rather than letting the manager
   ask you mid-run.
4. **Acceptance criteria.** For each phase the manager asks "is this
   testable?". If the migration plan's bullets are already concrete (e.g.
   "background loops X, Y, Z run on cadence C and update price via …"), it's
   `READY`. If they're aspirational ("hardening"), expect `THIN` and be ready
   to confirm or refine criteria at signoff.
5. **Carry-forwards.** Skim the most recent run's `digest-phase-<N>.md` files.
   The manager will thread these into each phase's prompt as continuity
   context — but it helps you sanity-check that nothing material is missing.

> `★ Insight ─────────────────────────────────────`
> The digests are the public-surface contract between phases — typically ≤40
> lines of "modules + key signatures + decisions". You don't need to read them
> all to drive a run, but eyeballing the latest one tells you what the next
> phase is *allowed to assume*.
> `─────────────────────────────────────────────────`

## 4. Kicking off: the first message to the manager

The first turn should: invoke the skill, name the phase(s), point at their
specs, propose a run-id, declare the unit-agent, and authorize the global
budget if you want anything other than the default 75.

A minimal template:

```text
Run baton-runner.

Run-id: br-<YYYY-MM-DD>-phase-<N>
Phases: phase-<N>  (spec: docs/04-migration-plan.md §Phase <N>)
Unit-agent: python-pro  (default — keep)
Carry-forwards to thread: baton-runner/br-<latest-prev>/digest-phase-<M>.md
Issue tracker: Refs #2
Budgets: defaults (global 75, per-phase thrash 20, bail 50 calls / 10 files)

Pre-flight, then pause for my signoff before spawning work-unit 1.
```

What happens next, in order:

1. **Pre-flight.** The manager creates the worktree, parses the phase list,
   classifies readiness, records the unit-agent in `STATE.md`, initializes
   `log.md`, and writes out the first `STATE.md`. It then **pauses**.
2. **Signoff.** The manager surfaces: open questions, any proposed criteria
   for `THIN` phases, and the chosen agent. *Nothing implements until you
   reply.*
3. **Phase 1 work-unit.** On your `proceed`, the manager spawns the first
   work-unit with `model: opus` and the full work-unit prompt (containment +
   return contracts from `REFERENCE.md`). It does *not* read the unit's
   diffs — only the 3-line return.

If you want to ship multiple phases in one run, list them all in the kickoff
message and let pre-flight classify them together. Each phase is its own loop;
the manager won't start phase N until phase N-1 is CLEAN + PR'd.

## 5. The signoff handshake

The signoff is your last cheap intervention point. Use it to:

- **Refine criteria** for `THIN` phases. Don't let the manager invent criteria
  silently — that's by design, and it expects you to confirm.
- **Override the unit-agent** if a phase truly needs something other than
  `python-pro` (rare — the agent is already configured with the `Skill` tool
  specifically so it can serve all three roles).
- **Adjust budgets** if you expect heavy phases (e.g. raise per-phase thrash
  for Phase 11 / Phase 17, or lower the global ceiling for a small run).
- **Pre-authorize re-use** of digests from prior runs — the manager will list
  what it intends to thread; correct the list if a digest is stale or
  irrelevant.

Reply with a single, decisive message — `proceed` plus any deltas. Once the
work-unit spawns, you can't cheaply revise the criteria without abandoning the
phase.

## 6. Reading progress without breaking the manager

The manager updates two files on every transition:

- `baton-runner/<run-id>/STATE.md` — current phase, current unit, review
  iteration, baton path, units-used vs ceiling, pause reason.
- `baton-runner/<run-id>/log.md` — append-only UTC-stamped lines, one per
  spawn / return / commit / PR / pause.

You can `cat` either at any time without disturbing the run. **Do not** edit
them while a unit is mid-execution — they're written by the manager between
units, and your edits will be clobbered at the next transition.

If you want a single-glance status line, the log's progress format is:

```
phase 3/6 · FIX iter2 · VERDICT ISSUES · units 17/75
```

— phase index, current unit + iter, last VERDICT, and global budget. That's
the same number the manager echoes after every unit.

## 7. Resuming a run from a fresh session

This is the cloud workflow's load-bearing feature. Whether your session
expired, you closed the tab, or your sandbox got torn down, you can resume by:

1. **Open a new Claude Code session** in the same repo clone (or a fresh clone
   if the workspace was lost — see note below).
2. **Confirm prerequisites** from §2 still hold.
3. **Re-invoke the skill** with a one-line resume directive:

   ```text
   Resume baton-runner run br-<YYYY-MM-DD>-phase-<N>.
   STATE.md is at baton-runner/br-<...>/STATE.md.
   ```

   The manager reads `STATE.md`, picks up at the exact recorded resume point,
   and **never restarts completed phases**.

If the workspace was destroyed: re-clone, `git checkout` the latest pushed
phase branch (the most recent `feat/<run-id>/phase-<K>` from the run), and
ensure the `baton-runner/<run-id>/` directory is present on that branch. As
long as one phase completed and was pushed before the loss, you have the full
state.

If *no* phase completed before the loss: there is nothing to resume — start
over with the same run-id. Re-running pre-flight is idempotent.

## 8. After the run: merging the stack and updating issue #2

`baton-runner` opens draft PRs but **merges nothing**. When the manager
reports `STATE = DONE`:

1. **Mark each draft PR ready for review** in order (`gh pr ready <N>`).
2. **Wait for CI** on each. The gate already ran locally, but GitHub Actions
   re-runs the suite against both Python 3.11 and 3.12. If a Python-version-
   specific failure surfaces, fix in a follow-up PR; don't reopen the
   phase.
3. **Squash-merge in order.** Each PR was opened against the previous phase's
   branch; merging in order keeps the diff stack clean. `deleteBranchOnMerge`
   is enabled, so the head branch evaporates after each merge.
4. **Tick the corresponding box on issue #2** and append the `(#<PR>)` ref so
   the audit trail shows which PR delivered which phase.
5. **Clean up the worktree** locally:
   ```bash
   git worktree remove .claude/worktrees/<run-id>
   git branch -D feat/<run-id>/phase-<final>  # if it lingered
   git fetch origin --prune
   ```

> A documentation-only PR pattern is also supported: after a complex phase,
> you may want to land a `baton-pass/` follow-up PR documenting any carry-
> forwards for the next run. See the existing Phase 7 → Phase 8 handoff
> (PR #39) for a worked example.

## 9. Failure handling — your three choices

If review can't reach `CLEAN` within 3 iterations, or a unit returns `FATAL`,
the manager preserves everything (no `git reset`, no force-push), writes a
**failure baton**, and pauses. It will offer you exactly three choices:

| Choice | What it means | When to pick it |
|---|---|---|
| **Guide & resume** | You write a directive; manager spawns a fresh fix-unit seeded with the failure baton + your guidance; iteration counter resets | You see the actual blocker and can articulate the fix in plain language |
| **Waive & proceed** | You explicitly accept the standing findings; the waiver is recorded in the phase's PR body + log; the phase closes | The findings are real but acceptable (e.g. LOW-only) and you'd rather ship + follow up |
| **Abandon / rollback** | The manager `git reset`s the phase branch — **only** because you said so | The whole approach is wrong and a different decomposition is needed |

The cost of guessing wrong is asymmetric: waiving a real CRITICAL/HIGH is
worse than asking the manager to retry. When in doubt, **guide & resume**.

## 10. Cloud-specific gotchas

A few hazards that bite cloud sessions specifically (local sessions hit most
of these less often because the dotfiles smooth them over):

- **No `~/.secrets` autoload.** Local sessions get `GH_TOKEN` injected via
  `settings.local.json`'s `env` block. Cloud sessions don't — confirm `gh
  auth status` before invoking the skill. A `gh pr create` failure mid-run is
  a fatal pause.
- **Long-running sessions hitting context ceilings.** If the **manager**'s
  own context fills (rare — it's intentionally lean), it will finalize
  `STATE.md`, tell you to resume fresh, and stop. Don't fight this; just
  follow §7. If a **unit**'s context fills, it returns `INCOMPLETE` and the
  manager seeds a continuation — handled automatically.
- **WSL arg-mangling.** Not cloud-specific but worth restating: commit
  messages and PR bodies with a `:` in them get truncated if passed inline on
  some shells. The skill already writes both to temp files and uses `-F` /
  `--body-file`. If you ever hand-edit a commit message during failure
  handling, do the same.
- **MCP server availability.** Some skills the units rely on may have MCP
  dependencies (e.g. `code-review`, `ecc-security-review`). Verify in §2 that
  these resolve in your cloud session before invoking.
- **`uv` cache cold-starts.** Pre-warm with `uv sync` before the first unit.
- **Filesystem case-sensitivity.** Some cloud sandboxes are case-insensitive;
  Friendex is developed on case-sensitive Linux. Watch for import-path
  mismatches if test runs surface bizarre `ModuleNotFoundError`s.

## 11. Scheduled / cron tooling — what it can and can't do for this loop

You have access to scheduled-remote-agent tooling (the `schedule` skill /
cron primitives). It's tempting to wire baton-runner into a cron schedule
and have it grind through phases overnight. **Don't.** Two reasons:

1. The skill is **user-invoked** with a mandatory signoff between pre-flight
   and the first unit, and it pauses for user judgment on every review
   failure. A cron-fired run will pause at signoff and sit there idle.
2. Each unit is a fresh subagent spawn — the **manager** must be alive to
   spawn them. Cron-firing a single Claude turn doesn't keep a multi-hour
   orchestrator running; you'd just get one spawn per cron tick, which is
   slower and more error-prone than a single live session.

What cron *is* good for, around the edges:

- **PR-status polling.** After a run, schedule a job that checks `gh pr
  status` for the draft stack and pings you when CI lands.
- **Inventory / `ARCH.md` refresh.** Already handled by the repo's commit
  hook, but a daily off-hours sanity check doesn't hurt.
- **Carry-forward review.** A weekly job that lists open items in
  `baton-pass/INDEX.md` is a cheap nudge for not letting follow-ups rot.

If you do schedule any of these, keep the prompts short and read-only — they
shouldn't touch the repo or open PRs without you in the loop.

## Appendix A — minimum cloud-session bootstrap checklist

Copy-pasteable verification block. Run before invoking the skill:

```bash
# Repo + git
git remote -v | grep -q 'z0rd0n88/Friendex' && echo OK: remote
git rev-parse --abbrev-ref HEAD             # any branch is fine; manager makes its own
git push --dry-run origin HEAD              # confirms write access

# GitHub CLI
gh auth status                               # must show logged-in + repo scope
gh repo view z0rd0n88/Friendex --json defaultBranchRef -q .defaultBranchRef.name  # → main

# Toolchain
uv --version
uv run python -V                             # ≥ 3.11
uv sync                                      # pre-warm cache

# Skill surface
ls .claude/skills/baton-runner/SKILL.md \
   .claude/skills/baton-runner/REFERENCE.md \
   .claude/skills/baton-runner/scripts/gate.sh
test -x .claude/skills/baton-runner/scripts/gate.sh && echo OK: gate executable

# Quick smoke test of the gate (optional but reassuring — ~30s on warm cache)
.claude/skills/baton-runner/scripts/gate.sh /tmp/gate-smoke
# Expect: GATE: PASS
```

## Appendix B — anatomy of a `baton-runner/<run-id>/` directory

After a run completes, the directory looks like this (using the Phase 8 run
as a worked example because it shows the multi-sub-phase pattern):

```
baton-runner/br-2026-05-25-phase-8/
├── STATE.md                  # final status: DONE, all phases CLEAN
├── log.md                    # append-only audit trail
├── digest-phase-8-fakes.md   # one digest per phase (or sub-phase)
├── digest-phase-8a.md        # — these are the inputs to the NEXT run
├── digest-phase-8b.md
├── digest-phase-8c.md
├── digest-phase-8d.md
├── digest-phase-8e.md
├── digest-phase-8f.md
└── gate-phase-8a-iter-1/     # gate.sh logs per phase / iteration
    ├── pytest.log
    ├── ruff-check.log
    ├── ruff-format.log
    └── mypy.log
```

Three things are worth knowing:

1. **The digests outlive the run.** Future runs read them as continuity
   context. Don't delete or rewrite old digests — they're the public surface
   contract.
2. **The gate logs are evidence, not noise.** When a review unit returns
   `VERDICT: ISSUES`, the gate logs are how you (or a guide-resume fix-unit)
   reconstruct what actually failed.
3. **The `baton-pass/<phase-N>/` files live elsewhere.** They're per-unit
   batons under the top-level `baton-pass/` tree, indexed by
   `baton-pass/INDEX.md`. The manager records only their paths in `STATE.md`;
   you read them directly when you want the unit's reasoning.

---

*Cross-references:*

- Skill mechanics → [`.claude/skills/baton-runner/SKILL.md`](../../.claude/skills/baton-runner/SKILL.md),
  [`.claude/skills/baton-runner/REFERENCE.md`](../../.claude/skills/baton-runner/REFERENCE.md)
- Phase plan → [`docs/04-migration-plan.md`](../04-migration-plan.md)
- Live phase status → GitHub issue #2 (never duplicate it in the repo)
- Pass-baton conventions → [`baton-pass/CLAUDE.md`](../../baton-pass/CLAUDE.md),
  [`baton-pass/INDEX.md`](../../baton-pass/INDEX.md)
- Testing strategy → [`docs/05-testing-strategy.md`](../05-testing-strategy.md)
