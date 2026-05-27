# Pass-Baton: Sub-unit 6f — JSON→SQLite migrator complete

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** 843cf2d chore(phase-6): 6e review CLEAN + phase-exit digest

## Where things stand

Sub-unit 6f (the final Phase 6 unit) is code-complete and gate-green, **not yet
committed**. Built the one-shot JSON→SQLite migrator
(`src/friendex/adapters/persistence/migrate_json_to_sqlite.py`), four synthetic
JSON fixtures shaped like the original bot's files, and a 12-test suite. TDD red
was captured (`ModuleNotFoundError` on the new module). Full gate passes:
`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/selfcheck-6f/` → **GATE: PASS**
(pytest, ruff check, ruff format, mypy). No new dependencies. With this, all six
Phase 6 sub-units (6a–6f) are done; the single Phase 6 draft PR can now open.

## What the migrator does (acceptance criteria all met)

- **CLI:** `python -m friendex.adapters.persistence.migrate_json_to_sqlite
  --source <dir> --target <async-url> --guild-id <id>`. `argparse` with all three
  required; `main(argv) -> int` returns 0 / non-zero (missing source dir → 1).
  Verified by running `python -m …` against the fixtures — logs per-table row
  counts and exits cleanly.
- **`--guild-id` is REQUIRED and is a deliberate addition** beyond the plan's
  `--source/--target` example. The original bot was single-guild (bare `user_id`
  JSON keys); the new schema keys every row by `(guild_id, user_id)` per ADR-0001,
  so a target guild is structurally mandatory. All migrated rows land under that
  one guild.
- **Decimal, never float:** JSON decoded with `json.load(..., parse_float=Decimal)`
  so `9876.54` → `Decimal('9876.54')` from its text. `_to_decimal` also handles
  int / str. Quantisation preserved (tests assert `as_tuple().exponent`).
- **UTC-aware datetimes:** `_to_utc` parses ISO strings; naive (the original
  `datetime.utcnow().isoformat()`) → `replace(tzinfo=UTC)`, aware → `astimezone(UTC)`.
  `UtcDateTime` rejects naive at bind, so this is mandatory.
- **FK-safe order:** writes go through the 6c–6e repos' `upsert`/`append_history`;
  aggregate repos insert parent before children internally. Stocks upserted before
  price history.
- **Idempotent:** repo upserts `session.merge` on natural PKs. Price history is
  the one append-only table — `_clear_price_history` (a single bulk DELETE per
  stock) runs before re-append, so a 2nd run replaces rather than duplicates.
  **Proven non-vacuously:** two `python -m` runs leave `price_history` at 6 rows,
  not 12. The migration target uses the FK-enforcing `build_engine` (PRAGMA ON).

## Public surface added

- `migrate(source: Path, maker, *, guild_id: str) -> dict[str, int]` — the async
  workhorse; returns `{table: rows_written}` for 8 tables. Re-exported from
  `adapters/persistence/__init__.py` (`__all__` now includes `"migrate"`).
- `main(argv: list[str] | None = None) -> int` and `build_parser()` — CLI.
- Files: migrator module; `tests/adapters/persistence/test_migrate_json.py`;
  `tests/fixtures/json/{users,prices,funds,fund_penalties}.json`.

## Next steps

1. **Commit 6f** on `feat/phase-6-repos` (7th plan commit):
   `feat(persistence): json-to-sqlite migrator + fixtures`. Stage the migrator,
   the `__init__.py` re-export, the test, and the 4 JSON fixtures.
2. **REVIEW unit** for 6f (gate + code-review + ecc-security-review), then write
   `digest-phase-6f.md` under `baton-runner/br-2026-05-24-phase-6/`.
3. **Open the single Phase 6 draft PR** (base `main`, head `feat/phase-6-repos`),
   body `Refs #2`, following `.github/pull_request_template.md`. Update
   `baton-runner/br-2026-05-24-phase-6/STATE.md` → status DONE.

## Open questions / risks

- **`--guild-id` not in the plan's CLI snippet.** A reviewer may want the plan's
  Phase 6 / Phase 15 CLI examples updated to include `--guild-id` (Phase 15 also
  adds `--dry-run`/`--report` flags to this same module — out of 6f scope).
- **events_wallet manager_id divergence (cosmetic):** the migrator faithfully
  writes the source's `manager_id` (fixture: `"events_wallet"`), whereas
  `SqlFundRepository.ensure_events_wallet` creates new wallets with `manager_id="0"`.
  Migration preserving source data is correct; flagged so it isn't mistaken for a bug.

## References

- PRs: none yet (Phase 6 draft PR to open after 6f review)
- Issues: #2 (master tracking — Phase 6 box)
- Docs: `docs/04-migration-plan.md` §"Phase 6 — Persistence: Repositories & JSON Migrator"; `docs/spec/original-skeleton.md` (original JSON shapes); ADR-0001 (per-guild), ADR-0002 (FK enforcement)
- Code: `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`; `tests/adapters/persistence/test_migrate_json.py`
- Digests honored: `baton-runner/br-2026-05-24-phase-6/digest-phase-6{a..e}.md`
