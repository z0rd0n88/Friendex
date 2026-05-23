# Pass-Baton: Phase 5a — ORM + engine + Decimal/UTC round-trip (DONE)

**Date:** 2026-05-23
**Scope:** phase-5-orm
**Branch:** feat/br-2026-05-23-p4p5/phase-5
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-23-p4p5
**HEAD:** 500a329 chore(phase-5): advance run state; branch off phase-4

## Where things stand

Sub-unit 5a (db.py + orm.py + round-trip tests) is **complete and green**. All
12 Option B ORM classes are implemented as SQLAlchemy 2.0 typed `Mapped[...]`
models with `from_domain`/`to_domain` mappers living next to each class. Every
domain model with an ORM mirror is round-trip-verified through an in-memory
SQLite engine, with Decimal precision/quantisation and UTC tz-awareness asserted
explicitly. Scoped gate (ruff/format/mypy/pytest) and the full repo suite are
green. **Sub-unit 5b (alembic.ini, alembic/env.py, script.py.mako,
versions/0001_baseline.py + reversibility check) is NOT done — that is the next
unit.**

## Files created (5 — one beyond the 4 specified; see Open questions)

- `src/friendex/adapters/persistence/db.py` — `DeclarativeBase` `Base`,
  `build_engine(url)`, `build_engine_from_settings(settings)`,
  `build_sessionmaker(engine)` (`expire_on_commit=False`). No import-time engine.
- `src/friendex/adapters/persistence/types.py` — custom `TypeDecorator`s
  `DecimalText` and `UtcDateTime` (the round-trip mechanism — see Decisions).
- `src/friendex/adapters/persistence/orm.py` — UserORM, LongPositionORM,
  ShortPositionORM, ActivityBucketORM, VoiceUniqueChannelORM, StockORM,
  PriceHistoryORM, HedgeFundORM, FundInvestorORM, FundPenaltyORM, SystemStateORM,
  TradeCooldownORM.
- `tests/adapters/persistence/__init__.py` (empty)
- `tests/adapters/persistence/test_orm.py` — 14 tests.

## Key decisions

- **Decimal storage = TEXT via `DecimalText(TypeDecorator)` storing `str(Decimal)`.**
  Chosen over `Numeric(asdecimal=True)` because SQLite's dynamic typing collapses
  NUMERIC to IEEE-754 float, losing exact quantisation. TEXT round-trips losslessly:
  manually verified `Decimal('100.00')` and `Decimal('100.0')` stay DISTINCT and
  3-decimal values survive. `from_domain` validates the input is `Decimal`.
- **Datetime storage = TEXT via `UtcDateTime(TypeDecorator)` storing ISO-8601.**
  Bind converts aware→UTC and REJECTS naive (`ValueError`); result reloads tz-aware
  UTC. Honors Phase 3.1 UTC-aware invariant at the persistence boundary.
- **guild_id per ADR-0001.** Every per-guild table has a `guild_id`-first composite
  PK. Domain dataclasses stay guild-agnostic: `from_domain(guild_id, obj)` attaches,
  `to_domain()` drops. Position/bucket mappers also take `owner_id`/`user_id` since
  the domain child objects don't carry the owner. Guild isolation proved by test
  (`test_guild_isolation_same_user_two_guilds`).
- **Child collections → child tables, never blobs.** `voice_unique_channels` →
  VoiceUniqueChannelORM; `Stock.history` → PriceHistoryORM (surrogate autoincrement
  `id` PK + lookup index); `HedgeFund.investors` → FundInvestorORM. Aggregate
  `to_domain(...)` takes the loaded children as args so the mapper stays pure.
- **SystemStateORM / TradeCooldownORM have no domain mirror** (adapter bookkeeping).
  They expose `create(...)` classmethods instead of `from_domain`. SystemStateORM is
  one row PER GUILD (ADR-0001 per-guild reset flags), not a global singleton.
- **FK constraints** use composite `ForeignKeyConstraint` with `ondelete=CASCADE`
  (sets up Phase 6 cascade-delete tests). Note: SQLite enforces FKs only with
  `PRAGMA foreign_keys=ON` — not enabled here; revisit in Phase 6 repos if cascade
  must be enforced at the DB level rather than app level.
- **Lint:** `# noqa: TC003` on `datetime`/`Decimal` runtime imports in orm.py —
  SQLAlchemy resolves `Mapped[...]` annotations at class-construction time, so they
  CANNOT move under TYPE_CHECKING (hit `MappedAnnotationError` when I tried; this was
  the first RED-after-import failure).

## RED evidence (TDD)

1. First run (no modules): `ModuleNotFoundError: No module named
   'friendex.adapters.persistence.db'`.
2. After writing orm.py with `datetime` under TYPE_CHECKING:
   `sqlalchemy.orm.exc.MappedAnnotationError: Could not resolve all types within
   mapped annotation: "Mapped[datetime]"` → fixed by runtime import.
3. GREEN: `14 passed in 0.39s`.

## Verification (actual output)

- `uv run ruff check src/friendex/adapters/persistence tests/adapters/persistence` → All checks passed!
- `uv run ruff format --check ...` → 6 files already formatted
- `uv run mypy src/friendex/adapters/persistence` → Success: no issues found in 4 source files
- `uv run pytest tests/adapters/persistence/test_orm.py -v` → 14 passed
- `uv run pytest -q` (full repo) → 258 passed
- Full-scope `ruff check src tests` + `ruff format --check src tests` → clean (baton-runner gate scope)

## Next steps (sub-unit 5b)

1. Create `alembic.ini` (`script_location = alembic`, url from `${DATABASE_URL}`),
   `alembic/env.py` (async-aware, imports `Base` from `db.py`,
   `target_metadata = Base.metadata`, reads `DATABASE_URL` from `os.environ`),
   `alembic/script.py.mako`, `alembic/versions/0001_baseline.py`.
2. Baseline `upgrade()` must create ALL 12 tables (mirror `orm.py` exactly,
   including `guild_id`-first composite PKs, the FK constraints, and the
   `ix_price_history_lookup` index); `downgrade()` drops them in FK-safe order.
3. Reversibility gate:
   `DATABASE_URL=sqlite+aiosqlite:///tmp/alembic-check.db uv run alembic upgrade head`
   then `... alembic downgrade base`, then `rm -f /tmp/alembic-check.db`.
4. Phase-5 review + digest-phase-5.md + stacked PR after 5b lands.

## Open questions / risks

- **Extra file `types.py`** (5 files vs the 4 in scope). Justified: it is the
  mechanism enforcing the critical Decimal/UTC round-trip invariants, kept as its
  own cohesive small module per coding-style. Flag for reviewer; fold into orm.py
  only if the reviewer prefers a single module.
- **SQLite FK enforcement** is off by default (see Decisions). App-level cascade in
  repos may suffice; decide in Phase 6.
- **Alembic baseline drift risk:** the 0001 migration is hand-authored in 5b and
  must stay byte-for-byte consistent with `orm.py`'s `create_all` output. Consider
  asserting `create_all`-schema == migrated-schema in a 5b test.

## References

- Spec: `docs/04-migration-plan.md` §"Phase 5 — Persistence: ORM & Alembic Baseline"
- Schema: `docs/02-target-architecture.md` §Persistence Strategy "Option B"
- Multi-tenancy: `docs/adr/0001-per-guild-markets.md` (guild_id placement, mapper signatures)
- Tracking: GitHub issue #2
- Code: `src/friendex/adapters/persistence/{db,types,orm}.py`,
  `tests/adapters/persistence/test_orm.py`
- Prior baton: `pass-baton/phase-4-domain-funcs/003-2026-05-23-phase-4-review.md`
