# Pass-Baton: Phase 15b — migrate flags + orphan check implemented

**Date:** 2026-05-27
**Scope:** phase-15b
**Branch:** feat/phase-15b-migrate-flags
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-15
**HEAD:** 3043716 chore(phase-15a): review CLEAN + phase-exit digest
(no Phase 15b commit yet — manager owns commits)

## Where things stand

`src/friendex/adapters/persistence/migrate_json_to_sqlite.py` now carries
`--dry-run`, `--report`, and a post-migration orphan-warning pass. All three
acceptance criteria (B1/B2/B3) plus B4 test coverage are green, the Phase 15a
artifacts (`tests/integration/test_migration_realistic.py` and
`tests/fixtures/json/realistic/`) are byte-identical to `3043716` (verified
with `git diff 3043716 -- ...` returning empty), and the full local gate
(`uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy src/friendex`) is green — 790 tests pass.

The branch is ready for the manager to commit and open a PR.

## What changed

- `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`:
  - **B1** — `--dry-run` argparse flag (`store_true`). When set, `_run` builds
    a throwaway in-memory engine (`_DRY_RUN_TARGET =
    "sqlite+aiosqlite:///:memory:"`) instead of opening `--target`, so the
    target URL is never touched. The migration still runs end-to-end (every
    fixture is parsed, every row is would-be-written) so the full mapping
    pipeline is exercised and `migrate()` still returns its counts dict.
  - **B2** — `--report` argparse flag (`store_true`) and a new `_print_report`
    helper that emits one `<table>: <count>` line per table to `stdout`,
    sorted by table name. Composes with `--dry-run` — the count dict captured
    before any rollback is what's printed.
  - **B3** — `_warn_orphan_positions(source)` runs unconditionally at the end
    of `_run`, on both real and dry-run paths. It walks `users.json` once,
    collects the set of known user ids, then iterates every long and short
    position's `target_user_id` and emits `logger.warning(...)` for each
    orphan with `owner_id`, `target_id`, and `side`. Source-side (not
    DB-side) so the dry-run path can warn without persisting. Never raises;
    the migrator continues to exit 0 per Q2 signoff.
  - Module docstring updated with a "Phase 15b" paragraph summarising the
    three new behaviours.
- `tests/integration/test_migration_dry_run.py` (NEW) — three tests:
  - `test_dry_run_writes_nothing_to_target` — invokes `main()` with
    `--dry-run` against the realistic fixtures, then re-opens the target
    on-disk SQLite file (`tmp_path / "target.db"`), creates the schema, and
    asserts `SqlUserRepository.list_all("999") == []`.
  - `test_report_prints_counts_in_sorted_order` — invokes `main()` with
    `--dry-run --report`, captures `stdout` via `capsys`, asserts one
    `<table>: <count>` line per expected table, lines are
    lexicographically sorted, and counts match the dict that `migrate()`
    returns against the same fixtures.
  - `test_orphan_position_is_warned_not_failed` — invokes `main()` against
    the new `tests/fixtures/json/realistic_orphan/` fixture (user 2001
    holds a long position on user 9999, which has no `UserAccount`), uses
    `caplog` on the migrator's named logger to assert at least one
    WARNING contains the orphan target id `9999`, and that `main()` still
    returns 0. Defends against `alembic.env`'s
    `disable_existing_loggers=True` by explicitly re-enabling the logger
    before `caplog.set_level` (mirrors the same defensive pattern at
    `tests/adapters/test_container.py:378`).
- `tests/fixtures/json/realistic_orphan/users.json` (NEW) — tiny 2-user
  fixture exercising the orphan path. User 2001 has a long position on
  user 9999 (orphan). User 2002 has a short on 2001 (valid). Only
  `users.json` is provided; the migrator's `_load_json_object` returns an
  empty dict for missing source files, so `prices.json`, `funds.json`, and
  `fund_penalties.json` are intentionally absent.

## RED-first evidence

Each acceptance criterion had its test fail before the implementation
landed (verified by temporarily neutralising the implementation and
re-running the test):

| AC | First-RED output |
|---|---|
| B1 dry-run | `assert accounts == []` failed with a 2828-line diff listing all 50 persisted users (with `_DRY_RUN_TARGET` swap disabled). |
| B2 report  | `expected one report line per table (8), got 0: []` (with the `_print_report` call commented out). |
| B3 orphan  | `expected at least one WARNING naming the orphan target id 9999; got records: []` (with the `_warn_orphan_positions` call commented out). |

The initial run (before any implementation) failed with `argparse:
unrecognized arguments: --dry-run` on all three tests, confirming the flag
itself was the first wall.

## Verification gate (local)

```
$ uv run pytest                                  # 790 passed, 1 warning in 16.08s
$ uv run ruff check .                            # All checks passed!
$ uv run ruff format --check .                   # 151 files already formatted
$ uv run mypy src/friendex                       # Success: no issues found in 70 source files
```

Manual CLI smoke from spec line 816-819:

```
$ uv run python -m friendex.adapters.persistence.migrate_json_to_sqlite \
    --source tests/fixtures/json/realistic/ \
    --target sqlite+aiosqlite:///:memory: \
    --dry-run --report --guild-id 999
INFO migrated 50 row(s) into users
...
fund_investors: 48
fund_penalties: 10
hedge_funds: 31
long_positions: 68
price_history: 252
short_positions: 40
stocks: 50
users: 50
INFO dry-run complete: 549 row(s) across 8 tables (no writes persisted)
```

Orphan smoke (using `realistic_orphan/`):

```
WARNING orphan long position: owner=2001 target=9999 has no matching UserAccount
INFO dry-run complete: 4 row(s) across 8 tables (no writes persisted)
```

## Next steps

1. Manager: stage and commit the changes — the Phase 15 spec suggests a
   single `feat(persistence): migrator dry-run and reporting` commit for
   this half (the 15a `test: realistic JSON migration fixtures` commit
   already landed at `3a90e2c`).
2. Open the PR referencing issue #2 and the Phase 15 plan section.
3. Run review subagent; address any findings; merge.

## Open questions / risks

- **No new runtime dependencies introduced.** The orphan check uses only
  the existing `_load_json_object` helper and standard `logging`.
- The orphan check runs on both `--dry-run` and real paths (per AC B3).
  If a future phase wants an "errors-only" mode that *fails* on orphans,
  that's a separate flag — out of Phase 15b scope.
- Phase 15a carry-forward LOW findings (digest §"Carry-forward LOW
  findings") are NOT addressed here because they touch
  `test_migration_realistic.py`, which the Phase 15b contract forbids
  modifying. They remain open for a future cleanup pass.

## References

- Spec: `docs/04-migration-plan.md` §"Phase 15" lines 793-824
- Phase 15a digest:
  `baton-runner/br-2026-05-27-phase-15/digest-phase-15a.md`
- Code:
  - `src/friendex/adapters/persistence/migrate_json_to_sqlite.py` —
    new `_warn_orphan_positions`, `_print_report`, `_DRY_RUN_TARGET`,
    updated `_run` + `main` + parser.
  - `tests/integration/test_migration_dry_run.py` — new
  - `tests/fixtures/json/realistic_orphan/users.json` — new
- Issue: #2 (phase status checklist)
