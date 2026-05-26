# Phase 8f exit digest — Liquidation + Discipline (review CLEAN)

`feat/phase-8f-liq-disc` @ c84ca79. Gate green: 180 application pytest (+9),
92.68% application cov (≥85%), 8c suite 41/41 (no regression).

## Public surface (Decimal $, UTC datetime)

```python
@dataclass(frozen=True)
class LiquidationEvent: holder_id; target_id; shares; entry_price; exit_price; collateral_returned; pnl; timestamp

class LiquidationService(*, guild_id, user_repo, price_repo, fund_repo, cooldown_repo, lock_manager, settings, trading_service):
    async def check_and_liquidate_shorts(now) -> list[LiquidationEvent]

class DisciplineService(*, guild_id, user_repo, price_repo, lock_manager, settings):
    async def apply_discipline_penalty(user_id, reason: Literal["timeout","ban"]) -> DisciplineEvent
@dataclass(frozen=True)
class DisciplineEvent: user_id; reason; old_price; new_price; timestamp

# trading_service.py — only public delta:
async def _cover_internal(coverer_id, target_id, shares, *, force: bool)
# public cover() signature unchanged; delegates to _cover_internal(force=False)
```

## `_cover_internal` contract (design (a))

Private, non-reentrant-safe: **does NOT acquire `locked()` — caller MUST hold
`locked(coverer, target)`**. `force=True` bypasses `PositionFrozen`. Cooldown
set by `cover()` outside the lock; liquidation sets no cooldown.

## For Phase 9 / Phase 12 consumers

- 9 `LiquidationTask`: 5-min `await check_and_liquidate_shorts(now)`; pipe each event into the Discord notifier (no `discord` import in service).
- 9/12 `on_member_update` → `apply_discipline_penalty(user_id, "timeout"|"ban")`.
- Threshold `>=` (149% no-op, exactly 150% liquidates). Floor is flat `max(proposed, min_price)`. No new Settings; no new deps.
