# Phase 8e kickoff — Fund & Daily Services

**Status:** IN PROGRESS. Reading context → RED-first per AC → GREEN → gate.

## Scope (from work-unit contract)

Build `FundService` + `DailyService` + `DailyClaimResult` per
`docs/04-migration-plan.md` §Phase 8e (lines 533–558), with full test suite.

## Plan

1. RED-first per AC (E1–E11), recording RED output incrementally.
2. Add new domain error `AlreadyClaimedToday` to `domain/errors.py` (this is
   a MODIFY that the contract authorises via "DECLARE that in your baton").
   Rationale: contract explicitly says E9 should raise an appropriate
   domain error "like `AlreadyClaimedToday`" and to add it if missing.
3. Implement services using:
   - `fund_math.compute_apy_accrual` for APY math (no hand-rolled math).
   - Per-user `LockManager.locked` composite key `f"{guild}:{user}"`.
   - Read-inside-lock RMW (matches 8b `_rmw_price` pattern).
   - Sweep-per-fund inside `accrue_apy` (mirrors 8c `update_frozen_shorts`).
   - `dataclasses.replace` immutability throughout.
4. `send_to_events` uses `IFundRepo.ensure_events_wallet(guild)` idempotently;
   takes TWO funds in one `locked(actor, "events_wallet")` call.
5. `invest(...)` raises `NotImplementedError` per §Open-Q5 (deferred).
6. Verification: ruff + ruff format + mypy + pytest scoped + full
   tests/application/ regression.

## Day-1-no-penalty interpretation

Spec line 1434: `if now.day != 1: apply_early_withdraw_penalty(user_id)`.
Interpretation: **calendar day 1** (the 1st of the month), NOT "day 1 after
fund creation". Monthly rollover happens on day 1, so withdrawals on that
day are penalty-free; everything else is "early".

This contradicts the work-unit's hint about "first day after fund was
created" — going with the actual spec (day 1 of month) which matches the
monthly accrual model.

## Files (planned)

NEW:
- `src/friendex/application/fund_service.py`
- `src/friendex/application/daily_service.py`
- `src/friendex/application/daily_result.py`
- `tests/application/test_fund_service.py`
- `tests/application/test_daily_service.py`

MODIFY (declared — authorised by contract for adding missing domain errors):
- `src/friendex/domain/errors.py` — add `AlreadyClaimedToday`.

## Carry-forward notes referenced

- 8d digest: read/mutating split, per-user composite lock key.
- 8c digest: lock-key shape, sweep-per-account inside loop.
- 8b digest: read-inside-lock RMW atomicity.
- 8-fakes digest: `FakeFundRepo.ensure_events_wallet` idempotency.
- p4p5 phase-4 digest: `compute_apy_accrual(balance, apy, "monthly")` is
  the canonical APY helper. Period is "monthly" since accrual is invoked
  from the monthly rollover task.

## RED captures (will populate as tests run)

(pending)
