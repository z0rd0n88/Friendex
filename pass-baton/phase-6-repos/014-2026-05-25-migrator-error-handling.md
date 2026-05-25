# Pass-Baton: H2 — migrator error-handling (MigrationError + clean exit 1)

**Date:** 2026-05-25
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** 0a54d61 perf(phase-6): batch SqlUserRepository child loads (fix N+1)

## Where things stand

Fixed the MEDIUM (narrow exception handling) + adjacent LOW (no JSON shape
validation) findings from the 6f migrator review (baton 012, §"Findings by
severity"). Corrupt-but-parseable source data now exits cleanly with a specific
operator-facing message instead of a raw traceback. **Done and gate-green;
not yet committed** (manager owns git).

What changed in `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`:

1. New `MigrationError(Exception)` raised at the load / record-mapping boundary.
2. `_load_json_object` now (a) maps `json.JSONDecodeError` → `MigrationError`
   and (b) validates the decoded top level is a `dict`, else raises
   `MigrationError("…expected a JSON object mapping id -> record, got a
   top-level list")` (the LOW fix).
3. New `_record_context(filename, record_id)` context manager wraps each
   per-record mapping in the four `_migrate_*` loops; it maps `KeyError`
   (missing required field), `ArithmeticError` (incl. `decimal.InvalidOperation`),
   `ValueError`/`TypeError`, **and** a nested field-level `MigrationError` into
   one `MigrationError` naming file + record key (+ field), chained via
   `raise … from`.
4. `_to_decimal(value, field=...)` — when a money field is non-numeric and a
   `field` name is supplied, raises a field-level `MigrationError` (offending
   value included) instead of leaking `decimal.InvalidOperation`. Threaded
   `field=` through `_build_user_account`'s `cash_balance`/`net_worth`/
   `month_start_net_worth`.
5. `main()` catches `MigrationError` → "migration failed — corrupt source data:
   …" and `OSError` → "migration failed — I/O error: …", each `return 1`.
   Unexpected programmer errors (`AttributeError`, etc.) are deliberately NOT
   caught — they still surface as tracebacks (no silent swallow).

TDD: wrote 3 RED tests first, confirmed each failed with an uncaught exception
under the old `except (OSError, ValueError)`, then implemented to green.

### RED (captured against the pre-fix narrow except)

```
E   decimal.InvalidOperation: [<class 'decimal.ConversionSyntax'>]   migrate_json_to_sqlite.py:132  (non-numeric money)
E   KeyError: 'cash_balance'                                          migrate_json_to_sqlite.py:208  (missing required field)
E   AttributeError: 'list' object has no attribute 'items'           migrate_json_to_sqlite.py:290  (top-level list)
3 failed, 12 deselected
```

### GREEN — live message check (run from worktree)

```
case a: users.json: record '111': field 'cash_balance' is not a number: 'not-a-number'   exit=1
case b: users.json: record '111' is missing required field 'cash_balance'                exit=1
case c: users.json: expected a JSON object mapping id -> record, got a top-level list     exit=1
```

### Gate — PASS

`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/selfcheck-h2/` → `GATE: PASS`:

```
PASS pytest        (369 passed)
PASS ruff-check    (src tests alembic)
PASS ruff-format
PASS mypy          (src/friendex)
```

3 new tests added to `tests/adapters/persistence/test_migrate_json.py`
(`test_cli_non_numeric_money_value_exits_one`,
`test_cli_missing_required_field_exits_one`,
`test_cli_top_level_not_an_object_exits_one`); the 12 prior migrator tests still
pass (15 in that file total).

## Next steps

1. Manager: stage + commit the two changed files (no new deps; `pyproject.toml`
   / `uv.lock` untouched). Suggested: `fix(phase-6): migrator maps corrupt
   source data to clean exit 1 (MigrationError)`.
2. Re-confirm gate before the Phase 6 PR (or include in it) —
   `scripts/gate.sh <log-dir>` must stay `GATE: PASS`.
3. The remaining 6f review items are untouched and still open: the NOTE
   (docs/04-migration-plan.md:360 + ~808-818 omit `--guild-id`) and opening the
   single Phase 6 draft PR — see baton 012 §"Next steps".

## Open questions / risks

- No new dependencies introduced.
- Both MEDIUM and LOW from baton 012 are now closed; no other 6f findings were
  in scope for this fix.

## References

- PRs: none open yet (Phase 6 draft PR still to open)
- Issues: #2 (master tracking — Phase 6 box)
- Review baton this fixes: `pass-baton/phase-6-repos/012-2026-05-24-6f-migrator-review.md` §"Findings by severity"
- Code: `src/friendex/adapters/persistence/migrate_json_to_sqlite.py` (`MigrationError`, `_load_json_object`, `_record_context`, `_to_decimal`, `main`)
- Tests: `tests/adapters/persistence/test_migrate_json.py` (3 new corrupt-data tests)
- Gate logs: `baton-runner/br-2026-05-24-phase-6/selfcheck-h2/`
