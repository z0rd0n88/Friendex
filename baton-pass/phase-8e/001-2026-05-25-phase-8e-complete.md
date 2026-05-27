# Phase 8e exit — Fund & Daily Services (CLEAN)

**Branch:** baton-runner worktree `br-2026-05-25-phase-8`.
**Files:** 5 new + 1 modify; 22 new tests; 522 total pytest pass; ruff +
ruff-format + mypy clean.

## Verification

```
uv run ruff check src tests       -> All checks passed!
uv run ruff format --check src tests -> 79 files already formatted
uv run mypy src/friendex          -> Success: no issues found in 40 source files
uv run pytest tests/application/test_fund_service.py tests/application/test_daily_service.py -v
                                  -> 22 passed (E1-E11 + boundary)
uv run pytest                     -> 522 passed
```

## RED captures (RED-first per AC)

Initial collection (services + new domain error absent):
```
ModuleNotFoundError: No module named 'friendex.application.fund_service'
ModuleNotFoundError: No module named 'friendex.application.daily_service'
```
The `from friendex.domain.errors import AlreadyClaimedToday` line in
`test_daily_service.py` would have produced an `ImportError` once the service
modules existed; the symbol was introduced as part of the GREEN step.
All 22 tests transitioned RED -> GREEN with no test edits after first run.

## AC mapping

| AC  | Test                                                         | Status |
|-----|--------------------------------------------------------------|--------|
| E1  | `test_e1_create_or_rename_creates_default_named_fund_when_absent` + `test_e1_create_or_rename_accepts_provided_name` | PASS |
| E2  | `test_e2_create_or_rename_renames_existing_fund`             | PASS   |
| E3  | `test_e3_withdraw_on_day_1_does_not_apply_penalty`           | PASS   |
| E4  | `test_e4_withdraw_mid_month_applies_penalty` + `test_e4_withdraw_mid_month_stacks_existing_penalty` | PASS |
| E5  | `test_e5_send_to_events_transfers_and_skips_penalty`         | PASS   |
| E6  | `test_e6_accrue_apy_credits_monthly_amount_to_each_personal_fund` + `test_accrue_apy_respects_active_penalty` | PASS |
| E7  | `test_e7_invest_raises_not_implemented`                       | PASS   |
| E8  | `test_e8_first_claim_credits_daily_reward`                    | PASS   |
| E9  | `test_e9_second_claim_same_day_raises`                        | PASS   |
| E10 | `test_e10_next_day_claim_continues_streak`                    | PASS   |
| E11 | `test_e11_day_seven_streak_bonus_and_reset`                   | PASS   |

Bonus boundary tests (not in the AC list, kept for safety):
`test_withdraw_zero_or_negative_raises_invalid_amount`,
`test_withdraw_more_than_balance_raises`,
`test_send_to_events_zero_or_negative_raises`,
`test_send_to_events_insufficient_balance_raises`,
`test_skipping_a_day_resets_streak_to_1`,
`test_claim_creates_account_for_unknown_user`,
`test_fund_info_returns_none_when_absent`,
`test_fund_info_returns_the_stored_fund`.

## DECLARED additions (per the contract's "DECLARE in your baton")

1. **`AlreadyClaimedToday(DomainError)`** added to
   `src/friendex/domain/errors.py`. The contract explicitly authorises this
   ("`AlreadyClaimedToday` - add it to `domain/errors.py` if it doesn't
   exist and DECLARE that in your baton"). Carries `seconds_remaining`
   and renders the original spec's "Next claim in {h}h {m}m" copy.

2. **No new `Settings` fields.** All tunables (`daily_reward`,
   `streak_bonus`, `early_withdraw_penalty`, `penalty_duration_days`,
   `hedge_fund_base_apy`, `initial_cash`) already exist on `Settings`.
   No `.env` changes needed.

3. **No new dependencies.**

## Public surface

```python
# fund_service.py
class FundService:
    def __init__(self, *, guild_id, user_repo, fund_repo, penalty_repo,
                 lock_manager, settings): ...
    async def fund_info(user_id) -> HedgeFund | None              # lockless
    async def create_or_rename(user_id, name=None) -> HedgeFund   # locked
    async def withdraw(user_id, amount, now) -> None              # locked
    async def send_to_events(user_id, amount) -> None             # 2-key locked
    async def accrue_apy(now) -> None                             # sweep-per-fund locked
    async def invest(investor_id, fund_id, amount) -> None        # raises NotImplementedError

# daily_service.py
class DailyService:
    def __init__(self, *, guild_id, user_repo, lock_manager, settings): ...
    async def claim_daily(user_id, now) -> DailyClaimResult       # locked

# daily_result.py
@dataclass(frozen=True)
class DailyClaimResult:
    user_id: str
    streak: int
    reward: Decimal
    is_streak_bonus: bool
    new_cash_balance: Decimal
    claim_date: datetime
```

## Conventions honoured (Phase 8 digests)

- **Composite lock key** `f"{guild_id}:{user_id}"` via `_lock_key` (8a/8c/8d).
- **Read-inside-lock RMW** (8b `_rmw_price` shape) — every mutating method
  re-`get`s the aggregate inside the critical section.
- **Sweep-per-fund** in `accrue_apy` mirrors 8c `update_frozen_shorts`
  (per-account `locked(...)` inside the loop, never wrap the loop).
- **Two-key single `locked()`** in `send_to_events` for `(user, events_wallet)`
  — mirrors 8c trade's `locked(actor, target)` pattern.
- **Immutable upserts via `dataclasses.replace`** (8d/8a/8c).
- **`fund_math.compute_apy_accrual` + `compute_effective_apy`** — APY math is
  fully delegated to Phase 4 domain helpers; service is a pure orchestrator.
- **`IFundRepo.ensure_events_wallet` idempotency** (8-fakes digest) — `send_to_events`
  always calls it inside the critical section so the wallet is created on first
  use and read-modified-written from there.
- **Decimal + UTC invariants** preserved end-to-end (Phase 3.1):
  `Decimal(str(settings.x))` for any float-sourced rate / amount; `now`
  threaded as tz-aware UTC `datetime`.

## Day-1-no-penalty interpretation (recorded)

The work-unit hint suggested "first day after the fund was created" but the
actual original spec (`docs/spec/original-skeleton.md:1434`) reads:

```python
if now.day != 1:  # treat anything not on the 1st as "early"
    apply_early_withdraw_penalty(user_id)
```

`now.day` is the **calendar day-of-month**. Implementation follows the spec
verbatim — withdrawals on the 1st of any month skip the penalty (matching
the monthly-rollover cadence in `MonthlyRolloverTask`). E3 tests this with
`datetime(2026, 6, 1, ...)`.

## Conventions for 8f to reuse

- **Two-key single `locked()`** pattern in `send_to_events` is the template
  for any cross-aggregate transfer (e.g. liquidation cover involves
  `(holder, target)`).
- **Sweep-per-aggregate-inside-loop** in `accrue_apy` is the template for
  `LiquidationService.check_and_liquidate_shorts` walking every short.
- **`HedgeFund` cash_balance RMW pattern** is now established —
  `LiquidationService` releasing collateral back to the fund follows the
  same `replace(fund, cash_balance=...)` shape.

## Carry-forward / non-blocking

None new. Pre-existing carry-forwards from earlier digests remain:
- M2 from 8c (`ITradeCooldownRepo.get` missing `now=` kwarg) — not touched here.
- Activity-K calibration (M1 from 8b) — user-pending.
- `Stock.high_24h`/`low_24h` model-shrink — separate unit.
