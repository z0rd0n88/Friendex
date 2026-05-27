# Phase 9 exit digest — Background Tasks (review CLEAN)

`feat/phase-9-tasks` @ `fe9f160`. Gate green: 581 pytest (+48 from 533),
100% coverage on `src/friendex/adapters/tasks/`, ruff/format/mypy clean.
No new deps. No domain/application/persistence/config changes.

## Public surface (per `src/friendex/adapters/tasks/__init__.py`)

```python
class BackgroundTask(ABC):
    interval_minutes: int = 0  # override exactly one
    interval_hours: int = 0
    _loop: Any                 # bound by Phase 14 composition layer
    @abstractmethod
    async def _run(self) -> None: ...
    def start(self) -> None
    def stop(self) -> None
    async def _safe_run(self, awaitable: Awaitable[Any]) -> None   # swallows Exception, logs via structlog; BaseException propagates

class ActivityTickTask(*, service_factory, iter_guild_ids)                    # interval_minutes=15  → PriceTickService.activity_price_tick()
class InactivityDecayTask(*, service_factory, iter_guild_ids)                 # interval_minutes=5   → PriceTickService.inactivity_decay_tick()
class FreezeCheckTask(*, service_factory, iter_guild_ids)                     # interval_minutes=5   → TradingService.update_frozen_shorts()
class LiquidationTask(*, service_factory, iter_guild_ids, notifier, clock?)   # interval_minutes=5   → LiquidationService.check_and_liquidate_shorts(now) + notifier(event) per event
class VcBoostTask(*, service_factory, iter_guild_ids, clock?)                 # interval_minutes=15  → PriceTickService.vc_boost_tick(extra_boosts, now); owns dict[str, list[VcExtraBoost]]
    def set_store_for_guild(guild_id, boosts: list[VcExtraBoost]) -> None
    def get_store_for_guild(guild_id) -> list[VcExtraBoost]
class DailyResetTask(*, service_factory, iter_guild_ids, system_state_repo, clock?)   # interval_minutes=1 → ActivityService.reset_today_buckets() + SystemState upsert (UTC date gate)
class WeeklyResetTask(*, service_factory, iter_guild_ids, system_state_repo, clock?)  # interval_minutes=1 → ActivityService.reset_week_buckets() + SystemState upsert (ISO (year, week) gate)
class MonthlyRolloverTask(*, portfolio_service_factory, fund_service_factory, iter_guild_ids, clock?)  # interval_hours=1 → portfolio.capture_month_start_net_worth() THEN fund.accrue_apy(now=now); fires only when day==1 AND hour==0
```

Common ctor shapes:
- `service_factory: Callable[[str], TService]` — builds a per-guild service for a `guild_id`.
- `iter_guild_ids: Callable[[], Awaitable[Iterable[str]]]` — async yields current guild IDs.
- `notifier: Callable[[LiquidationEvent], Awaitable[None]]` — generic; no `discord` coupling.
- `clock: Callable[[], datetime] | None = None` — defaults to `datetime.now(tz=UTC)`.

## Conventions Phase 10/11/13/14 MUST honour

1. **Task scheduling binding point** (Phase 14): for each task, read its
   `interval_minutes` / `interval_hours` class attribute and bind
   `task._loop = discord_tasks.loop(...)(task._run)`. Snippet at
   `baton-pass/phase-9/002-2026-05-25-phase-9-complete.md` L33-40. This is the
   ONLY place in the codebase that imports `discord.ext.tasks` for these
   loops — keeps `adapters/tasks/` package free of any `discord` import.
2. **Notifier callback contract** (Phase 14 Liquidation wiring): provide a
   `Callable[[LiquidationEvent], Awaitable[None]]` that builds + dispatches
   the Discord embed. Each notifier invocation is independently
   `_safe_run`-wrapped, so a single malformed embed cannot stall the per-tick
   event stream.
3. **`VcExtraBoost` ownership location**: the `VcBoostTask` instance owns
   `dict[str, list[VcExtraBoost]]` (per-guild). The Phase 12 voice-ping listener
   calls `task.set_store_for_guild(guild_id, ...)` to seed; the task is
   single-instance across all guilds (storage-by-parameter per 8b digest §5).
4. **Per-guild fan-out mechanism**: ONE task instance per task class — NOT per
   guild. The task walks `iter_guild_ids()` each tick and builds per-guild
   services on demand. Phase 14 wires `iter_guild_ids` to
   `discord.Client.guilds` (yielding `str(g.id)`).
5. **Reset-task state-advance ordering** (Daily/Weekly): service call runs FIRST,
   state upsert SECOND. A failing service leaves state unadvanced and the next
   tick retries — never silently skips a reset.
6. **Monthly rollover ordering** (load-bearing): `capture_month_start_net_worth`
   runs BEFORE `accrue_apy`. Re-ordering inflates the baseline with the
   freshly-accrued APY and skews per-month P&L attribution.
7. **No new `SystemState` field** for monthly rollover — the day+hour gate plus
   1-hour cadence guarantees ≤1 fire/month (Phase 8e `accrue_apy` is retry-safe).
8. **`_safe_run` catches `Exception` only** — `asyncio.CancelledError`,
   `KeyboardInterrupt`, `SystemExit` propagate so process shutdown works.
