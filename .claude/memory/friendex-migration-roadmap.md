---
name: friendex-migration-roadmap
description: Friendex (z0rd0n88/Friendex) is being greenfield-rebuilt from spec via an 18-phase plan tracked by GitHub issue #2; status snapshot as of 2026-05-15.
metadata:
  type: project
---

Friendex is the Discord stock-exchange bot at `~/Friendex` (GitHub `z0rd0n88/Friendex`, formerly `StockXchange` — renamed in PR #6 on 2026-05-14). The repo has **no `bot.py`**; the original monolith lives only in `Slut Stock xXxchange [Overview + Dev Brief + Code Skeleton].md` and is being rebuilt from scratch into a hex-arch package (`src/friendex/{domain,application,adapters}`).

**Plan source:** `docs/04-migration-plan.md` lays out 18 phases (0 → 17), each shipped as its own PR against `main` with a deterministic gate (`ruff` + `mypy` + `pytest --cov-fail-under=...`). PR bodies reference master tracking issue **#2**; final phase will `Closes #2`.

**Status as of 2026-05-15:**
- Phase 1 (scaffold) — merged in PR #4
- Phase 2 (config & constants) — PR #7 (draft); rebased + path-aligned to `friendex` package on 2026-05-15, gates green (24 tests, 100% cov on `config.py`); needs `gh pr ready 7` then merge
- Phases 3-17 not started (domain models → ORM → services → cogs → bot entry → cutover → hardening)

**Why:** Original spec was a single `bot.py` skeleton; rebuild aims to make every layer testable in isolation and replace JSON files with SQLite via Alembic migrations.

**How to apply:** When the user asks "what's next" on Friendex, the answer is whatever the next unchecked box on issue #2 is. New work always belongs on `feat/phase-N-<slug>` branches in `.worktrees/phase-N-<slug>` (per the repo CLAUDE.md hard rule). Don't propose net-new architecture work outside the phase plan — propose a phase deviation if needed.

**Trap to remember:** PR #6's rename moved `src/stockxchange/` → `src/friendex/`. Any worktree branched before the rename will still write to `src/stockxchange/`; needs `git mv` after rebase. Worktree `.git` files that point at the old `/home/alex/StockXchange/` path are unrepairable via `git worktree repair` — recreate from `origin/<branch>` instead (the branches are pushed, so files won't be lost).
