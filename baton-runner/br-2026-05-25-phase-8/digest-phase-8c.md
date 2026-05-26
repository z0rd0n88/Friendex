# Phase 8c exit digest — `TradingService`

Source: `feat/phase-8c-trading` at HEAD `2965238`. Phases 4 + 7 + 8a + 8b +
8-fakes digests authoritative for layers below. Gate green; 92.53% cov on
`trading_service.py` (>=90%); 486 pytest all pass.

## Public surface

```python
class TradingService:
    def __init__(self, *, guild_id: str, user_repo, price_repo, fund_repo,
                 cooldown_repo, lock_manager: LockManager, settings: Settings) -> None
    async def buy(self, buyer_id, target_id, shares: int) -> BuyResult
    async def sell(self, seller_id, target_id, shares: int) -> SellResult
    async def short(self, shorter_id, target_id, shares: int) -> ShortResult
    async def cover(self, coverer_id, target_id, shares: int) -> CoverResult
    async def update_frozen_shorts(self) -> None
```

Result DTOs (`application/trade_results.py`, all `frozen=True`): `BuyResult`,
`SellResult` (`position_after: LongPosition | None`), `ShortResult`,
`CoverResult` (`position_after: ShortPosition | None`, signed `pnl`, separate
`released_cash`/`released_fund`).

## Conventions 8d-8f MUST honour

1. **Two-user single `locked()` per trade.** `async with
   self._locks.locked(self._lock_key(actor), self._lock_key(target))` — BOTH
   ids in ONE call, never nested (lock is non-reentrant per Phase 7).
   `update_frozen_shorts` is a sweep: per-account `locked(...)` one user at
   a time, never wrap whole loop.
2. **Price RMW INSIDE the trade's lock.** Read `stock` inside the
   `locked()` block, then call `_apply_price_impact_unlocked` which does:
   `apply_trade_impact` compute → no-op short-circuit on equal → `upsert` +
   `append_history` + ratchet `all_time_high = max(...)` — helper does NOT
   re-enter `locked()` (name = contract). Same shape as 8b `_rmw_price`.
3. **Cooldown.** `_check_cooldown` calls `cooldown_repo.get(g, u)` with no
   kwarg; treats `None` OR `remaining <= 0` as cleared. Real adapter + fake
   both default `now=` to wall-clock and filter expired rows; safe under
   `freeze_time`. `ITradeCooldownRepo.get` Protocol still lacks `now=` —
   widen when 8d/8e touch the port (review baton 001 / M2).
4. **Cooldown short+cover ONLY**, set AFTER lock release on success;
   buy/sell never gated. Collateral split:
   `fund_avail = fund_cash * 0.5; locked_cash = min(cash, notional);
   locked_fund = min(fund_avail, notional - locked_cash)`. Recomputed every
   short; released `proportion = shares / existing.shares` on cover.
5. **Position deletion at zero shares.** `del`-ete the dict entry (not
   shares=0); `position_after = None` in result DTO.
6. **Phase 8f bypass NOT here.** Public `cover` always raises
   `PositionFrozen` on frozen. Phase 8f liquidation MUST add a private
   `_cover_internal(force=True)` — do NOT leak `force=` onto public method.
7. **Immutability + Decimal/UTC** preserved end-to-end. No new `Settings`.
