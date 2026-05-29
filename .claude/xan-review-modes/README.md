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

## Layer-slice fan-out

The user-scope skill reviews **one target per invocation**. Friendex's
hexagonal layout (`domain / application / persistence / discord / tasks
/ wiring / tests`) is documented in `ARCH.md`; to replicate the full
"slice-by-slice review" pattern, invoke the skill once per slice with
the same `--mode`. Each mode file lists the relevant invocations under
its own *Layer-slice usage* heading.

This stays out of the core skill on purpose — it's Friendex-specific
operational discipline, not a generic review feature.

## Exclusion-list pass (avoid duplicate findings)

Before running any mode that will file a tracker issue (everything
except `pre-pr`), fetch the open-issue findings to skip. This is the
operational lesson from issues #82, #83, and #84 (the 2026-05-28
review pass) — without an exclusion step, parallel reviewers
re-discover the same items and you do triage twice.

```bash
gh issue list \
  --repo z0rd0n88/Friendex \
  --state open \
  --limit 30 \
  --label review,tech-debt,security,performance,architecture \
  --json number,title,body \
  --jq '.[] | "#\(.number) — \(.title)"'
```

For each matched issue, extract the checkbox lines from the body and
build a flat skip list of the form `<short title> — <file:line>`.
Inject it verbatim into the synthesizer's prompt under a heading like:

> **DO NOT report findings already tracked in:**
> - Issue #N (<short title>): <skip rule 1>, <skip rule 2>, …
> - Issue #M: …

The user-scope skill doesn't have a first-class "prompt prelude" hook
yet, so the workflow is: run the skill, then before the synthesizer
fires, paste the skip list into the chat (the orchestrator can include
it when expanding the synthesizer brief). If this workflow proves
load-bearing, file an issue on the user-scope skill to add a
`--prompt-prelude <path>` flag.

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
deduplicates by reviewer; the exclusion-list step deduplicates against
already-tracked work.
