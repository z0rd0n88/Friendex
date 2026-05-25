# Phase-6 sub-unit 6a — FK enforcement + cascade migration (digest)

VERDICT: CLEAN. Gate green (pytest 271 / ruff / mypy). No new deps.

## Public surface added

- `adapters/persistence/db.py`
  - `build_engine(url, *, echo=False) -> AsyncEngine` now wires a `connect`
    event listener (`_enable_sqlite_foreign_keys`) that issues
    `PRAGMA foreign_keys=ON` on every SQLite DBAPI connection. Dialect-guarded
    (`engine.dialect.name == "sqlite"`), so it's a no-op on Postgres.
    Contract: **every engine built by this factory enforces FKs.** A fresh
    connection reports `PRAGMA foreign_keys == 1`.
  - `build_engine_from_settings` / `build_sessionmaker` unchanged.
- `alembic/env.py`: `render_as_batch=True` on BOTH `context.configure` calls
  (offline + online) — required for SQLite move-and-copy DDL.
- `alembic/versions/0002_fk_cascade.py` (`down_revision = 0001_baseline`):
  rebuilds the 6 child tables via `op.batch_alter_table(copy_from=...,
  recreate="always")`. `upgrade` → FK `ondelete="CASCADE"`; `downgrade` →
  plain FK. Fully reversible (head→base→head test passes).

## CASCADE behavior (the 6 child FKs)

long_positions→users, short_positions→users, activity_buckets→users,
voice_unique_channels→activity_buckets, price_history→stocks,
fund_investors→hedge_funds. `fund_penalties` / `system_state` /
`trade_cooldowns` declare NO FK (not children) — correctly excluded.

## Decisions / conventions the next sub-units MUST honor

- **FK enforcement is ON for every engine, including test fixtures.** Tests and
  fixtures MUST insert + flush the parent row before any child. Use the
  `_minimal_user()` / `_add_parent(session, parent)` helpers established in
  `tests/adapters/persistence/test_orm.py`.
- **Repository `delete` methods need NO explicit child deletion** — the DB-level
  CASCADE handles it (ADR-0002). Do not hand-roll child cleanup.
- **Adding a new child table later:** declare its FK with `ondelete="CASCADE"`
  in `orm.py` AND, if amending an existing deployed schema, add a follow-on
  batch migration. (Note: the baseline `0001` runs `create_all` off
  `Base.metadata`, so a fresh DB already carries CASCADE from the ORM
  declaration — see review baton 002 M1; reconcile ADR-0002 wording in a small
  follow-up, non-blocking.)
- **Money/Decimal:** `DecimalText` preserves exact value AND quantisation
  exponent; never swap it for `Numeric`/float (would corrupt both).
