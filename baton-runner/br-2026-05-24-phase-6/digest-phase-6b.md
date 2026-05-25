# Phase-exit digest — sub-unit 6b: repository Protocol interfaces

CLEAN. Gate green (pytest/ruff/ruff-format/mypy). HEAD 40726d3.
`src/friendex/application/interfaces.py`. All methods `async`, hinted with
**domain models** (never ORM); every per-guild method takes `guild_id`.

## Public Protocol surface (contract 6c–6f must satisfy)

Common CRUD on all six: `get / upsert / delete / list_all`. Plus:

- **IUserRepo** (UserAccount): + `list_active_in_last(guild_id, seconds: float) -> list[UserAccount]`.
- **IPriceRepo** (Stock): + `append_history(guild_id, user_id, point: PricePoint)`,
  `get_history(guild_id, user_id, *, since: datetime | None = None) -> list[PricePoint]`
  (oldest-first; since→`recorded_at`),
  `prune_history_older_than(cutoff: datetime) -> int` (cross-guild).
- **IFundRepo** (HedgeFund): + `ensure_events_wallet(guild_id) -> HedgeFund` (idempotent; fund_id `events_wallet`).
- **IPenaltyRepo** (FundPenalty): CRUD only.
- **ITradeCooldownRepo** (TradeCooldown DTO): `get` **excludes expired**;
  `upsert(cooldown)` scope-in-DTO; `list_all(guild_id)` includes expired;
  + `purge_expired(now: datetime) -> int`.
- **ISystemStateRepo** (SystemState DTO): keyed by guild_id only;
  `upsert(state)` scope-in-DTO; `list_all()` **unscoped** (iterates every guild).

## DTO decision (sound, no finding)

`SystemState` + `TradeCooldown` (`@dataclass(frozen=True)`) live in interfaces.py:
their ORM tables have no domain mirror and interfaces must not import the ORM.
They are adapter-bookkeeping value objects (not game-domain), so they correctly
do NOT go in `domain/models.py`. Frozen, UTC-aware datetimes, no money fields.

## Conventions 6c–6f MUST honour

- **Structural conformance, NO inheritance** — mypy gates by shape (verified: a
  non-matching impl IS rejected). Do not subclass IXxx.
- **Split upsert signatures:** User/Price/Fund/Penalty → `upsert(guild_id, obj)`;
  Cooldown/SystemState → `upsert(dto)` (guild_id is a DTO field).
- **`delete` does no child cleanup** — DB-level ON DELETE CASCADE (ADR-0002).
- **Parameterized SQL** for cutoff/now in prune/purge/get_history(since).
- Import the two DTOs from `friendex.application.interfaces`; never re-declare.
