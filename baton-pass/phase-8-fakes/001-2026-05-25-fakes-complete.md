# Pass-Baton: Fakes infra complete — all 8 ACs green

**Date:** 2026-05-25
**Scope:** phase-8-fakes
**Branch:** feat/phase-8-fakes
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** d8279cd chore(baton-runner): spawn python-pro for all units in this project (#41)

## Where things stand

Phase 8 test-double infrastructure is COMPLETE and all gates are green. Four
files delivered, nothing outside scope touched:
- `tests/application/fakes/__init__.py`
- `tests/application/fakes/fake_repos.py` — six fakes (User, Price, Fund,
  Penalty, TradeCooldown, SystemState)
- `tests/application/fakes/test_fake_repos.py` — 30 tests, all ACs pinned
- `tests/application/conftest.py` — 8 fresh-per-test fixtures

No new dependencies declared or needed. Ready for service phases 8a-8f to build
on these fakes + fixtures.

## Acceptance criteria — all GREEN

- [x] AC1 full Protocol surface (async sig + return type) + typed assignability
      (mypy verifies `repo: IXxxRepo = FakeXxx()` for all six)
- [x] AC2 round-trip upsert/get, missing→None, delete (all six fakes)
- [x] AC3 per-guild isolation; shared user-id across guilds does not collide
- [x] AC4 list_active_in_last windowing (inclusive `>= now - seconds`), scoped
- [x] AC5 history append→get oldest-first, `since` filter, prune count +
      inclusive-cutoff boundary, prune spans every guild
- [x] AC6 ensure_events_wallet idempotent + does not clobber existing balance
- [x] AC7 purge_expired removes only expired (inclusive `<=`), returns count,
      spans every guild; get hides expired while list_all keeps it
- [x] AC8 8 fixtures, each a fresh instance per test (verified empty at test start)

## Gates run (actual output)

- `uv run ruff check src tests` → `All checks passed!`
- `uv run ruff format --check src tests` → `59 files already formatted`
- `uv run pytest tests/application/fakes/test_fake_repos.py -v` → `30 passed`
- full suite `uv run pytest -q` → `403 passed in 6.78s` (no regressions)
- `uv run mypy src/friendex` → `Success: no issues found in 28 source files`
  (fake_repos.py + conftest.py + test file also mypy-clean, informational)

## TDD RED discipline

AC1 RED first surfaced as `ModuleNotFoundError: No module named
'...fake_repos'`, then a real failure `NameError: name 'UserAccount' is not
defined` from `get_type_hints` on `TYPE_CHECKING`-guarded forward refs — fixed
by passing a `localns` of domain models + DTOs. AC4/AC5/AC6/AC7 boundary
assertions were proven load-bearing by reverting the implementation
(inverted filters / removed sort / flipped `<`/`<=` boundaries / dropped
idempotency check) → 6 targeted FAILs, then restored → 30 passed.

## Key semantics mirrored from the real adapters

- `list_active_in_last`: `last_activity >= now - seconds` (inclusive).
- cooldown `get`: active iff `expires_at > now` (exclusive); fake keeps the
  adapter's extra `*, now: datetime | None = None` keyword (Protocol superset).
- `purge_expired`: `expires_at <= now` (inclusive) — matches `get` so a row is
  never both hidden-by-get and a purge survivor.
- `prune_history_older_than`: drops `timestamp < cutoff` (point at cutoff kept),
  spans every guild.
- `ensure_events_wallet`: fund_id `events_wallet`, name `Events Wallet`,
  manager_id `0`, $0.00 — returns existing untouched on repeat.
- Storage immutable: store references to frozen-style dataclasses, replace
  wholesale on upsert; never mutate in place.

## Next steps

1. Phase 8a service work consumes these fixtures (`fake_*_repo`, `lock_manager`,
   `default_settings`) from `tests/application/conftest.py`.
2. Carry-forward from phase-7-locks #000: fix the cancel-mid-acquire lock leak
   (1 MEDIUM) in the first service phase that uses `LockManager.locked`.

## References

- Protocols: `src/friendex/application/interfaces.py`
- Real adapters mirrored: `src/friendex/adapters/persistence/{user,price,fund,penalty,cooldown,system_state}_repo.py`
- Domain models: `src/friendex/domain/models.py`
- Prior baton: `baton-pass/phase-8-fakes/000-2026-05-25-fakes-infra.md`
- LockManager: `src/friendex/application/lock_manager.py`; Settings: `src/friendex/adapters/config.py`
