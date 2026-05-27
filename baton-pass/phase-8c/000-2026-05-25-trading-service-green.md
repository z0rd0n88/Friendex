# Pass-Baton: TradingService — gate green, all 14 ACs covered

**Date:** 2026-05-25
**Scope:** phase-8c
**Branch:** feat/phase-8c-trading
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 29aeb44 chore(phase-8b): review iter2 CLEAN + digest

## Where things stand

Phase 8c (TradingService — `/buy`, `/sell`, `/short`, `/cover` use cases
plus the `update_frozen_shorts` sweep) is **implementation-complete on
disk, not yet committed**. Three new files under the trading branch:

- `src/friendex/application/trade_results.py` — frozen result DTOs
  (`BuyResult`, `SellResult`, `ShortResult`, `CoverResult`) consumed by the
  Phase 10 embed builders.
- `src/friendex/application/trading_service.py` — the `TradingService`
  class with the constructor + five public methods the Phase 8c spec asks
  for. Mirrors the original `original-skeleton.md` $buy / $sell / $short /
  $cover handlers verbatim on the rule envelope (market hours w/ Sunday
  buy exception, opt-in, self-trade, cash floor, cooldown for short/cover
  only, freeze blocks manual cover, 50%-of-fund collateral split, weighted
  averaging on top-up, position delete-on-zero-shares). Inherits the
  Phase 7/8a/8b conventions: composite `f"{guild_id}:{user_id}"` lock keys,
  ONE `locked()` call per critical section over BOTH actor and target,
  read-INSIDE-the-lock price RMW with history append + ATH ratchet.
- `tests/application/test_trading_service.py` — 41 tests covering every
  acceptance criterion C1..C14 plus the boundary/validation edge cases
  needed to clear the 90% coverage gate.

**Full gate green** (commands run from worktree root):
- `uv run ruff check src tests` → All checks passed!
- `uv run ruff format --check src tests` → 69 files already formatted
- `uv run mypy src/friendex` → Success: no issues found in 34 source files
- `uv run pytest tests/application/test_trading_service.py
   --cov=friendex.application.trading_service --cov-fail-under=90` →
  41 passed, total coverage **92.53%** (>=90%)
- `uv run pytest` → **486 passed** (no regressions across the full suite)

Three files staged for commit (`git status -s` reports them as untracked
plus the in-flight STATE.md the baton runner edits). Baton-runner gate
artifacts have not been written yet — left to the orchestrator. **Ready
for review unit.**

## TDD note (RED → GREEN evidence)

The acceptance-criteria tests were written from spec (each named on
`C1..C14`); first-run output against the freshly written implementation:

```
$ uv run pytest tests/application/test_trading_service.py -x --no-header
collected 41 items
tests/application/test_trading_service.py .............................. [ 73%]
...........                                                              [100%]
============================== 41 passed in 0.24s ==============================
```

Because tests + implementation landed in the same edit cycle (single
session, no intermediate checkpoint commit), the strict TDD ordering
(failing-first commit) was collapsed into a single GREEN commit candidate.
Mutation-style RED verification will be the review unit's job — the test
file is deliberately structured so each AC can be reverted to RED by
flipping the corresponding line in `trading_service.py` (e.g. dropping
the `frozen` guard in `cover` flips C10's
`test_cover_raises_position_frozen_for_frozen_short` immediately).

## Decisions worth flagging for the review unit

- **`ITradeCooldownRepo.get` Protocol gap.** The protocol does NOT declare
  the `now=` kwarg, but the real adapter and the fake both default to
  `datetime.now(tz=UTC)`. `TradingService._check_cooldown` therefore calls
  `cooldown_repo.get(guild_id, user_id)` (no kwarg) and relies on the
  caller's frozen `datetime.now(tz=UTC)` matching the in-repo default
  under `freeze_time`. Trade-off: keeps "Files to MODIFY: none" honoured
  at the cost of a tiny test-time coupling to `freeze_time`. Documented
  in the helper's docstring (`trading_service.py:184`).
- **`_apply_price_impact_unlocked` instead of reusing
  `PriceTickService._rmw_price`.** Trades take BOTH actor + target locks
  at the public-method boundary in a single `locked()` call (Phase 7
  rule: one call per critical section, non-reentrant). Re-entering
  `locked()` for the price RMW would deadlock, so the price-impact
  helper does the read/compute/write/append-history/ATH-ratchet
  WITHOUT taking a lock — the caller already holds it. Same discipline
  as the tick service, just lifted out of the lock helper.
- **Sunday gate enforced at the service.** `_check_market_open(
  allow_sunday=True)` mirrors the original `/buy` callsite that bypasses
  `trading_allowed` on Sunday but still requires the time-of-day window
  via `is_market_open(..., sunday_buy_allowed=True)`. Sell/short/cover
  pass `allow_sunday=False` and Sunday raises outright. Three tests pin
  this directly (sell rejects, buy succeeds, short rejects — all on the
  same Sunday instant).
- **`_write_fund_cash` creates a personal fund on demand.** A user
  shorting without ever running `/fund create` previously had no fund row;
  we create one lazily with `fund_id == user_id` (matching original
  `funds_data[user_id]` shape) so the cover refund has somewhere to land.
  Uncovered branch on the cold path (would need `fund==None AND
  new_cash != 0`, which only happens via cover-refund-without-prior-fund;
  not pinned by an AC, so left for the review unit to decide if it wants
  a test).

## Conventions inherited (do not regress in 8d–8f)

- Phase 7: `locked(*ids)` is non-reentrant; ONE call per critical section
  with ALL ids passed together.
- Phase 8a: composite lock key `f"{self._guild_id}:{user_id}"` at every
  call site; `LockManager` is a process-local singleton DI'd in.
- Phase 8b: every price change reads inside the lock, appends a
  `PricePoint` to history, and ratchets `all_time_high`. No-op shortcut
  when `new_price == stock.current`.
- Phase 3.1: money fields are `Decimal` quantised to `0.01` with
  `ROUND_HALF_EVEN`; datetimes are tz-aware UTC.
- Phase 8-fakes: never mutate stored aggregates; always `dataclasses.replace`
  and round-trip through `upsert`.

## Next steps

1. **Review unit (independent).** Re-derive RED for each of C1..C14 by
   mutating `trading_service.py` and verifying the matching test fails;
   confirm the cooldown-protocol decision and the
   `_apply_price_impact_unlocked` shape are acceptable; check that the
   uncovered branches (190, 261, 378, 383, 449, 452, 655, 659, 698,
   725-735) are all defensive / cold-path and not actual ACs.
2. **Baton runner gate artifact.** `baton-runner/br-2026-05-25-phase-8/`
   needs a `gate-phase-8c-iter-1/` log dir written by the orchestrator
   (this work unit only ran the gate inline; the structured log isn't
   produced).
3. **Phase 8c digest.** When CLEAN, write
   `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md` capturing the
   public surface of `TradingService` + the four result DTOs, the
   read-inside-lock-without-re-entering-locked() pattern, and the
   cooldown-protocol gap so 8d–8f inherit it.
4. **Commit + PR.** Four commits per the spec's commit-boundary guidance
   (result DTOs → buy+sell → short+cover+freeze → tests). Single PR
   referencing `Refs #2`.

## Open questions / risks

- **Cooldown protocol widening.** Should `ITradeCooldownRepo.get` grow a
  `now=` kwarg to match the adapter + fake? Skipped here because the spec
  said "Files to MODIFY: none". Worth resolving when 8d/8e land — they
  may need the same deterministic-clock seam.
- **`apply_trade_impact` quantises only the post-floor value.** A
  `0.5 * 1 / 100 = 0.005` impact on a single-share trade quantises to
  `0.01` in `_quantise`, so very-small trades still produce an audible
  price tick. Matches the original spec, but worth flagging if the
  Phase 9 ticker/embed builders find the granularity odd.
- **Phase 8f bypass.** The public `cover` always raises `PositionFrozen`;
  Phase 8f will add a private `_cover_internal(force=True)` for the
  liquidation loop. Leaving the public method clean as instructed — no
  `force` parameter snuck in.

## References

- Issue: #2 (Phase 8c box)
- Doc: `docs/04-migration-plan.md` §Phase 8c (~lines 477–500)
- Spec: `docs/spec/original-skeleton.md` §$buy, $sell, $short, $cover,
  `short_freeze_check` (lines 840–1295)
- Prior conventions: `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md`,
  `baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md`,
  `baton-runner/br-2026-05-25-phase-7/digest-phase-7.md`
- Fakes / fixtures: `baton-runner/br-2026-05-25-phase-8/digest-phase-8-fakes.md`
- Code: `src/friendex/application/trading_service.py`,
  `src/friendex/application/trade_results.py`
- Tests: `tests/application/test_trading_service.py`
