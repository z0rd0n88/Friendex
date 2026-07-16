# Contributing to Friendex

This is a single-maintainer project (no external contributors yet), but the
Claude Code / agent workflow follows the same rules a human contributor would.
This doc consolidates the workflow that's otherwise scattered across
`CLAUDE.md`, `baton-pass/CLAUDE.md`, and PR review.

## Prerequisites

See [README.md](./README.md#quick-start--development) for environment setup
(`uv sync`, `.env`, `alembic upgrade head`).

## Branching & worktrees

Every change goes through a worktree on a feature branch — never a direct
commit to `main`.

```bash
git worktree add .claude/worktrees/<name> -b <type>/<name> main
```

Worktrees live under `.claude/worktrees/` (not gitignored; git auto-excludes
registered worktrees from status). Branch prefixes:

| Prefix | Use |
|---|---|
| `feat/` | New functionality |
| `fix/` | Bug fixes |
| `chore/` | Tooling, docs, dependency maintenance |

Clean up after merge (merges auto-delete the head branch on GitHub, so
`git push origin --delete <branch>` will error harmlessly):

```bash
git worktree remove .claude/worktrees/<name>
git branch -D <type>/<name>
git fetch --prune
```

## Before writing Python

Run the `ecc-python-patterns` skill (`/ecc-python-patterns`) before touching
any `.py` file — bug fix, refactor, new feature, or test. This applies even
to small edits; see `CLAUDE.md` for the mandatory-skill rule.

## Development loop

```bash
uv run pytest                                     # tests (coverage-gated at 95%)
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src/ tests/
```

Follow TDD (red → green → refactor) for behavior changes; see
[docs/05-testing-strategy.md](./docs/05-testing-strategy.md) for the test
pyramid, fixture patterns, and the `tests/e2e/` scenario harness.

## Commit messages

Conventional Commits format:

```
<type>: <description>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`.

## Pull requests

- Follow [`.github/pull_request_template.md`](./.github/pull_request_template.md).
- Reference the tracking issue (`Refs #2`, or `Closes #2` for a final phase).
- All three verification gates (ruff, mypy, pytest) must pass in CI before
  merge — see [.github/workflows/ci.yml](./.github/workflows/ci.yml).
- **Phase / project status lives in GitHub issue #2, never as a status line
  in this repo.** For docs/tooling PRs with no Python change, mark the
  Verification gates **N/A** and note "not a phase PR" in Tracking.

## Multi-phase / multi-agent builds

The user-invoked `baton-runner` skill (`.claude/skills/baton-runner/`)
orchestrates implement→review→fix subagent units for larger builds, writing
session handoff notes to `baton-pass/`.

`baton-pass/` is an **append-only audit trail** — every write goes through
the project-scoped `baton-pass` skill, never a direct edit. See
[baton-pass/CLAUDE.md](./baton-pass/CLAUDE.md) for the sequential-numbering
and feature-subdirectory rules. If you're resuming in-flight work, start by
reading [baton-pass/INDEX.md](./baton-pass/INDEX.md).

## Documentation

- [ARCH.md](./ARCH.md) auto-regenerates on every commit (`.githooks/pre-commit`);
  don't hand-edit it. Manual refresh: `python3 .githooks/gen_arch.py`.
- Authoritative living docs (don't re-snapshot their content elsewhere — see
  `CLAUDE.md`'s "Authoritative sources" list): `docs/02-target-architecture.md`,
  `docs/05-testing-strategy.md`, `docs/deployment-guide.md`.
- `docs/01-current-state.md`, `docs/03-python-review.md`, `docs/04-migration-plan.md`,
  and `docs/06-per-guild-markets-migration.md` are historical/point-in-time
  documents — they describe the codebase as it was before or during the
  build, not necessarily as it is now. Each carries a framing note saying so;
  don't rewrite their substance to match current state, only fix broken links.
- New or renamed slash commands must be added to the command tables in both
  `README.md` and `CLAUDE.md`, and to
  [docs/command-reference.md](./docs/command-reference.md).
