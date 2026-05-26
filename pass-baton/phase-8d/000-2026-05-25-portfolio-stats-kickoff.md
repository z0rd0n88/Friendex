# Pass-Baton: Phase 8d kickoff — Portfolio & Stats Services (read-only)

**Date:** 2026-05-25
**Scope:** phase-8d
**Branch:** feat/phase-8d-portfolio
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 306c5df chore(phase-8c): review iter1 CLEAN + digest

## Where things stand

Kickoff for Phase 8d (`docs/04-migration-plan.md` §Phase 8d, lines ~504–529).
Read continuity batons (8a/8b/8c/8-fakes digests + phase-4 digest) and have
the per-guild constructor shape, fake-repo surface, domain pure-func contract,
and the §Open-Q9 dynamic-24h-high-low decision in hand.

Three new files in `src/friendex/application/`:

- `snapshot_models.py` — frozen DTOs (`PortfolioSnapshot`, `TrendingEntry`,
  `PriceStats`, `UserStats`) tailored to Phase 10 embed builders, deliberately
  distinct from domain models.
- `portfolio_service.py` — `PortfolioService(*, guild_id, user_repo, price_repo,
  fund_repo, settings)`; methods `calculate_net_worth(user_id)`,
  `portfolio_snapshot(user_id)`, `capture_month_start_net_worth()`. Pure
  orchestrator over `domain.fund_math.compute_net_worth` and personal-fund
  lookup by `fund_id == user_id`.
- `stats_service.py` — `StatsService(*, guild_id, user_repo, price_repo,
  settings)`; methods `trending_snapshot(limit=15)`, `user_stats(user_id)`,
  `get_price_stats(user_id)`. Pure orchestrator over
  `domain.activity.calculate_trending_score` + `get_engagement_tier`. 24h
  high/low computed dynamically via `price_repo.get_history(..., since=now-24h)`.

## Design decisions (committed before TDD)

1. **Read-only ops take no locks** (per spec). Reads are best-effort;
   concurrent ticks during a read are tolerated.
2. **`capture_month_start_net_worth()` writes per-user under a per-user lock**
   — mirrors Phase 8b's `update_frozen_shorts` sweep. The monthly rollover is
   independent per user (no cross-user races) but a concurrent trade or tick
   that touches the same `UserAccount.cash_balance` / positions while we
   recompute net worth would race the `upsert`. Per-user `locked()` reads the
   account, computes net worth, and `upsert`s `replace(account,
   net_worth=…, month_start_net_worth=…)` inside the critical section.
   Lock manager DI is added to `PortfolioService.__init__` for this single
   write path. **Declared here, not assumed.**
3. **24h high/low boundary** — `since = now - timedelta(hours=24)`, inclusive
   (`>=`) per `IPriceRepo.get_history` semantics and Phase-6c digest. The
   stock's *current* price is also included in the window because every
   successful price change appends to history (Phase 8b rule 3).
4. **`capture_month_start_net_worth()` storage target** — the `UserAccount`
   already has both `net_worth: Decimal` and `month_start_net_worth: Decimal`
   fields, so no model gap. The capture writes BOTH (the current net worth is
   the month's starting baseline).
5. **`StatsService.get_price_stats` empty-history fallback** — if the 24h
   window is empty (no ticks in last 24h, brand-new stock, history pruned),
   fall back to the current stock price for both high and low. This avoids
   `None`-valued embeds and matches the most-recent-known-price expectation
   of a `/price` embed.

## Acceptance criteria (RED-first)

PortfolioService: D1 long-only NW · D2 short-only NW · D3 mixed NW concrete ·
D4 frozen-only NW · D5 capture_month_start writes per-user snapshot.

StatsService: D6 trending sorts DESC · D7 trending filters zero scores ·
D8 trending slices to 15 / explicit limit · D9 24h high/low inside window ·
D10 engagement-tier coverage across boundaries.

## Next steps

1. Write `tests/application/test_portfolio_service.py` RED — D1–D5.
2. Write `tests/application/test_stats_service.py` RED — D6–D10.
3. Confirm RED via `uv run pytest tests/application/test_portfolio_service.py
   tests/application/test_stats_service.py -v` and paste failures.
4. Implement `snapshot_models.py`, `portfolio_service.py`,
   `stats_service.py` to GREEN.
5. Run full gate: `uv run ruff check src tests && uv run ruff format --check
   src tests && uv run mypy src/friendex && uv run pytest tests/application/`.

## Open questions / risks

- M2 carry-forward from Phase 8c review (`ITradeCooldownRepo.get` Protocol
  drift) is OUT OF SCOPE for 8d — 8d does not touch `cooldown_repo` or
  `interfaces.py`. Leave for 8e/8f.

## References

- Spec: `docs/04-migration-plan.md` §Phase 8d (~lines 504–529)
- Open-Q9: `docs/02-target-architecture.md` line ~943
- Issue: #2 (master tracking — Phase 8d box)
- Phase-8c digest: `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md`
- Phase-8b digest: `baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md`
- Phase-8-fakes digest: `baton-runner/br-2026-05-25-phase-8/digest-phase-8-fakes.md`
- Phase-4 digest: `baton-runner/br-2026-05-23-p4p5/digest-phase-4.md`
- Domain pure funcs: `src/friendex/domain/fund_math.py`,
  `src/friendex/domain/activity.py`
- Persistence ports: `src/friendex/application/interfaces.py`
- Models: `src/friendex/domain/models.py`
