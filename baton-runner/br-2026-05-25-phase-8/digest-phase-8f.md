# Phase 8f exit digest — `LiquidationService` + `DisciplineService`

Source: branch `feat/phase-8f-liq-disc` (uncommitted; manager owns commits).
Phases 4 + 7 + 8a + 8b + 8c + 8-fakes digests authoritative for layers
below. Gate green; 92.68% aggregate cov on `src/friendex/application/`
(>=85%); 531 pytest total (+9 from 522 baseline), 180 in
`tests/application/`.

## Public surface

```python
# application/liquidation_events.py — frozen DTO
@dataclass(frozen=True)
class LiquidationEvent:
    holder_id: str; target_id: str; shares: int
    entry_price: Decimal; exit_price: Decimal
    collateral_returned: Decimal; pnl: Decimal
    timestamp: datetime

# application/liquidation_service.py
class LiquidationService:
    def __init__(self, *, guild_id, user_repo, price_repo, fund_repo,
                 cooldown_repo, lock_manager, settings,
                 trading_service: TradingService) -> None
    async def check_and_liquidate_shorts(self, now) -> list[LiquidationEvent]

# application/discipline_service.py
class DisciplineService:
    def __init__(self, *, guild_id, user_repo, price_repo,
                 lock_manager, settings) -> None
    async def apply_discipline_penalty(self, user_id,
                                       reason: Literal["timeout","ban"]
                                       ) -> DisciplineEvent

@dataclass(frozen=True)
class DisciplineEvent:
    user_id: str; reason: DisciplineReason
    old_price: Decimal; new_price: Decimal; timestamp: datetime

# trading_service.py — NEW private helper (the only public-surface delta)
class TradingService:
    async def _cover_internal(self, coverer_id, target_id, shares,
                              *, force: bool) -> CoverResult
    # public cover() unchanged in signature; now delegates to _cover_internal
```

## Locking contract (declared per work-unit hint — option (a))

`TradingService._cover_internal` does **NOT** acquire `locked()`; the caller
holds them. Two callers:

1. Public `TradingService.cover()` — does validate + market-open +
   `_check_cooldown` + `locked(coverer, target)` then calls
   `_cover_internal(force=False)`. Cooldown is set AFTER the lock release
   on success.
2. `LiquidationService._maybe_liquidate` — acquires
   `locked(holder, target)` ONCE per candidate, re-reads the account +
   stock INSIDE the lock, then calls `_cover_internal(force=True)`. No
   cooldown set (system action, not user-initiated).

Rationale: the `LockManager` is non-reentrant (Phase 7 digest). If
`_cover_internal` re-acquired the locks itself, calling it from inside the
liquidation's outer `locked()` would deadlock. Pushing the lock to the
caller keeps the inside-lock RMW body identical between the two paths.

## Conventions Phase 9 + 10 + 12 MUST honour

1. **`force=` does NOT leak to the public API.** `TradingService.cover()`'s
   signature is unchanged; only the new private `_cover_internal` carries
   the flag. The Phase 9 liquidation task and any other system caller go
   through `LiquidationService`, not directly to `_cover_internal`.
2. **Threshold inclusive at `>=`** (F2). Compare
   `stock.current >= short.entry_price * settings.liquidation_threshold`
   (Decimal-quantised). 149% no-op, 150% liquidate (the boundary lands on
   the at-threshold side, mirroring the spec's "auto-cover at 150%").
3. **Pre-lock candidate scan, inside-lock re-read.** The sweep enumerates
   targets from a pre-lock snapshot of each account's `short_positions`,
   then re-reads inside the lock — silently skipping when the position has
   raced to a manual cover or the price has dropped back below the
   threshold. Mirrors `update_frozen_shorts` (8c).
4. **DisciplineService floor uses flat `max(proposed, min_price)`** — same
   shape as `apply_inactivity_decay`. NOT `apply_floor_stall` (whose
   attenuation would silently soften a 17% penalty for cratered stocks).
   The discipline penalty is by spec a flat percentage hit, full stop.
5. **DisciplineService writes follow Phase-8b `_rmw_price` shape:** read
   stock INSIDE lock → compute → no-op short-circuit on equal → upsert +
   `append_history` + `all_time_high = max(...)` ratchet inside the same
   critical section. No new history row when the stock was already at
   floor (silent no-op).
6. **No new `Settings`.** `liquidation_threshold` (=1.5) and
   `discipline_penalty` (=0.17) and `min_price` (=70.0) already exist.
7. **Opt-OUT does NOT exempt discipline (F8).** `opt_in` only gates being
   *traded into*; discipline applies to the user's own stock regardless.
8. **`LiquidationEvent.timestamp` comes from the caller's `now`** (passed
   to `check_and_liquidate_shorts`), not from `datetime.now()` inside the
   service — keeps the notifier's attribution stable across retries.

## Verification

```
ruff check src tests   → All checks passed!
ruff format --check    → 84 files already formatted
mypy src/friendex      → Success: no issues found in 43 source files
pytest tests/application/ -v --cov=src/friendex/application
                       → 180 passed; coverage 92.68% (gate 85%)
pytest (full repo)     → 531 passed (+9 from 8e's 522)
```

Coverage on the new modules:
- `liquidation_events.py` — 100%
- `liquidation_service.py` — 91%
- `discipline_service.py` — 92%
- `trading_service.py` — 94% (after the `_cover_internal` refactor; no
  regression versus 8c's 92.53%)

## RED-first evidence

All 8 ACs were RED-first verified by pre-implementation `pytest` runs —
imports of `discipline_service`, `liquidation_service`,
`liquidation_events` all `ModuleNotFoundError` before the implementation
modules were created. See kickoff baton
`pass-baton/phase-8f/000-2026-05-25-phase-8f-kickoff.md`.

## Deferred / carry-forward

- Carry-forward unchanged from 8e: 8c M2 (`ITradeCooldownRepo.get` missing
  `now=` kwarg); 8b M1 (`activity_tick_k = 0.5` placeholder).
- No new carry-forward introduced by 8f.

## For Phase 9 (`LiquidationTask` / `freeze_check_task`) to reuse

- `LiquidationService.check_and_liquidate_shorts(now)` returns the per-
  event payload directly — the task wraps the call and pipes each event
  into the injected Discord-notification callback (no `discord` import in
  the task itself).
- `DisciplineService.apply_discipline_penalty(user_id, reason)` is the
  entry-point for the Phase 12 `on_member_update` listener (timeout/ban
  branch); the listener simply translates the Discord event to the reason
  literal and calls the service.
