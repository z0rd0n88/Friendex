# Phase 8e digest — Fund + Daily Services (CLEAN)

`feat/phase-8e-fund-daily` @ `1258f85`. Gate green (522 pytest, ruff, format,
mypy). Review iter-1 CLEAN; 0 CRITICAL/HIGH/MEDIUM, 2 LOW (zero-balance fund
side-effect on insufficient-withdraw; seeded account uses `datetime.now`).

## Public surface
```python
class FundService:  # application/fund_service.py — ctor takes
    # (guild_id, user_repo, fund_repo, penalty_repo, lock_manager, settings)
    async def fund_info(user_id) -> HedgeFund | None              # lockless
    async def create_or_rename(user_id, name=None) -> HedgeFund
    async def withdraw(user_id, amount: Decimal, now: datetime) -> None
    async def send_to_events(user_id, amount: Decimal) -> None    # 2-key locked
    async def accrue_apy(now: datetime) -> None                   # sweep-per-fund
    async def invest(...) -> None                                 # NotImplementedError
class DailyService:  # application/daily_service.py
    async def claim_daily(user_id, now: datetime) -> DailyClaimResult
@dataclass(frozen=True)  # application/daily_result.py
class DailyClaimResult: user_id; streak: int; reward: Decimal;
    is_streak_bonus: bool; new_cash_balance: Decimal; claim_date: datetime
```

## Domain addition
`AlreadyClaimedToday(DomainError)` in `domain/errors.py:131-146`
(`seconds_remaining`; copy "...Next claim in {h}h {m}m.").

## Conventions
Composite lock key `f"{guild_id}:{user_id}"`; read-inside-lock RMW; `accrue_apy`
per-fund `locked()` *inside* the loop (events_wallet skipped); `send_to_events`
one `locked(user, "events_wallet")`; mutations via `dataclasses.replace`; APY
math delegated to `fund_math`.

## Game rules pinned + 8f/Phase 9 hooks
Day-1 = no penalty (L1434); penalty APR stacks for `penalty_duration_days`
(L614); `send_to_events` exempt (L1475); streak resets to **0** after day-7
bonus (L980); events_wallet never accrues. `LiquidationService` mirrors
sweep-per-aggregate-inside-loop; `MonthlyRolloverTask` calls `accrue_apy(now)`
day-1 hour-0 (retry-safe); `DailyCog` catches `AlreadyClaimedToday`.
Carry-forward unchanged (8c M2, 8b M1).
