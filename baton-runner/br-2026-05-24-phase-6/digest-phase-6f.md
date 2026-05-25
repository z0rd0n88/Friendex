# Phase 6f digest — JSON->SQLite migrator (Phase 6 COMPLETE)

One-shot migrator porting the original single-file bot's four guild-less JSON
files into the new per-guild SQLite schema, via the 6c-6e repositories. Review
VERDICT CLEAN: gate green (362 tests, ruff, mypy), all 4 ACs mutation-verified,
no new deps. **With 6f, all of Phase 6 (6a-6f) is done — the single Phase 6
draft PR (head `feat/phase-6-repos`, base `main`, `Refs #2`) can now open.**

## CLI contract

`python -m friendex.adapters.persistence.migrate_json_to_sqlite \`
`  --source <dir> --target <async-url> --guild-id <id>`

- **All three flags REQUIRED.** `--source` = dir holding `users.json` /
  `prices.json` / `funds.json` / `fund_penalties.json` (missing file = empty set).
  `--target` = SQLAlchemy async URL (e.g. `sqlite+aiosqlite:///data/friendex.db`);
  schema is `create_all`-ed on the FK-enforcing engine.
- **`--guild-id` is a deliberate addition beyond the plan snippet**, mandated by
  ADR-0001: the original data is guild-less, the new schema keys every row by
  `(guild_id, user_id)`, so all migrated rows land under this one supplied guild.
  Required-no-default is intentional (a default would misplace economies).
- `main(argv) -> int`: 0 on success; 1 on missing source dir / caught OSError /
  ValueError. Logs per-table row counts; logs a final total.
- Public API: `migrate(source, maker, *, guild_id) -> dict[str,int]`
  (re-exported from `adapters/persistence/__init__.py`).

## Invariants preserved

- **Decimal, not float:** `json.load(..., parse_float=Decimal)` + `_to_decimal`
  (int via `Decimal(v)`, str via text). Quantisation kept.
- **UTC-aware:** `_to_utc` localises naive (the original `utcnow()` strings) to
  UTC, converts aware to UTC. `UtcDateTime` rejects naive at bind.
- **FK-safe order:** stocks before price history; aggregate repos insert parent
  before children (ADR-0002 PRAGMA foreign_keys=ON).

## Idempotency mechanism

Merge-based tables (users/longs/shorts/buckets, stocks, funds/investors,
penalties) ride the repos' `session.merge` on natural PKs (+ delete-then-reinsert
children) — a 2nd run is an UPDATE, no dups. **price_history is the exception:**
it is append-only with a surrogate autoincrement PK, so `_clear_price_history`
issues a bulk DELETE per stock before re-appending. A 2nd full migrate keeps
counts identical (verified: disabling the clear makes 6->12 and fails the test).

## Non-blocking follow-ups

- MEDIUM: `main()` `except` misses `ArithmeticError`/`KeyError` from corrupt data.
- NOTE: plan CLI snippets (docs/04 line 360, ~808) omit `--guild-id`; Phase 15
  adds `--dry-run`/`--report` to this same module.
