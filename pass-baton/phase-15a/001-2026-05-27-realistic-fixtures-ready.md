# Pass-Baton: Phase 15a realistic fixtures + integration test ready

**Date:** 2026-05-27
**Scope:** phase-15a
**Branch:** feat/phase-15a-fixtures
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-15
**HEAD:** f1e0e7f feat(phase-14): bot factory + entry point wiring (#65)

## Where things stand

Phase 15a implementation complete on a clean tree. Four realistic JSON
fixtures under `tests/fixtures/json/realistic/` plus the integration test
`tests/integration/test_migration_realistic.py` are in place. Full project
gate green (787 pytest, ruff check, ruff format --check, mypy on the test
file). Phase 15b can now extend the migrator (`--dry-run` / `--report` flags
+ orphan-warning consistency check) against this stable fixture surface
without touching the test.

The migrator (`src/friendex/adapters/persistence/migrate_json_to_sqlite.py`)
was NOT modified — this work-unit only validates the existing migrator
against richer data.

## What was built

### Fixtures (all match the small-fixture schema the migrator already parses)

- `tests/fixtures/json/realistic/users.json` — 50 users, ids `1001`..`1050`.
  35 users carry ≥ 1 long position, 25 carry ≥ 1 short, 6 of those shorts
  are `frozen: true`. Every long/short `target_id` references another id
  in the same file (zero orphans — orphan handling is Phase 15b's
  responsibility). Both `activity.today` and `activity.week` populated for
  every user (the migrator's two bucket discriminators are `today`/`week`,
  not `today`/`yesterday` — the task spec's "yesterday" was interpreted as
  `week` to match the schema the migrator actually parses). `daily.streak`
  values span `{0, 1, 2, 3, 5, 6, 7, 10, 14, 21, 30}` (every required value
  is present). `last_activity`, `opt_in`, `intro_shown` populated.
- `tests/fixtures/json/realistic/prices.json` — 50 stocks, same ids as
  users. Every stock has ≥ 3 history points with ascending naive-UTC
  timestamps. Total `price_history` rows: 252.
- `tests/fixtures/json/realistic/funds.json` — 31 entries: 30 funds + the
  `events_wallet` pseudo-fund. 12 funds have ≥ 2 investors (spec required
  ≥ 10). All investor ids exist in `users.json`.
- `tests/fixtures/json/realistic/fund_penalties.json` — 10 entries. All
  `penalty_until` timestamps strictly later than `2026-06-01T00:00:00`
  (no already-expired rows). APRs mix `0.05` and `0.10`.

### Integration test

- `tests/integration/test_migration_realistic.py` — single
  `@pytest.mark.asyncio` test against
  `sqlite+aiosqlite:///:memory:` with `guild_id="999"`. Covers all three
  AC-A2 sub-criteria in one function:
  - **(a)** Run `migrate()`; assert the returned dict equals an
    `_expected_counts()` value derived directly from the source JSON
    (structural, not magic-numbered). Expected: 50/68/40/50/252/31/48/10.
  - **(b)** Spot-check 5 read-side methods — `UserRepo.list_all`,
    `FundRepo.list_all`, `PriceRepo.list_all`, `PriceRepo.get_history`,
    `PenaltyRepo.list_all` — each compares to a value derived from the
    JSON the test itself parsed (sampled cash balance, current price,
    history length, investor count, APR + UTC-aware `penalty_until`).
  - **(c)** Re-run `migrate()` on the **same** engine + sessionmaker; assert
    the returned counts dict equals the first run's dict exactly. Also
    re-asserts the live `list_all` counts to prove no duplicates or
    orphans were created on the second pass. (Choice: same engine, not a
    fresh one — the migrator's idempotency guarantee is rooted in
    `session.merge` on natural keys + explicit history clear-and-append,
    so re-running on the populated DB exercises the merge path; rebuilding
    the schema would only re-test the first pass.)

## RED-first verification

- Initial test run (full fixture set present): GREEN.
- Temporarily moved `users.json` aside → test FAILS with
  `FileNotFoundError: ... realistic/users.json` (RED captured). Restored
  → GREEN again. This proves the assertions are wired to real fixture
  loading; without the data, the test does not coincidentally pass.

## Gate output

```
$ uv run pytest tests/integration/test_migration_realistic.py -v
tests/integration/test_migration_realistic.py::test_migrate_realistic_fixtures_round_trip_and_idempotent PASSED [100%]
1 passed, 1 warning in 4.24s

$ uv run pytest tests/ -q
787 passed, 1 warning in 12.94s        # 786 baseline + 1 new

$ uv run ruff check tests/integration/test_migration_realistic.py
All checks passed!

$ uv run ruff format --check tests/
76 files already formatted

$ uv run mypy tests/integration/test_migration_realistic.py
Success: no issues found in 1 source file
```

## Next steps (for Phase 15b)

1. Modify `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`:
   add `--dry-run` flag (skip writes), `--report` flag (print per-table
   counts), and a post-migration orphan-warning pass that walks every
   `LongPosition.target_user_id` / `ShortPosition.target_user_id` and
   logs (does NOT fail) any reference that lacks a matching `UserAccount`
   row.
2. Keep the existing test green untouched — the fixtures are intentionally
   orphan-free in 15a, so 15b's orphan check is exercised by a separate
   future fixture or a unit test, not by tweaking
   `tests/fixtures/json/realistic/`.
3. After 15b lands, the manual smoke from `docs/04-migration-plan.md` §15
   verification gate can be exercised:
   `uv run python -m friendex.adapters.persistence.migrate_json_to_sqlite \
     --source tests/fixtures/json/realistic/ \
     --target sqlite+aiosqlite:///:memory: --dry-run --report`.

## Open questions / risks

- **Schema interpretation.** The task spec used loose field names
  (`daily_streak`, `last_daily_claim`, `activity.yesterday`,
  `ping_responses`) that do not match the migrator's actual parser. The
  fixtures follow the migrator's contract (verified by reading the small
  fixtures at `tests/fixtures/json/{users,prices,funds,fund_penalties}.json`
  and `migrate_json_to_sqlite.py`). If reviewers expected literal field
  names, that would be a non-issue for 15b's scope but worth confirming
  on PR.
- **`hedge_funds == 31`.** The migrator counts `events_wallet` as a
  hedge-fund row (it is written via `SqlFundRepository.upsert`), so the
  test asserts 31, not 30. This matches the small fixture in
  `funds.json` which also includes `events_wallet`.

## References

- Spec: `docs/04-migration-plan.md` §Phase 15 (lines 793-824)
- Migrator: `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`
  (return-dict keys at line 471-480)
- Repos:
  - `src/friendex/adapters/persistence/user_repo.py:101` (`list_all`)
  - `src/friendex/adapters/persistence/fund_repo.py:96` (`list_all`)
  - `src/friendex/adapters/persistence/price_repo.py:93` (`list_all`)
  - `src/friendex/adapters/persistence/price_repo.py:123` (`get_history`)
  - `src/friendex/adapters/persistence/penalty_repo.py:78` (`list_all`)
- Schema reference: `tests/fixtures/json/{users,prices,funds,fund_penalties}.json`
- Pattern reference: `tests/integration/test_full_command_flow.py`
- Prior context: `baton-runner/br-2026-05-27-phase-14/digest-phase-14.md`
- Issue: #2 (Phase-15a row)
