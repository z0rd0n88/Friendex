# Phase 6e digest — penalty / cooldown / system-state repos

Three FK-less, child-less SQLAlchemy adapters; ctor takes
`async_sessionmaker[AsyncSession]`; one `async with session` + `commit()` per
mutation. All re-exported from `adapters/persistence/__init__.py`. No new deps.

## Public surface (conform structurally to interfaces.py Protocols)

### SqlPenaltyRepository (`penalty_repo.py`) — plain store, NOT a TTL filter
- `get(guild_id, user_id) -> FundPenalty | None`  (returns expired too)
- `upsert(guild_id, penalty)` — scope is a **separate arg** (domain model has no guild_id)
- `delete(guild_id, user_id)`
- `list_all(guild_id) -> list[FundPenalty]`  (live + expired)

### SqlTradeCooldownRepository (`cooldown_repo.py`) — TTL via expires_at
- `get(guild_id, user_id, *, now: datetime | None = None) -> TradeCooldown | None`
  excludes `expires_at <= now` (strict `>` in SQL); `now` defaults to `datetime.now(UTC)`
- `upsert(cooldown)` — guild_id is **inside the DTO** (no separate arg)
- `delete(guild_id, user_id)`
- `list_all(guild_id) -> list[TradeCooldown]`  (includes expired)
- `purge_expired(now) -> int` — one bulk `DELETE WHERE expires_at <= now`, **unscoped (cross-guild)**

### SqlSystemStateRepository (`system_state_repo.py`) — single row per guild
- `get(guild_id) -> SystemState | None`  (None = "never reset")
- `upsert(state)` — guild_id **inside the DTO**; `merge` on PK ⇒ idempotent UPDATE
- `delete(guild_id)`
- `list_all() -> list[SystemState]`  — **unscoped**, no guild_id arg

## Conventions 6f (migrator) MUST follow when writing these tables

- **DTO scope placement differs:** penalty's `upsert` takes `(guild_id, penalty)`;
  cooldown/system-state carry `guild_id` *inside* the DTO. Don't pass it twice.
- **`SystemState`/`TradeCooldown` are app-layer `@dataclass(frozen=True)` DTOs**
  (in `application/interfaces.py`), NOT domain models. Build fresh, never mutate.
- **Datetimes must be tz-aware UTC** — `UtcDateTime` rejects naive at bind time
  (ValueError). `penalty_apr` must be a `Decimal` (`DecimalText` rejects non-Decimal).
- **PKs:** penalty/cooldown `(guild_id, user_id)`; system_state `guild_id` alone.
  Re-inserting the same PK via `upsert`/`merge` is an UPDATE — idempotent, no dup row.
- **Cooldown TTL semantics:** seed `expires_at` only; expired rows are valid to
  write (the sweep/`get` interpret expiry). Penalties: write expired rows freely too
  (decay task deletes them; expiry interpretation lives in `fund_math`).
