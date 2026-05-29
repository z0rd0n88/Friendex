---
name: friendex-total-review
description: Run parallel multi-agent reviews of the Friendex codebase. Supports eight modes — code, cleanup, security, architecture, test, perf, docs, pre-pr — each fanning out specialised agents across hexagonal layer slices (domain / application / persistence / discord / tasks / wiring / tests). Always excludes findings already tracked in open issues. Use when the user invokes /friendex-total-review or asks for a "review pass", "code review", "security sweep", "architecture audit", "cleanup pass", "performance review", "docs drift check", or wants a pre-PR triage on the current worktree.
---

# friendex-total-review

Parallel multi-agent code review tailored to Friendex's hexagonal layout. Each mode fans out specialised agents along clean layer seams so the reviewers' outputs are complementary, not duplicated.

The insight that drives the design: **Friendex's `domain → application → adapters` boundaries are clean enough that reviewers can divide work along those seams with minimal overlap, and the split between *correctness* lenses (`code-reviewer`) and *idiom* lenses (`python-reviewer` / `python-pro`) surfaces complementary findings on the same file without duplication** (see `container.py` in the 2026-05-28 review pass).

## Quick start

| Invocation | Mode | Output |
|---|---|---|
| `/friendex-total-review code` | Correctness + atomicity + idiom + typing | Tracker issue, severity-bucketed checkboxes |
| `/friendex-total-review cleanup` | Dead code + duplication + unused helpers | Tracker issue, behaviour-preserving punch list |
| `/friendex-total-review security` | Input/output + money flow + OWASP framing | Tracker issue, threat-model framed |
| `/friendex-total-review architecture` | Hexagonal boundaries + deepening + silent-failure ladder | Tracker issue, refactor opportunities |
| `/friendex-total-review test` | Coverage + fake parity + mock-spec adequacy | Tracker issue, coverage baseline pinned |
| `/friendex-total-review perf` | N+1 + SQLite contention + hot-path Decimal | Tracker issue, perf baseline pinned |
| `/friendex-total-review docs` | ARCH.md / ADR / baton-pass drift | Inline patch proposal, file issue only if large |
| `/friendex-total-review pre-pr` | Diff-only sanity check before opening a PR | Inline summary; no issue filed |

No arg → ask the user which mode. `/friendex-total-review all` runs every mode except `pre-pr` and files one tracker issue per mode.

## Workflow (every mode that files an issue)

1. **Confirm scope.** Read `ARCH.md` first; verify the seven layer slices in REFERENCE.md still match the tree.
2. **Resolve exclusions.** Run the `gh issue list` query in REFERENCE.md to fetch the current open-issue findings. Inject the resulting "skip list" verbatim into every reviewer prompt — this is the #1 lever for avoiding duplicate work.
3. **Map (modes that need it).** Fan out `code-explorer` × N layer slices. Skip mapping for `cleanup`, `docs`, `pre-pr`.
4. **Review fan-out.** Launch the mode's agent set in **one message, multiple Agent tool calls**, one agent per `(slice, lens)`. Apply the exclusion list verbatim. Cap each agent at the word budget in REFERENCE.md.
5. **Consolidate.** Dedupe by `(file, line, root cause)` — when two lenses raise the same file:line, merge with both perspectives noted. Bucket by severity (CRITICAL/HIGH/MEDIUM/LOW). Group HIGH items by theme to feed the PR-slicing block.
6. **File the issue** (skip for `pre-pr`). Title format: `"<Mode> review: N CRITICAL · N HIGH · N MEDIUM · N LOW"`. Body uses the template in REFERENCE.md (checkboxes, suggested PR slicing, source-of-findings footer, cross-refs).
7. **Report.** Print the issue URL, the counts, and any cross-references to existing issues that overlap.

## When to file vs. when to fix

This skill **files issues**; it does **not** push code. If the user asks "review and fix", run the review pass, file the issue, then propose creating a worktree per CLAUDE.md's hard rule before touching code. PR slicing in the issue body tells the user (or `baton-runner`) where to start.

## Conventions

- **Worktree rule** — review passes are read-only; no worktree needed. Follow-up *fixes* always go through `.claude/worktrees/<name>` on a `feat/`/`fix/`/`chore/` branch per CLAUDE.md.
- **Issue style** — match the repo's existing `Refs #N` cross-link convention; `Closes #N` only when the new issue supersedes an open one.
- **Severity discipline** — CRITICAL = block / data corruption / money loss; HIGH = bug or significant quality; MEDIUM = maintainability; LOW = style. Reviewers **must** cite `file:line`.
- **Exclusions are authoritative** — every agent prompt MUST list the open-issue findings to skip. The cost of an extra prompt line is far less than the cost of a duplicate finding.
- **Money + datetime invariants** — every lens must respect: money = `Decimal` quantised to `$0.01` `ROUND_HALF_EVEN`; datetimes = tz-aware UTC; per-guild markets keyed `"<guild_id>:<user_id>"`.

## Advanced features

See [REFERENCE.md](REFERENCE.md) for:
- The seven layer slice definitions
- Per-mode agent + skill picks with word budgets
- Per-mode prompt template with exclusion-list injection
- The `gh issue list` exclusion query
- Issue body template (checkbox tracker + PR-slicing block + source footer)
- Consolidation rules + failure-mode guards
