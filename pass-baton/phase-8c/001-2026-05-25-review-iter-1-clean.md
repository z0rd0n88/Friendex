# Pass-Baton: phase-8c review iter-1 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-8c
**Branch:** feat/phase-8c-trading
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 2965238 feat(phase-8c): trading service (buy/sell/short/cover + update_frozen_shorts)

## Where things stand

Review unit ran the deterministic gate, re-verified every Phase 8c
acceptance criterion against `tests/application/test_trading_service.py`,
audited the load-bearing architecture invariants (two-user single
`locked()`, `_apply_price_impact_unlocked` RMW atomicity inside the lock,
sweep-per-account in `update_frozen_shorts`, immutability via
`dataclasses.replace`, domain-math delegation through
`apply_trade_impact`), and confirmed no new deps and no
SQL/string-format injection paths in the service. No CRITICAL/HIGH
findings — the implementation matches the spec envelope and the
inherited Phase 7/8a/8b conventions. **Verdict: CLEAN.**

## Verification (this iteration)

```
$ scripts/gate.sh baton-runner/br-2026-05-25-phase-8/gate-phase-8c-iter-1/
PASS pytest        (486 passed across the full suite)
PASS ruff-check    (src tests alembic)
PASS ruff-format
PASS mypy          (Success: no issues found in 34 source files)
GATE: PASS

$ uv run pytest tests/application/test_trading_service.py \
    --cov=friendex.application.trading_service --cov-fail-under=90 \
    --cov-report=term-missing
41 passed; coverage 92.53% (>=90% required). Missing branches all
defensive/cold-path: 190, 261, 378, 383, 449, 452, 655, 659, 698, 725-735.
```

## AC coverage spot-checks (load-bearing test confirmation)

- **C1–C8, C10, C12, C13, C14:** each AC has at least one explicit assertion
  on the state mutation it pins; mutating the matching guard or arithmetic
  in `trading_service.py` would flip the test to RED. C11 cash/fund deltas
  are pinned to exact values (`locked_cash=400`, `locked_fund=600`,
  `new_cash=0.00`, `new_fund=1400.00`; on cover: `released_cash=200`,
  `released_fund=300`, `pnl=100`). C12 numerically asserts
  `(10*80 + 10*100)/20 == 90.00` on both long and short top-up. C13
  asserts `TARGET not in after.long_positions` / `... short_positions`
  (record gone, not just zero shares).
- **C9 — partial gap (flagged below).** WITHIN-cooldown rejection is
  pinned (`test_short_raises_on_cooldown_when_active` +
  `test_cover_raises_on_cooldown_when_active`); buy/sell-not-gated is
  pinned (`test_buy_and_sell_are_not_cooldown_gated`); cooldown row
  written on success is pinned (`test_short_sets_cooldown_after_success`).
  AFTER-cooldown success — `freeze_time(now + cooldown_seconds + 1) →
  short() succeeds` — is **NOT** asserted directly. Functionally safe (the
  fake's `get(now=)` filter + the service's `remaining <= 0 → return`
  both already cover the boundary), but the test asymmetry leaves the
  cooldown-expiry path un-pinned.

## Findings by severity

### CRITICAL — none.

### HIGH — none.

### MEDIUM

- **M1 (C9 cooldown boundary, test-completeness).** No test exercises a
  successful short/cover AFTER `trade_cooldown_seconds` has elapsed; only
  the WITHIN-cooldown rejection path is pinned. A mutation that turned
  `_check_cooldown`'s `remaining <= 0: return` into `remaining < 0:
  return` would leak past the within-boundary tests (off-by-one at the
  exact tick) and is not currently caught. Recommend adding one test:
  freeze at `now`, post a cooldown expiring at
  `now + trade_cooldown_seconds`, advance to
  `now + trade_cooldown_seconds + 1`, assert the next short succeeds.
- **M2 (`ITradeCooldownRepo.get` protocol gap).** The Protocol declares
  `get(guild_id, user_id) -> TradeCooldown | None` but the concrete
  adapter (`adapters/persistence/cooldown_repo.py:69-90`) and the test
  fake (`tests/application/fakes/fake_repos.py:254-266`) both accept
  `*, now: datetime | None = None`. The service calls with no kwarg, and
  the fake/adapter default to wall-clock; under `freeze_time` the in-repo
  default matches the service's frozen `now`, so the remainder arithmetic
  is correct in tests. Production risk = nil: the adapter filters
  `expires_at > now` in SQL AND the service handles
  `remaining <= 0 → return`. This is a typing/contract drift, not a
  runtime hole. Widen the Protocol's `get` signature to match the
  adapter (add the same `*, now: datetime | None = None`) when 8d/8e
  land — they will want the same deterministic-clock seam and a divergent
  Protocol will keep biting.

### LOW

- **L1 (uncovered defensive lines).** The 14 missing lines (190, 261,
  378, 383, 449, 452, 655, 659, 698, 725-735) are all
  defensive-create-on-cold-path branches (idempotent stock/user upsert
  guards, expired-cooldown short-circuit, `update_frozen_shorts` "row
  vanished mid-sweep" guard, `_write_fund_cash` first-time-create
  branch). They are not behind an AC and pin invariants that would
  surface as test failures elsewhere if they regressed. Coverage gate
  passes at 92.53%; leaving them uncovered is acceptable.
- **L2 (price-impact granularity carry-forward).** The work-unit baton
  notes that `apply_trade_impact` quantises a `0.005` impact to `0.01`,
  so a single-share trade still produces an audible price tick. Matches
  the original spec; flag it forward to Phase 9 (ticker/embed builders)
  in case the granularity surprises the user.

## Architecture / security audit

- **Two-user single `locked()`:** four trade methods each acquire ONE
  `self._locks.locked(self._lock_key(actor), self._lock_key(target))`
  call (lines 338, 416, 489, 603). Sweep takes per-account lock
  (line 695). No nesting → no deadlock per Phase 7 rule 3.
- **Composite lock keys:** `_lock_key` returns `f"{self._guild_id}:{user_id}"`
  (line 149) — ADR-0001 / Phase 8a convention preserved.
- **Price-impact RMW atomicity:** every trade calls
  `await self._get_or_create_stock(target_id)` INSIDE the locked block
  (lines 344, 426, 495, 616); `_apply_price_impact_unlocked` reads the
  passed-in `stock`, computes `new_price`, ratchets
  `all_time_high = max(...)`, upserts + appends history in the same
  critical section. No-op short-circuit (`new_price == old_price`) skips
  upsert + history (line 289). Matches the Phase 8b `_rmw_price` digest.
- **Immutability:** every aggregate write uses
  `dataclasses.replace(...)`; new long/short position dicts built with
  `{**buyer.long_positions, target_id: position}` rather than mutation.
- **Domain-math delegation:** trade impact comes from
  `friendex.domain.price_engine.apply_trade_impact`; market gating via
  `friendex.domain.market_hours.is_market_open` / `is_sunday`. No
  hand-rolled math in the service.
- **No new deps:** `pyproject.toml` and `uv.lock` are untouched in the diff.
- **No new `Settings`:** the service reads
  `initial_cash`, `initial_price`, `min_price`, `price_impact_k`,
  `trade_cooldown_seconds`, `short_freeze_minutes`, `market_open`,
  `market_close` — all introduced in earlier phases. None added in 8c.
- **Security:** service composes no raw SQL or shell strings. Discord
  ids flow only into composite lock keys (in-memory dict keys) and
  Decimal-quantised arithmetic. SQL-injection surface lives in the
  SQLAlchemy adapters, not the service.

## Next steps

1. **Address M1 before merge** — add the after-cooldown success boundary
   test (one `freeze_time` advance past `trade_cooldown_seconds`,
   asserting the next short call returns a `ShortResult`). Estimate:
   5 lines in `test_trading_service.py`.
2. **Defer M2 to Phase 8d** — widen `ITradeCooldownRepo.get` Protocol
   to match the adapter's `*, now=` kwarg when 8d touches the same
   port; record as a carry-forward note rather than fixing here, since
   the work unit's "Files to MODIFY: none" envelope explicitly forbade
   editing `interfaces.py`.
3. **Phase 8c digest written** at
   `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md` capturing the
   `TradingService` public surface, the two-user single-`locked()`
   pattern, the read-INSIDE-lock + `_apply_price_impact_unlocked` shape,
   the cooldown-handling convention, and the eventual
   `_cover_internal(force=True)` need for Phase 8f.
4. **Commit + PR (orchestrator step).** Four commits per the work-unit
   baton's commit-boundary guidance; single PR referencing `Refs #2`.

## References

- Spec: `docs/04-migration-plan.md` §Phase 8c (~lines 477–500)
- Issue: #2 (Phase 8c box)
- Work-unit baton: `pass-baton/phase-8c/000-2026-05-25-trading-service-green.md`
- Gate log: `baton-runner/br-2026-05-25-phase-8/gate-phase-8c-iter-1/`
- Phase digest (this iter): `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md`
- Prior digests: `digest-phase-8a.md`, `digest-phase-8b.md`,
  `digest-phase-8-fakes.md`, `digest-phase-7.md`
- Code: `src/friendex/application/trading_service.py`,
  `src/friendex/application/trade_results.py`
- Tests: `tests/application/test_trading_service.py`
