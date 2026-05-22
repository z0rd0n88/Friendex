---
name: friendex-migration-roadmap
description: Friendex (z0rd0n88/Friendex) is a greenfield rebuild from spec via an 18-phase plan tracked by GitHub issue #2; this file holds durable facts and defers phase status to the live source so it does not go stale.
metadata:
  type: project
---

Friendex is the Discord stock-exchange bot at `~/Friendex` (GitHub `z0rd0n88/Friendex`, formerly `StockXchange` — renamed in PR #6 on 2026-05-14). The repo has **no `bot.py`**; the original monolith lives only in `docs/spec/original-skeleton.md` (renamed from the bracketed spec filename in PR #10) and is being rebuilt into a hex-arch package (`src/friendex/{domain,application,adapters}`).

**Plan (durable):** `docs/04-migration-plan.md` lays out 18 phases (0 → 17), each shipped as its own PR against `main` (`feat/phase-N-<slug>`) with a deterministic gate (`ruff` + `mypy` + `pytest --cov-fail-under=...`). PR bodies carry `Refs #2`; the final phase `Closes #2`.

**Phase status — DO NOT snapshot it here (a status list rots).** The single source of truth is **issue #2's checklist + merged PRs**. To answer "what's next", run `gh issue view 2 --repo z0rd0n88/Friendex` (take the next unchecked box) and cross-check `gh pr list --repo z0rd0n88/Friendex --state merged`. In-flight work per feature/epic lives in `pass-baton/INDEX.md`. *Non-authoritative hint, last verified 2026-05-22:* Phases 1–3 merged (PRs #4/#7/#11) plus a 3.1 Decimal-money/UTC refinement (PR #13); **Phase 4 — domain pure functions — is next**. Verify against #2 before relying on this line.

**Command surface (durable decision):** migrated `$` prefix → `/` slash commands (`discord.app_commands`) in PR #16 — a docs/spec/config change re-speccing the future Discord phases (10–14). Replies are ephemeral for personal/read commands, public for actions; `config.py` no longer has `command_prefix` (commands sync to `guild_id`).

**Why:** Original spec was a single `bot.py` skeleton; the rebuild makes every layer testable in isolation and replaces JSON files with SQLite via Alembic migrations.

**How to apply:** When asked "what's next" on Friendex, query issue #2 (above) — do not quote a status line from this memory. New work goes on `feat/phase-N-<slug>` branches in `.worktrees/phase-N-<slug>` (per the repo CLAUDE.md hard rule). **Each phase PR ticks its #2 checkbox on merge** — that discipline is what keeps the source of truth current. Don't propose architecture work outside the phase plan — propose a phase deviation instead.

**Trap to remember:** PR #6's rename moved `src/stockxchange/` → `src/friendex/`. Any worktree branched before the rename still writes to `src/stockxchange/`; needs `git mv` after rebase. Worktree `.git` files pointing at the old `/home/alex/StockXchange/` path are unrepairable via `git worktree repair` — recreate from `origin/<branch>` instead (branches are pushed, so files won't be lost).
