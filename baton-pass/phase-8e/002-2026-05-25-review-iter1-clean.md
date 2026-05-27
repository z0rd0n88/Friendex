# Pass-Baton: Phase 8e review iter1 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-8e
**Branch:** feat/phase-8e-fund-daily
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 1258f85 feat(phase-8e): fund + daily services

## Where things stand

Phase 8e (`FundService` + `DailyService` + `AlreadyClaimedToday` domain error)
is review-CLEAN. Gate `baton-runner/br-2026-05-25-phase-8/gate-phase-8e-iter-1/`
passed all four checks (pytest 522 passed, ruff-check, ruff-format, mypy).
Static + behavioural review against E1–E11 found no CRITICAL or HIGH issues;
two LOW notes recorded below (non-blocking). Ready to write phase-exit digest.

## Findings by severity

### CRITICAL — none

### HIGH — none

Architectural invariants verified:

- **Composite lock key** — every mutating method (`create_or_rename`,
  `withdraw`, `send_to_events`, the per-fund critical section inside
  `accrue_apy`, `claim_daily`) uses `self._lock_key(user_id)` =
  `f"{self._guild_id}:{user_id}"`. No bare-user-id keys remain (would have
  re-introduced the 8a HIGH).
- **Read-inside-lock RMW** — `withdraw` re-reads fund + account inside the
  lock (`fund_service.py:220-221`), `send_to_events` re-reads fund + wallet
  inside the lock (`fund_service.py:289-290`), `accrue_apy` re-`get`s each
  fund inside the per-fund lock (`fund_service.py:332`), `claim_daily`
  re-reads account inside the lock (`daily_service.py:171`). Matches the
  8b `_rmw_price` discipline.
- **Sweep-per-aggregate-inside-loop** — `accrue_apy` takes the lock
  *inside* the `for fund in funds:` loop, one fund at a time (line 331).
  Mirrors 8c `update_frozen_shorts`.
- **Two-key single `locked()`** — `send_to_events` passes both composite
  keys to one `locked(user, events_wallet)` call (lines 285-288), avoiding
  the non-reentrant nested-lock deadlock from the Phase 7 digest.
- **Immutability** — every mutation goes through `dataclasses.replace`;
  no in-place writes to `HedgeFund`, `UserAccount`, `FundPenalty`, or
  `DailyProgress`.
- **Domain-math delegation** — APY accrual is fully delegated to
  `fund_math.compute_apy_accrual` + `compute_effective_apy`
  (`fund_service.py:336-341`). Verified: 1200 × 0.0125 = 15.00 and
  600 × 0.0125 = 7.50 match the test assertions (E6), and the
  penalty-active branch matches 1200 × (0.10/12) = 10.00. No hand-rolled
  compounding.
- **`AlreadyClaimedToday`** — added to `domain/errors.py:131-146` as a
  `DomainError` subclass with `seconds_remaining` and the original
  "Next claim in {h}h {m}m" copy. Taxonomy correct (user-facing rule
  violation, not infrastructure). Test imports and asserts it directly.
- **`invest()` scaffold** — raises `NotImplementedError` with a clear
  message pointing at §Open-Q5 (`fund_service.py:365-368`). E7 asserts it
  via `pytest.raises(NotImplementedError)`.

### MEDIUM — none

### LOW (non-blocking)

- **L1 — `withdraw` creates a zero-balance fund on the insufficient-balance
  path.** `_get_or_create_fund` (line 220) upserts a new fund *before* the
  balance check (lines 230-233). If a user with no fund calls `/fund
  withdraw 100`, an empty fund is now persisted and the call raises
  `FundInsufficientBalance`. This is a benign side-effect (the
  zero-balance fund is the same shape the next `/fund create` would
  produce), and `send_to_events` shares the same shape, but worth a
  one-line note if a future change tightens the contract.
- **L2 — `DailyService._get_or_create_account` reads `datetime.now(tz=UTC)`
  for the seeded `ActivityBucket.bucket_start` / `last_activity`
  (`daily_service.py:136`) instead of threading the `now` parameter the
  caller passed.** Cosmetic — these fields are not consulted by
  `claim_daily` and the seeded account is upserted again with the
  fresh `daily` state via `replace`. If the spec ever ties bucket_start
  to claim time, thread `now` through.

## AC matrix — load-bearing verification

| AC  | Verdict | Notes |
|-----|---------|-------|
| E1  | LOAD-BEARING | Default-name + custom-name paths both asserted on the returned fund AND the persisted one |
| E2  | LOAD-BEARING | Rename preserves `cash_balance` (asserts 2500.00 round-trip), not only name |
| E3  | LOAD-BEARING | Asserts `penalty_after is None` AND post-withdraw balances on 2026-06-01 |
| E4  | LOAD-BEARING | Two tests: fresh penalty asserts exact 0.05 APR + `penalty_until` exact instant; stacking test asserts 0.05 + 0.05 = 0.10 |
| E5  | LOAD-BEARING | Asserts events_wallet receives the gross 150.00, fund debits to 350.00, AND `penalty_after is None` |
| E6  | LOAD-BEARING | Asserts exact 1215.00 / 607.50 amounts (proves `compute_apy_accrual` was used), AND events_wallet untouched at 0.00; bonus test asserts penalty-affected accrual 1210.00 |
| E7  | LOAD-BEARING | `pytest.raises(NotImplementedError)` against the method |
| E8  | LOAD-BEARING | Asserts `DailyClaimResult` shape + persisted account state |
| E9  | LOAD-BEARING | Asserts both the raise AND that cash + streak are untouched |
| E10 | LOAD-BEARING | Asserts streak == 2, no bonus, reward unchanged |
| E11 | LOAD-BEARING | Loops 7 actual claims with stepping `now`, asserts day-7 bonus + reset-to-0 + cumulative cash balance (proves the 6-day buildup, not an isolated day-7 stub) |

## Declared additions

- `AlreadyClaimedToday(DomainError)` added per the work-unit's authorisation.
- No new `Settings` fields.
- No new dependencies (`pyproject.toml`/`uv.lock` untouched per `git diff`).

## Next steps

1. Write phase-exit digest at
   `baton-runner/br-2026-05-25-phase-8/digest-phase-8e.md` (the next action
   by this review unit).
2. Hand back to baton-runner; next phase is 8f (`LiquidationService`).
3. Optionally address L1/L2 in a follow-up commit on the same branch
   (non-blocking; both are cosmetic).

## References

- Work baton: `baton-pass/phase-8e/001-2026-05-25-phase-8e-complete.md`
- Gate logs: `baton-runner/br-2026-05-25-phase-8/gate-phase-8e-iter-1/`
- Spec: `docs/04-migration-plan.md` §"Phase 8e — Fund & Daily Services"
- Original-bot spec: `docs/spec/original-skeleton.md:941-986` (daily),
  `:1359-1484` (fund), `:614` (penalty stacking)
- Code:
  - `src/friendex/application/fund_service.py`
  - `src/friendex/application/daily_service.py`
  - `src/friendex/application/daily_result.py`
  - `src/friendex/domain/errors.py:131-146`
- Tests:
  - `tests/application/test_fund_service.py`
  - `tests/application/test_daily_service.py`
