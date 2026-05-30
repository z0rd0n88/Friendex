# Friendex review modes for `xan-multi-agent-review`

This directory holds project-scoped mode presets consumed by the user-scope
[`xan-multi-agent-review`](~/.claude/skills/xan-multi-agent-review/SKILL.md)
skill. Each `<name>.md` is a frontmatter-only file declaring a reviewer
roster (and optionally a synthesizer); invoking
`xan-multi-agent-review <target> --mode <name>` resolves the preset from
here.

The available modes:

| Mode | Purpose |
|---|---|
| `code` | Correctness + idiom + typing (two-lens fan-out) |
| `cleanup` | Dead code, duplication, unused helpers (behaviour-preserving) |
| `security` | Boundary + money-flow + OWASP + economic exploits |
| `architecture` | Hexagonal boundary, deepening, silent-failure ladder |
| `test` | Coverage + fake parity + mock-spec adequacy |
| `perf` | N+1, SQLite contention, hot-path Decimal, embed limits |
| `docs` | `ARCH.md` / ADR / baton-pass drift |
| `pre-pr` | Diff-only sanity check before `gh pr create` |

## Layer-slice fan-out (use the `slices` target)

Friendex's hexagonal layout is documented in `ARCH.md`. The seven slices
are: `src/friendex/domain/`, `src/friendex/application/`,
`src/friendex/adapters/persistence/`, `src/friendex/adapters/discord_bot/`,
`src/friendex/adapters/tasks/`, `src/friendex/adapters/` (wiring + config),
and `tests/`.

Use the user-scope skill's `slices` target to fan out one roster across
multiple slices in a single invocation:

```bash
xan-multi-agent-review slices \
  src/friendex/domain/ \
  src/friendex/application/ \
  src/friendex/adapters/persistence/ \
  src/friendex/adapters/discord_bot/ \
  src/friendex/adapters/tasks/ \
  --mode architecture \
  --prompt-prelude .claude/xan-review-modes/skip-list.md
```

The skill dispatches `len(roster) × len(slices)` reviewer agents in **one
parallel message** (no sequential per-slice loop), then the synthesizer
emits a single report with findings attributed per slice and a
"Cross-Slice Patterns" section that calls out themes that repeat across
slices. The user-scope skill caps slice count at 6 by default — lift
with `--max-slices <n>` deliberately.

> The seven-slice canonical list above is 7 paths; pick the slices most
> relevant to the mode, or run two invocations (`adapters/*` first,
> `domain/ + application/ + tests/` second).

## Exclusion-list pass (use `--prompt-prelude`)

Before running any mode that will file a tracker issue (everything except
`pre-pr`), refresh `.claude/xan-review-modes/skip-list.md` from the open
tracker issues, then pass it via `--prompt-prelude`. This is the
operational lesson from issues #82, #83, and #84 (the 2026-05-28 review
pass) — without an exclusion step, parallel reviewers re-discover the
same items and you do triage twice.

Fetch the open findings:

```bash
gh issue list \
  --repo z0rd0n88/Friendex \
  --state open \
  --limit 30 \
  --label review,tech-debt,security,performance,architecture \
  --json number,title,body \
  --jq '.[] | "#\(.number) — \(.title)"'
```

For each matched issue, pull its checkbox lines and write them into
`.claude/xan-review-modes/skip-list.md` (gitignored sample lives at
`skip-list.md.example`) under a `DO NOT report findings already tracked in`
heading. Then run the mode:

```bash
xan-multi-agent-review dir src/friendex/application/ \
  --mode security \
  --prompt-prelude .claude/xan-review-modes/skip-list.md
```

The skill injects the file's contents verbatim as `## PROJECT PRELUDE` at
the top of every reviewer brief AND the synthesizer brief. Max 16 KB —
trim to the highest-signal items if the raw `gh` output exceeds that.

## Pre-PR sanity check (use the `diff` target)

For the `pre-pr` mode, run against the uncommitted working-tree diff —
not against a draft PR you have not opened yet:

```bash
xan-multi-agent-review diff --mode pre-pr            # working-tree changes
xan-multi-agent-review diff --staged --mode pre-pr   # only staged hunks
```

Empty diff = hard-fail. Fix CRITICAL / HIGH findings on the same
worktree before opening the PR; the synthesizer output is for the
author's eyes only.

## When to file an issue vs. propose a patch

- **File a tracker issue** for `code`, `security`, `architecture`,
  `test`, `perf`, `cleanup` — the output is meant to be sliced into
  PRs (one branch per theme).
- **Propose a minimal patch** for `docs` when the drift is small.
- **Do not file an issue** for `pre-pr` — the synthesizer output is
  read by the author of the in-flight branch.

## Worktree rule

Review passes are read-only — no worktree required. Follow-up *fixes*
always go through `.claude/worktrees/<name>` on a `feat/` / `fix/` /
`chore/` branch per the repo CLAUDE.md.

## Severity discipline

CRITICAL = block / data corruption / money loss; HIGH = bug or
significant quality issue; MEDIUM = maintainability; LOW = style.
Reviewers must cite `file:line`. The user-scope skill's synthesizer
deduplicates by reviewer; `--prompt-prelude` deduplicates against
already-tracked work; `slices` adds cross-slice pattern attribution.
