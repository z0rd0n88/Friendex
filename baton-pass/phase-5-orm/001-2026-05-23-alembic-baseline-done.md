# Pass-Baton: Phase 5b — Alembic baseline & reversibility (DONE)

**Date:** 2026-05-23
**Scope:** phase-5-orm
**Branch:** feat/br-2026-05-23-p4p5/phase-5
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-23-p4p5
**HEAD:** 678a432 feat(phase-5): SQLAlchemy base/engine + ORM models with domain mappers

## Where things stand

Sub-unit 5b is **complete and green** (uncommitted — the manager owns git). The
Alembic baseline is wired and proven reversible: `alembic.ini`, `alembic/env.py`
(async-aware, `DATABASE_URL`-driven), `alembic/script.py.mako` (stock async
template), and `alembic/versions/0001_baseline.py` now exist, plus a new
pytest reversibility/no-drift suite `tests/adapters/persistence/test_migrations.py`
(3 tests). All five acceptance criteria pass. With 5a (ORM) already done, **Phase 5
is functionally complete** — the next move is the Phase-5 review + digest + PR.

## Files created (5)

- `alembic.ini` — `script_location = alembic`, `prepend_sys_path = .`,
  placeholder `sqlalchemy.url = sqlite+aiosqlite:///data/friendex.db` (overridden
  at runtime by `DATABASE_URL` in env.py), standard logging config.
- `alembic/env.py` — async-aware; reads `DATABASE_URL` from `os.environ` and
  `set_main_option("sqlalchemy.url", ...)`; imports `Base` **and**
  `friendex.adapters.persistence.orm` (side-effect import → all tables register);
  `target_metadata = Base.metadata`; offline + online (async `asyncio.run`) modes.
- `alembic/script.py.mako` — the standard Alembic async template, verbatim.
- `alembic/versions/0001_baseline.py` — see Key decisions (metadata-driven, zero-drift).
- `tests/adapters/persistence/test_migrations.py` — 3 tests (upgrade-creates,
  downgrade-drops, baseline==ORM-metadata).

## Key decisions

- **Baseline is metadata-driven, NOT hand-transcribed.** `upgrade()` does
  `Base.metadata.create_all(bind=op.get_bind())`; `downgrade()` does
  `Base.metadata.drop_all(...)`. The migration is therefore a literal projection
  of `orm.py` — drift is impossible by construction, and `drop_all` orders drops
  to respect the FK dependency graph (FK-safe downgrade with no manual sequencing).
  This directly resolves the "baseline drift risk" flagged in baton 000.
  Trade-off: this baseline isn't itself autogenerate-style explicit DDL; future
  *incremental* migrations (Phase 6+) will be real `op.*` ops via `alembic
  revision --autogenerate`. Acceptable: a baseline that mirrors metadata is the
  common pattern and removes the transcription-drift failure mode entirely.
- **No-diff check (criterion #2) = table-set + per-table column-name-set equality**
  between an Alembic-migrated DB and a `create_all` DB, not a strict
  `compare_metadata` autogenerate diff. Rationale: SQLite's dynamic typing
  round-trips the `DecimalText`/`UtcDateTime` `TypeDecorator` columns through
  generic affinities, so `compare_metadata` reports noisy type-only "diffs" that
  aren't real schema differences. The table+column contract is what actually
  matters for the JSON→SQLite cutover. Test also asserts the migrated set equals
  the explicit 12-table `_EXPECTED_TABLES` frozenset, so a future hand-edit that
  silently adds/drops a table is caught.
- **Tests use a `tmp_path` file-backed temp DB** (`sqlite+aiosqlite:///<tmp>/m.db`)
  with `monkeypatch.setenv("DATABASE_URL", ...)` — never `Settings.database_url`.
  Introspection uses a *sync* inspector against the `sqlite://` (non-aiosqlite)
  equivalent URL so it needs no event loop; tests are plain `def` (not `async`)
  because `alembic.command.*` is synchronous and env.py drives the async engine
  via `asyncio.run` internally — calling `command.upgrade` must NOT be awaited.

## RED evidence (TDD)

Wrote `test_migrations.py` first; ran before any alembic files existed:
```
alembic.util.exc.CommandError: Path doesn't exist:
  /home/alex/Friendex/.claude/worktrees/br-2026-05-23-p4p5/alembic.
  Please use the 'init' command to create a new scripts folder.
3 failed in 0.28s
```
After creating alembic config + baseline → `3 passed in 1.17s`.

## Verification (actual output)

- `ruff check src/friendex/adapters/persistence tests/adapters/persistence alembic` → **All checks passed!**
- `ruff format --check ...` → **9 files already formatted**
- `mypy src/friendex/adapters/persistence alembic/env.py` → **Success: no issues found in 5 source files**
  (also `mypy alembic/versions/0001_baseline.py` → Success, defense-in-depth)
- `pytest tests/adapters/persistence/ -q` → **17 passed** (14 ORM + 3 migration)
- **Criterion #4 (doc shell flow):**
  `DATABASE_URL=sqlite+aiosqlite:////tmp/alembic-check.db uv run alembic upgrade head` → exit 0;
  post-upgrade tables = all 12 domain tables + `alembic_version`;
  `... alembic downgrade base` → exit 0; post-downgrade tables = `['alembic_version']` only;
  `rm -f /tmp/alembic-check.db` → clean.
- **Criterion #5 (full repo suite):** `uv run pytest -q` → **261 passed** (was 258 pre-5b; +3).

## Next steps

1. **Phase-5 review** (5a+5b together): the metadata-driven baseline + the
   no-diff check decision are the two things a reviewer should weigh in on.
2. Write `digest-phase-5.md` and open the stacked Phase-5 PR (`Refs #2`).
3. Phase 6 (repositories + JSON migrator): first real autogenerate migration
   should be exercised then to confirm `--autogenerate` picks up future ORM
   changes against this baseline (the baseline being metadata-driven does not
   impair autogenerate, which diffs DB-state vs metadata, not migration source).

## Open questions / risks

- **No new dependencies declared.** `alembic>=1.13` was already in
  `pyproject.toml` deps (installed 1.18.4); SQLAlchemy 2.0.49. Nothing added.
- **`alembic.ini` placeholder URL** points at the app default
  `sqlite+aiosqlite:///data/friendex.db`; it is only used if `DATABASE_URL` is
  unset. Reviewer may prefer an obviously-fake placeholder so a stray run can't
  touch a real DB — left as the real default so an accidental unset fails loudly
  against the expected path rather than a surprise location.
- **SQLite FK enforcement** still off by default (carried from baton 000) — the
  baseline emits the `ForeignKeyConstraint`s but SQLite only enforces them with
  `PRAGMA foreign_keys=ON`. Decide in Phase 6 repos.

## References

- Prior baton (5a): `baton-pass/phase-5-orm/000-2026-05-23-orm-roundtrip-done.md`
- Spec: `docs/04-migration-plan.md` §"Phase 5 — Persistence: ORM & Alembic Baseline"
- Tracking: GitHub issue #2
- Code: `alembic.ini`, `alembic/env.py`, `alembic/script.py.mako`,
  `alembic/versions/0001_baseline.py`,
  `tests/adapters/persistence/test_migrations.py`
