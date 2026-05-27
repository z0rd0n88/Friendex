# Phase 15b review — VERDICT CLEAN

- Date: 2026-05-27
- Worktree: `.claude/worktrees/br-2026-05-27-phase-15`
- Branch: `feat/phase-15b-migrate-flags`
- Base: `feat/phase-15a-fixtures@3043716`
- HEAD: `8249b09` ("feat(phase-15b): migrator --dry-run, --report, orphan consistency check")
- Reviewer mode: independent (no pre-read of the work diff).

## Gate

`scripts/gate.sh baton-runner/br-2026-05-27-phase-15/gate-phase-15b-iter-1/` → **GATE: PASS**
(pytest, ruff-check, ruff-format, mypy — all green).

## Acceptance-criteria verification

| AC | Status | Evidence |
|----|--------|----------|
| B1 — `--dry-run` end-to-end run, no writes to target, exit 0 | MET | `migrate_json_to_sqlite.py:602` rebinds `effective_target` to `_DRY_RUN_TARGET = "sqlite+aiosqlite:///:memory:"` when `dry_run` is set, so the operator-supplied target URL is never opened. Test `test_dry_run_writes_nothing_to_target` round-trips the same `tmp_path` URL after the CLI returns and finds 0 users. |
| B2 — `--report` prints `<table>: <count>` lines sorted by table; composes with `--dry-run` | MET | `_print_report` iterates `sorted(counts)` and `if args.report:` in `main()` is unconditional of `dry_run` (line 659). Test asserts (a) one line per expected table, (b) lines equal `sorted(table_lines)`, (c) parsed counts equal `migrate()` return dict for the same fixtures. |
| B3 — Post-migration orphan check, unconditional, warn-not-fail, includes owner_id/target_id/side | MET | `_warn_orphan_positions` invoked in `_run` after `migrate()` regardless of `dry_run` (line 614). Warning string: `"orphan %s position: owner=%s target=%s has no matching UserAccount"` with `(side, owner_id, target_id)` — all three fields present. Never raises (pure dict walk + `logger.warning`). Test exit-code is 0. |
| B4 — Tests cover B1/B2/B3, RED-first, non-tautological | MET | See RED-under-mutation table below. |

## RED-under-mutation verification (B4)

Each mutation applied under revert to a `/tmp/migrate_backup.py` copy of `8249b09`; source restored byte-identically after each run (verified `diff` returned no output).

| AC | Mutation | Result |
|----|----------|--------|
| B1 | Replace `effective_target = _DRY_RUN_TARGET if dry_run else target` with `effective_target = target` (dry-run no longer redirects). | `test_dry_run_writes_nothing_to_target` FAILED — 50 user accounts persisted to target. RED confirmed. |
| B2 | `sorted(counts, reverse=True)` in `_print_report`. | `test_report_prints_counts_in_sorted_order` FAILED — sort-order assertion tripped. RED confirmed. |
| B3 | Comment out the `_warn_orphan_positions(source)` call in `_run`. | `test_orphan_position_is_warned_not_failed` FAILED — no WARNING naming target id `9999`. RED confirmed. |

Post-restore re-run of `tests/integration/test_migration_dry_run.py` → 3 passed.

## Continuity / containment checks

- `git diff 3043716 -- tests/fixtures/json/realistic/ tests/integration/test_migration_realistic.py` → empty (Phase 15a artifacts byte-identical).
- `git diff 3043716 -- pyproject.toml uv.lock` → empty (no new runtime dependencies).
- `--guild-id` remains `required=True` in `build_parser` (line 562) — signoff Q3 honoured.
- Orphan fixture (`tests/fixtures/json/realistic_orphan/users.json`) genuinely seeds an orphan: owner `2001` holds a long position on `9999`, and `9999` is absent from the top-level user map. Owner `2002` shorts `2001` (not orphan) — confirms the test would not warn for that side.

## Findings

### CRITICAL
_None._

### HIGH
_None._

### MEDIUM
_None._

### LOW

- **L1 — `_warn_orphan_positions` reloads `users.json`.** `migrate_json_to_sqlite.py:517` calls `_load_json_object` a second time (the first happens inside `_migrate_users`). For a one-shot CLI this is harmless, but on very large source files it doubles the JSON parse cost. *Fix (optional):* thread the already-loaded `users` mapping out of `_migrate_users` or split the loader from the writer. Defer — not blocking.

- **L2 — Orphan test only asserts target id substring.** `tests/integration/test_migration_dry_run.py:238` checks `"9999" in record.getMessage()` but does not pin that `owner_id` (`2001`) or `side` (`long`) appear in the warning. The implementation does include both, but the test would still pass if a future refactor dropped them. *Fix (optional):* extend the assertion to `all(token in msg for token in ("9999", "2001", "long"))`. Defer.

### INFO

- **I1 — Dry-run semantics use a separate in-memory engine rather than open-then-rollback.** The signoff's parenthetical ("opens engine, would-write rows") is satisfied in spirit — the writes DO happen, against a throwaway in-memory engine, so counts are real and the target URL is never opened. Documented in the module docstring (lines 23–32). Phase 16+ tooling that wants to confirm a target URL is reachable on dry-run will need a separate `--check-target` style flag.

- **I2 — `_DRY_RUN_TARGET` is a private module constant.** Downstream tests / scripts that need to assert "dry-run used a throwaway engine" must do so behaviourally (target DB still empty) rather than by importing the constant.

## VERDICT

**CLEAN.**
(a) gate green; (b) 0 CRITICAL/HIGH; (c) all four ACs met; (d) B1/B2/B3 each RED-under-mutation; (e) Phase 15a artifacts byte-identical to `3043716`; (f) no new dependencies.

Phase-exit digest written to `baton-runner/br-2026-05-27-phase-15/digest-phase-15b.md`.
