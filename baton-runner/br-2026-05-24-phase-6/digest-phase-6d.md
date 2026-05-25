# Phase-6d digest — SqlPriceRepository + SqlFundRepository (conventions for 6e/6f)

**Status:** CLEAN (gate green; all 4 ACs met; prune-boundary test non-tautological; no N+1; no new deps).

## Public surface (`adapters/persistence/price_repo.py`)
`SqlPriceRepository` — structural `IPriceRepo`, no inheritance. Methods:
- `async get(guild_id, user_id) -> Stock | None`
- `async upsert(guild_id, stock) -> None`  (scalar row only — history is append-only)
- `async delete(guild_id, user_id) -> None`  (DB CASCADE drops history)
- `async list_all(guild_id) -> list[Stock]`
- `async append_history(guild_id, user_id, point) -> None`
- `async get_history(guild_id, user_id, *, since=None) -> list[PricePoint]`  (oldest-first)
- `async prune_history_older_than(cutoff) -> int`  (single bulk DELETE, cross-guild)

## Public surface (`adapters/persistence/fund_repo.py`)
`SqlFundRepository` — structural `IFundRepo`, no inheritance. Methods:
- `async get(guild_id, fund_id) -> HedgeFund | None`
- `async upsert(guild_id, fund) -> None`  (whole aggregate: merge scalar + wipe/re-insert investors)
- `async delete(guild_id, fund_id) -> None`  (DB CASCADE drops investors)
- `async list_all(guild_id) -> list[HedgeFund]`
- `async ensure_events_wallet(guild_id) -> HedgeFund`  (get-or-create; idempotent)

## Conventions 6e/6f MUST follow
- **Bulk sweeps = one DELETE, never a loop.** Prune/purge =
  `delete(ORM).where(<col> < cutoff)` returning `rowcount` (cast
  `result` to `CursorResult[object]` for mypy under `warn_redundant_casts`).
  6e's `purge_expired` mirrors this with `expires_at <= now` (note `<=`, inclusive
  — TTL is "expired AT now", unlike price prune's exclusive `<`).
- **Boundary is load-bearing — pin it with a test whose row sits exactly on the
  cutoff** so flipping `<`↔`<=` flips the result (non-tautological).
- **No N+1 in `list_all` (this is the 6c carry-forward, now resolved):** load all
  children for the guild in ONE query, group in memory with `defaultdict`
  (`_load_history_by_user`, `_load_investors_by_fund`). 2 queries total, not N+1.
- **Idempotent get-or-create** relies on `session.merge` on a fixed PK → repeat
  call is an UPDATE, no IntegrityError. Assert `COUNT == 1` + no value mutation.
- Ctor takes `async_sessionmaker`; one `async with session` + `commit()` per
  method (from 6c). Money via `DecimalText`, datetimes via `UtcDateTime`;
  immutable mapping (build fresh domain objs, never mutate loaded rows).
- 6e DTOs (`SystemState`, `TradeCooldown`) live in `application/interfaces.py` and
  carry `guild_id` *inside* the DTO — `upsert(dto)` has no separate `guild_id` arg.
