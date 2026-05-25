# Phase-6c digest — SqlUserRepository (conventions for 6d/6e/6f)

**Status:** CLEAN (gate green; ACs met; cascade keystone non-vacuous; no new deps).
## Public surface (`adapters/persistence/user_repo.py`)
`SqlUserRepository` — structural `IUserRepo`, no inheritance. Methods:
- `async get(guild_id, user_id) -> UserAccount | None`
- `async upsert(guild_id, account) -> None`
- `async delete(guild_id, user_id) -> None`
- `async list_all(guild_id) -> list[UserAccount]`
- `async list_active_in_last(guild_id, seconds: float) -> list[UserAccount]`

## Construction pattern (FOLLOW in 6d–6f)
- Ctor takes `async_sessionmaker[AsyncSession]` (NOT an engine/live session):
  `def __init__(self, sessionmaker): self._sessionmaker = sessionmaker`.
- Each method opens its own `async with self._sessionmaker() as session:` — one
  transaction per call; never share sessions. Mutations end `await commit()`.
  Built on `build_sessionmaker` (`expire_on_commit=False`) so attrs stay live.

## ORM↔domain mapping (FOLLOW)
- Mapping lives on ORM classes (`from_domain(guild_id, ...)`/`to_domain(...)` in
  `orm.py`); repos stay thin. ADR-0001: `from_domain` attaches guild scope,
  `to_domain` drops it (domain is guild-agnostic).
- Immutable mapping: build fresh domain objects; never mutate loaded ORM rows.
  Aggregate `to_domain` takes loaded children as kwargs (pure fn).
- Money via `DecimalText` (exact value + scale), datetimes via `UtcDateTime`
  (tz-aware UTC) — preserve both; test with `as_tuple().exponent` + `tzinfo`.

## Aggregate persistence pattern (FOLLOW for owning-aggregate repos)
- `upsert` = `session.merge(parent_from_domain)` → explicitly DELETE all owned
  child tables for that key → re-`add_all` children from the domain object →
  commit. NOTE: because the parent is `merge`d (kept), children do NOT
  cascade on upsert — the explicit child wipe is required, not redundant.
- `delete` = single parent `delete(...).where(pk)` + commit; rely on DB-level
  `ON DELETE CASCADE` (ADR-0002 PRAGMA). Do NOT hand-roll child cleanup in
  `delete`. Prove the cascade test is non-vacuous (run once with FK OFF →
  orphans must remain).

## Carry-forward follow-up (non-blocking)
- N+1: `list_all`/`list_active_in_last` rebuild per-row (~5 child SELECTs/user).
  6d+ list methods should prefer batched `IN (...)` / eager load before Phase 9.
