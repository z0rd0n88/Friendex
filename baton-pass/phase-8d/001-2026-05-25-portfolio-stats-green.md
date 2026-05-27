# Pass-Baton: Phase 8d GREEN — portfolio + stats services landed

**Date:** 2026-05-25
**Scope:** phase-8d
**Branch:** feat/phase-8d-portfolio
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 306c5df chore(phase-8c): review iter1 CLEAN + digest
*(work uncommitted on top of HEAD — three prod files + two test files + this baton)*

## Where things stand

Phase 8d implementation complete and gate-green. Five new files, zero
modified. All 10 acceptance criteria (D1–D10) pinned with concrete
assertions; two bonus tests round out the read-model wiring and the empty-
history fallback. 500/500 pytest suite-wide (486 prior + 14 new), ruff
check + format clean, mypy clean across 37 source files. Coverage on the
three new prod modules: 92% combined (`snapshot_models.py` 100%,
`portfolio_service.py` 89%, `stats_service.py` 90%). The seven uncovered
lines are all defensive `None` guards — see §coverage below.

Work is uncommitted and ready for the orchestrator's commit + PR step.

## Files created (all under the worktree root)

- `src/friendex/application/snapshot_models.py` (4 frozen dataclasses:
  `PortfolioSnapshot`, `TrendingEntry`, `PriceStats`, `UserStats`).
- `src/friendex/application/portfolio_service.py` (`PortfolioService`:
  `calculate_net_worth`, `portfolio_snapshot`, `capture_month_start_net_worth`).
- `src/friendex/application/stats_service.py` (`StatsService`:
  `trending_snapshot`, `user_stats`, `get_price_stats`).
- `tests/application/test_portfolio_service.py` (6 tests, D1–D5 + smoke).
- `tests/application/test_stats_service.py` (8 tests, D6–D10 + boundaries).

## Design decisions executed (declared in the kickoff baton)

1. **Read paths lockless.** `calculate_net_worth`, `portfolio_snapshot`,
   `trending_snapshot`, `user_stats`, `get_price_stats` — no lock acquired.
2. **`capture_month_start_net_worth` writes per-user under a per-user
   `locked()` sweep** (mirrors `TradingService.update_frozen_shorts` from
   Phase 8c digest §convention 1). Composite key `"<guild>:<user>"` via
   `_lock_key`. Sweep takes ONE `locked()` per account, never wrapping the
   whole loop (Phase 7 deadlock rule). Writes both `net_worth` and
   `month_start_net_worth` inside the critical section via
   `dataclasses.replace`.
3. **24h high/low boundary** — `since = now - timedelta(hours=24)`, inclusive
   `>=` per `IPriceRepo.get_history` / Phase-6c digest / Phase-8-fakes
   digest. Empty-window fallback: high = low = `stock.current`.
4. **Net-worth math 100% delegated** to
   `domain.fund_math.compute_net_worth` — service is a pure orchestrator
   that composes price + fund lookups. Preserves the
   `locked_cash + locked_fund == shares*entry_price` invariant relied on by
   the domain helper (Phase 4 digest contract).
5. **Trending math 100% delegated** to
   `domain.activity.calculate_trending_score` +
   `domain.activity.get_engagement_tier`. Zero-score filter + descending
   sort + slice happen in the orchestrator; the math doesn't.
6. **Personal fund lookup uses `fund_id == user_id`** — matches the Phase
   8a/8c convention.

## RED capture (pre-implementation)

```
$ uv run pytest tests/application/test_portfolio_service.py \
    tests/application/test_stats_service.py -v
ERROR tests/application/test_portfolio_service.py
  ModuleNotFoundError: No module named 'friendex.application.portfolio_service'
ERROR tests/application/test_stats_service.py
  ModuleNotFoundError: No module named 'friendex.application.stats_service'
!!! Interrupted: 2 errors during collection !!!
```

## Verification (gate)

```
$ uv run ruff check src tests
All checks passed!

$ uv run ruff format --check src tests
74 files already formatted

$ uv run mypy src/friendex
Success: no issues found in 37 source files

$ uv run pytest tests/application/test_portfolio_service.py \
    tests/application/test_stats_service.py -v
14 passed in 0.06s

$ uv run pytest tests/application/
149 passed in 0.69s

$ uv run pytest
500 passed in 6.61s
```

## Coverage

```
Name                                            Stmts   Miss Branch BrPart  Cover
-------------------------------------------------------------------------------------
src/friendex/application/portfolio_service.py      54      3     12      4    89%
src/friendex/application/snapshot_models.py        31      0      0      0   100%
src/friendex/application/stats_service.py          52      4      8      2    90%
-------------------------------------------------------------------------------------
TOTAL                                             137      7     20      6    92%
```

Uncovered lines (all defensive `None`-guards / fallbacks):
- `portfolio_service.py:132,144` — `calculate_net_worth` /
  `portfolio_snapshot` returning `None` for absent users. Not behind an AC.
- `portfolio_service.py:177` — mid-sweep `None`-guard in
  `capture_month_start_net_worth` (account deleted between `list_all` and
  per-user `get`). Defensive only.
- `portfolio_service.py:110->108` — branch when `price_repo.get` returns
  `None` for a referenced target (every test fixture supplies a stock).
- `stats_service.py:117,148` — current-price-missing branch in
  `trending_snapshot` (the `_zero_price()` fallback) and `user_stats`
  returning `None` for an absent user.
- `stats_service.py:180-182` — `_zero_price()` itself (the lazy `Decimal`
  import + return). Triggered only by the missing-stock fallback.

## Open questions / risks

- M2 carry-forward from Phase 8c review (`ITradeCooldownRepo.get` Protocol
  drift) remains OUT OF SCOPE for 8d — 8d does not touch `cooldown_repo`
  or `interfaces.py`. Defer to 8e/8f.
- The `Stock` model still carries stale `high_24h` / `low_24h` fields
  (already noted in Phase 8b digest). 8d ignores them and computes
  dynamically, but the persisted values will drift further from any
  read-time computation. Cleanup is a separate model-shrink unit, NOT
  this phase's problem.

## Next steps

1. **Orchestrator commits + PR.** Suggested commit boundary (matches the
   work-unit spec's "three commits" guidance):
   1. `feat(application): snapshot read models for portfolio + stats`
   2. `feat(application): portfolio + stats services (read-only, Phase 8d)`
   3. `test(application): portfolio + stats services`
   Single PR referencing `Refs #2`.
2. **Independent review** to run the same gate + spot-check D1–D10
   non-tautologically. Mutation candidates worth flipping:
   - drop the zero-score filter in `trending_snapshot` (D7 should fail)
   - flip `reverse=True` to `reverse=False` (D6 should fail)
   - change `since = now - 24h` to `now - 48h` (D9 should fail — the
     `outside_old_high` point at 48h is exactly the kind it would let in)
   - drop the per-user `locked()` in `capture_month_start_net_worth` (D5
     would still pass because there's no concurrent trade in the test —
     this is a known coverage gap; the lock is concurrency-defensive)
3. **Phase 8d digest** to be written by the review unit at
   `baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md` once review is
   CLEAN. Should capture the read-only-no-lock convention, the single
   per-user lock for `capture_month_start_net_worth`, the 24h dynamic
   high/low boundary, and the read-model dataclass shape for Phase 10.

## References

- Spec: `docs/04-migration-plan.md` §Phase 8d (~lines 504–529)
- Open-Q9: `docs/02-target-architecture.md` line ~943
- Issue: #2 (Phase 8d box)
- Kickoff baton: `baton-pass/phase-8d/000-2026-05-25-portfolio-stats-kickoff.md`
- Prior digests: `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md`,
  `digest-phase-8b.md`, `digest-phase-8a.md`, `digest-phase-8-fakes.md`,
  `baton-runner/br-2026-05-23-p4p5/digest-phase-4.md`
- Code: `src/friendex/application/portfolio_service.py`,
  `src/friendex/application/stats_service.py`,
  `src/friendex/application/snapshot_models.py`
- Tests: `tests/application/test_portfolio_service.py`,
  `tests/application/test_stats_service.py`
