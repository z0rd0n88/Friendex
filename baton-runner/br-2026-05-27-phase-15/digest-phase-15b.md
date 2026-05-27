# Phase 15b digest — JSON→SQLite migrator: --dry-run, --report, orphan check

HEAD `8249b09`, gate-phase-15b-iter-1 → PASS.

## Public surface added

### CLI flags on `python -m friendex.adapters.persistence.migrate_json_to_sqlite`

- `--dry-run` (boolean, optional, default off). When set, the migration runs end-to-end against a **throwaway in-memory engine** (`sqlite+aiosqlite:///:memory:`). The operator-supplied `--target` URL is **not opened, not schema-created, not written**. Exit code is 0 on success. Composes with `--report`. `--guild-id` remains required.
- `--report` (boolean, optional, default off). When set, after the run prints one line per migrated table to stdout in the form `<table>: <count>`, **sorted alphabetically by table name**. The set of tables (and therefore the line set) is exactly the keys of `migrate()`'s return dict: `fund_investors`, `fund_penalties`, `hedge_funds`, `long_positions`, `price_history`, `short_positions`, `stocks`, `users`. Counts equal the number of records processed (idempotent re-runs report the same numbers).

### Orphan-warning log line

Emitted by `_warn_orphan_positions` (unconditionally, both real and dry-run paths) on logger `friendex.adapters.persistence.migrate_json_to_sqlite`:

```
WARNING orphan <side> position: owner=<owner_id> target=<target_id> has no matching UserAccount
```

- `<side>` is the literal string `long` or `short`.
- `<owner_id>` / `<target_id>` are the bare user-id strings from `users.json`.
- Never raises; migrator exits 0.

## Decisions Phase 16+ must honour

1. **Dry-run target isolation is contractual.** A dry-run MUST NOT open or touch `--target`. Operators rely on running dry-run against a live database URL with zero side effects. Do not "optimise" this into `engine.begin() / rollback` against the real target — the in-memory split is the documented design.
2. **`--report` output ordering is contractual.** Lines are sorted by table name. Downstream tooling and tests may parse the report deterministically; do not change to insertion order or change the `:` / single-space separator.
3. **Orphan check is warn-not-fail and runs on both paths.** Signoff Q2. Do not promote to a non-zero exit, do not gate it behind `--dry-run` or a separate flag.
4. **`--guild-id` stays required.** Signoff Q3. The migrator never silently defaults a guild.
5. **No new runtime dependencies.** `pyproject.toml` / `uv.lock` unchanged since Phase 15a.
6. **Phase 15a artifacts are frozen.** `tests/fixtures/json/realistic/` and `tests/integration/test_migration_realistic.py` are byte-identical to `3043716`. The orphan fixture lives at `tests/fixtures/json/realistic_orphan/` — do not mutate the realistic fixtures to introduce orphans.
