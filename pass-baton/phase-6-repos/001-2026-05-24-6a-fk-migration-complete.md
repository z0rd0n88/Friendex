# Pass-Baton: Sub-unit 6a — FK enforcement + cascade migration COMPLETE

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** f7aa6f1 chore(baton-runner): set up phase-6 run + preserve phase-5 FK baton
(uncommitted — manager owns git)

## Where things stand

Sub-unit **6a-fk-migration** is **COMPLETE**. All four acceptance criteria TDD'd
RED→GREEN; `scripts/gate.sh baton-runner/br-2026-05-24-phase-6/selfcheck-6a/`
prints **GATE: PASS** (pytest 271 passed, ruff check + format clean, mypy clean).
Supersedes `000` in this scope.

- [x] **AC1 — PRAGMA foreign_keys=ON** (`src/friendex/adapters/persistence/db.py`).
  `_enable_sqlite_foreign_keys` attaches a `@event.listens_for(engine.sync_engine,
  "connect")` listener inside `build_engine`, guarded to the `sqlite` dialect.
  RED: `assert 0 == 1` (`test_db.py::test_build_engine_enables_foreign_keys`).
- [x] **AC2 — Alembic 0002 ON DELETE CASCADE** (`alembic/versions/0002_fk_cascade.py`)
  + `render_as_batch=True` on BOTH `context.configure` calls in `alembic/env.py`.
  Batch `copy_from` + `recreate="always"` rebuilds all 6 child tables; `upgrade`
  sets CASCADE, `downgrade` reverts to plain FK (fully reversible). RED:
  `assert 6 == 0` (downgrade-strips-cascade). Three AC2 tests:
  `test_0002_round_trips_head_base_head`, `test_0002_upgrade_sets_cascade_on_child_fks`,
  `test_parent_delete_cascades_to_children`.
- [x] **AC3 — Decimal quantisation** (MEDIUM carry-forward, `test_orm.py`). Two new
  tests + `_FLOAT_INEXACT = Decimal("12345.67")` fixture (float-inexact). Explicit
  `.as_tuple().exponent ==` per DecimalText column. RED (with a Numeric column):
  `AssertionError: assert -10 == -2`.
- [x] **AC4 — real compare_metadata drift test** (LOW carry-forward, `test_migrations.py`):
  `test_no_drift_after_head_compare_metadata` upgrades to head then asserts
  `compare_metadata(MigrationContext, Base.metadata) == []`. Verified load-bearing
  (dropping a table yields an `add_table` diff). NB: compare_metadata does NOT
  catch SQLite FK ondelete — that is pinned by the AC2 cascade tests instead.

## Important side effect handled (not scope creep — ADR-0002 mandated)

Turning FK enforcement ON broke 6 PRE-EXISTING `test_orm.py` round-trip tests that
inserted orphan child rows. Per ADR-0002 Consequences ("test setup must insert
parents first") I added `_minimal_user()` + `_add_parent()` (flush-parent-first)
helpers and fixed all 6 (long/short position, user-with-positions, activity
bucket, stock, hedge fund). No production logic changed.

## Files touched (7)

- M `src/friendex/adapters/persistence/db.py`, `alembic/env.py`,
  `tests/adapters/persistence/test_migrations.py`, `tests/adapters/persistence/test_orm.py`
- A `alembic/versions/0002_fk_cascade.py`, `tests/adapters/persistence/test_db.py`
- A this baton (+ `000`)

## Next steps (for 6b+ — out of THIS sub-unit's scope)

1. Repository interfaces (`application/interfaces.py`) + Sql*Repository impls.
2. JSON→SQLite migrator + fixtures.
3. Repo `delete` methods need NO explicit child deletion — DB cascade handles it
   (ADR-0002). New child tables added later: add their FK with CASCADE in orm.py
   AND a follow-on batch migration if amending an existing schema.

## Open questions / risks

- None blocking. Dependency note: NO new dependencies added.
- Manager owns all git/PR actions; nothing committed by this sub-unit.

## References

- Spec: `docs/04-migration-plan.md` §"Phase 6 — Persistence" (L345-388)
- ADR: `docs/adr/0002-sqlite-fk-enforcement.md`
- Gate logs: `baton-runner/br-2026-05-24-phase-6/selfcheck-6a/`
- Prior baton: `pass-baton/phase-6-repos/000-2026-05-24-6a-fk-migration.md`
