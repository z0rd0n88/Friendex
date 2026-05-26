# Phase 8d digest — Portfolio + Stats services (read-only)

**Status:** CLEAN. 500 pytest, ruff/format/mypy clean. PR pending orchestrator.

## Surface

`application/snapshot_models.py` — four `@dataclass(frozen=True)` DTOs:
- `PortfolioSnapshot(user_id, cash_balance, net_worth, month_start_net_worth, fund_balance, long_positions, short_positions)`
- `TrendingEntry(rank, user_id, score, current_price)` (rank 1-indexed)
- `PriceStats(user_id, current, high_24h, low_24h, all_time_high)` (high/low **computed**, not stored)
- `UserStats(user_id, trending_score, engagement_tier, last_activity)` (tier ∈ Elite/High/Medium/Low)

`application/portfolio_service.py` — `PortfolioService(*, guild_id, user_repo, price_repo, fund_repo, lock_manager, settings)`:
- `async calculate_net_worth(user_id) -> Decimal | None` *(lockless)*
- `async portfolio_snapshot(user_id) -> PortfolioSnapshot | None` *(lockless)*
- `async capture_month_start_net_worth() -> None` *(only mutating method)*

`application/stats_service.py` — `StatsService(*, guild_id, user_repo, price_repo, settings)` (no fund_repo, no lock_manager):
- `async trending_snapshot(limit: int = 15) -> list[TrendingEntry]` *(lockless)*
- `async user_stats(user_id) -> UserStats | None` *(lockless)*
- `async get_price_stats(user_id) -> PriceStats | None` *(lockless)*

## Conventions (reusable for 8e/8f)

1. **Per-user `locked()` INSIDE the loop, never wrapping it.** `capture_month_start_net_worth` walks `list_all(guild)`, takes `async with self._locks.locked(self._lock_key(user_id))`, re-`get`s + recomputes + `upsert`s inside the critical section. Mirrors 8c `update_frozen_shorts`.
2. **Composite lock key** `f"{guild_id}:{user_id}"` via `_lock_key()` — matches 8a/8c.
3. **Domain math 100% delegated** to `fund_math.compute_net_worth` / `activity.calculate_trending_score` / `activity.get_engagement_tier`. Only service-local math is `max()/min()` over the 24h price window.
4. **24h window** — `since = datetime.now(tz=UTC) - timedelta(hours=24)` → `IPriceRepo.get_history(..., since=since)`; repo filter is **inclusive `>=`** (Phase-6c + 8-fakes). Empty-window fallback: `high = low = stock.current`.
5. **Immutable upserts** via `dataclasses.replace`; never attribute assignment.
6. **Read paths take NO lock.** Best-effort.
7. **Read-models distinct from `domain.models`** — embed builders consume them as-is.

## For 8e/8f to reuse

Both snapshot DTOs; `_personal_fund_cash` / `_current_price` helper shape (extract if duplicated); per-user-`locked()`-inside-loop pattern; `fund_id == user_id` personal-fund lookup.

## Carry-forward (deferred)

M2 from 8c (`ITradeCooldownRepo.get` missing `now=` kwarg); `Stock.high_24h`/`low_24h` stored fields now unused by reads (computed dynamically) — model-shrink is a separate unit.
