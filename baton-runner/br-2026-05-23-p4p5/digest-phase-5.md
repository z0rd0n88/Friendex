# Phase 5 exit digest — persistence ORM + Alembic baseline (CLEAN)

Public surface Phase 6 (repositories + JSON migrator) must honor.

## `friendex.adapters.persistence`
- **`db.py`**: `Base(DeclarativeBase)` (single metadata registry); `build_engine(url, *, echo=False) -> AsyncEngine`; `build_engine_from_settings(settings, *, echo=False)`; `build_sessionmaker(engine) -> async_sessionmaker[AsyncSession]` (`expire_on_commit=False`). No import-time engine.
- **`types.py`** (reuse for every column): `DecimalText(TypeDecorator[Decimal])` stores `str(Decimal)` in TEXT — exact value + quantisation; rejects non-Decimal (`TypeError`). `UtcDateTime(TypeDecorator[datetime])` stores ISO-8601 UTC TEXT; aware→UTC on bind, REJECTS naive (`ValueError`); reloads tz-aware UTC.
- **`orm.py`** — 12 classes: UserORM, LongPositionORM, ShortPositionORM, ActivityBucketORM, VoiceUniqueChannelORM, StockORM, PriceHistoryORM, HedgeFundORM, FundInvestorORM, FundPenaltyORM, SystemStateORM, TradeCooldownORM.
  - Domain-mirror classes: `from_domain(guild_id, …, obj) -> ORM` + `to_domain(…) -> domain`. Adapter-only (SystemStateORM, TradeCooldownORM) use `create(...)`; SystemStateORM is one row PER GUILD.
  - Aggregate `to_domain` takes loaded children (pure): `UserORM.to_domain(long_positions=, short_positions=, today=, week=)`; `ActivityBucketORM.to_domain(voice_unique_channels)`; `StockORM.to_domain(history)`; `HedgeFundORM.to_domain(investors)`; `FundInvestorORM.to_amount()`.

## Storage conventions (MANDATORY)
- Money/price → `DecimalText` (never raw `Numeric`/`Float` — SQLite drops quantisation). Pass pre-quantised `Decimal` (currency 0.01, rates 0.0001).
- Datetimes → `UtcDateTime`, always tz-aware UTC; naive raises at bind.
- Per-guild tables are `(guild_id, …)`-first composite PK (ADR-0001); domain dataclasses stay guild-agnostic — `from_domain` attaches guild_id, `to_domain` drops it.
- Collections → child tables, never blobs. Composite `ForeignKeyConstraint(ondelete=CASCADE)`. SQLite enforces FKs only with `PRAGMA foreign_keys=ON` (currently OFF) — Phase 6 must enforce cascade app-side OR enable the pragma.

## Migration baseline
- `alembic/versions/0001_baseline.py` is **metadata-driven**: `upgrade()`=`Base.metadata.create_all(bind)`, `downgrade()`=`drop_all(bind)`. `env.py` async-aware, reads `DATABASE_URL` from env, side-imports `…persistence.orm` to register tables.
- Phase 6 incremental migrations MUST be real `op.*` DDL via `alembic revision --autogenerate`; add a genuine drift test against that first migration (baseline column no-drift check is tautological — review baton 002 LOW).

## Carry-forward (review MEDIUM)
Add Decimal-quantisation assertions (`.as_tuple().exponent`) + a float-inexact fixture so a `Numeric` regression goes RED. UTC invariant already mutation-proven.
