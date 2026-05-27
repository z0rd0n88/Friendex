# Pass-Baton: Independent review of hardening fixes H1 + H2 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** ddadffb fix(phase-6): harden JSON migrator error handling

## Where things stand

Independently reviewed the two targeted hardening fixes (`git diff
d093266..HEAD`): **H1** batched the N+1 child loads in
`SqlUserRepository.list_all` / `list_active_in_last` to a constant query count
(+ deterministic voice-channel `ORDER BY`); **H2** introduced `MigrationError`
so the JSON migrator returns a clean exit 1 with an operator-facing message on
corrupt source data instead of a raw traceback. Gate is **green**, both fixes
are **correct**, and the new tests are **genuinely RED under reversion**
(verified empirically, not just from the implementer batons). No
CRITICAL/HIGH/MEDIUM findings. One LOW (a non-RED test for the LOW ordering
sub-fix) and one informational note. **Blocking state: none — VERDICT CLEAN.**

## Verification (actual output)

Gate (`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/gate-hardening-iter-1/`):
```
PASS pytest   PASS ruff-check   PASS ruff-format   PASS mypy
----
GATE: PASS
```

RED confirmed by reverting each source file to d093266 and running the new tests:
```
H1 (user_repo reverted):
  AssertionError: list_all over 4 users issued 21 SELECTs; expected <= 6
  AssertionError: list_active_in_last over 4 users issued 21 SELECTs; expected <= 6
  (21 == 5N+1 for N=4 — the classic N+1; both pass at <= 6 after the fix)

H2 (migrator reverted to `except (OSError, ValueError)`):
  decimal.InvalidOperation  (non-numeric money — escaped old except)
  KeyError: 'cash_balance'  (missing field — escaped old except)
  AttributeError: 'list' object has no attribute 'items'  (top-level list)
  3 failed → all map to exit 1 after the fix
```
Restored both files to HEAD afterward (no product-code edits left behind).
Full `test_user_repo.py` + `test_migrate_json.py`: **28 passed**.

## Findings by severity

**CRITICAL / HIGH / MEDIUM:** none.

**LOW — `test_list_all_voice_channels_have_deterministic_order` is not RED for
the ordering sub-fix.** `tests/adapters/persistence/test_user_repo.py:459`. The
test passes even with the source reverted to d093266 (where `_rebuild_bucket`
had no `order_by`) — SQLite happened to return the in-memory rows already
sorted, so the test does not actually guard the new `ORDER BY channel_id`. The
ordering fix itself is correct (`order_by` added symmetrically to both
`_rebuild_bucket`:214 and the batched voice load in `_rebuild_many`:264); only
the *test* is weak. **Fix (optional):** make the assertion bite by either
forcing reverse-rowid insertion that SQLite would otherwise return unsorted, or
add a unit-level check that the emitted SELECT contains `ORDER BY`. Non-blocking
— the N+1 query-count tests (the primary H1 guard) are solidly RED.

**INFO — H2 error messages disclose the operator's own source values.**
`migrate_json_to_sqlite.py:168,210,215,219`. `MigrationError` messages include
the record id, field name, and the offending value (`{value!r}`). This is by
design and appropriate: the migrator is a local one-time operator CLI run
against the operator's own JSON backup, output goes to `logger.error` on the
operator console (never to Discord users), and the migrated data is game state
(cash/prices), not secrets (no tokens/passwords). The disclosed value is exactly
what the operator needs to fix. No action needed.

## Correctness notes (why CLEAN)

- **H1 grouping — no cross-user leakage.** Children are filtered by
  `<owner/user>_id.in_(user_ids)` (parameterized `.in_()`, no SQL injection),
  grouped by `owner_id`/`user_id`, then each row pulls only its own bucket via
  `.get(row.user_id, {})`. Voice channels are keyed by `(user_id, bucket_type)`,
  so A's today-channels can't bleed into B or into A's week bucket. Output is
  byte-equivalent to the per-user `_rebuild`: longs/shorts keyed by
  `target_user_id`, today/week buckets, `None` when absent, Decimal scale +
  UTC-aware datetimes preserved (round-trip + cascade tests still pass).
- **H1 edge paths.** Empty `user_ids` returns `[]` early — no `IN ()` error
  (`test_list_all_empty_guild_returns_empty`). Single-`get`/`_rebuild` path is
  unchanged. `_count_selects` test helper removes its event listener in
  `finally` (no listener leak).
- **H2 error mapping is specific, not over-broad.** `_record_context` catches an
  enumerated set (`MigrationError`, `KeyError`, `ArithmeticError`, `ValueError`,
  `TypeError`) scoped to one record's mapping — not a bare `except Exception`.
  `main()` catches only `MigrationError` + `OSError`. Programmer bugs
  (`AttributeError`, etc.) still propagate as tracebacks (no swallowing).
  Dropping `ValueError` from `main()`'s direct catch is safe: every `_build_*`
  / `_to_decimal` / `_require_utc` call site sits inside a `_record_context`, so
  anticipated `ValueError`s (bad timestamps) are mapped before reaching `main()`.
  `_load_json_object` raises `MigrationError` directly for JSON-decode and
  top-level-shape errors.
- **No new dependencies.** `pyproject.toml` / `uv.lock` untouched; added imports
  are stdlib only (`collections.defaultdict`, `contextlib.contextmanager`).

## Next steps

1. (Optional) Strengthen the LOW finding's ordering test so it is RED against
   the pre-`order_by` code — see Fix above. Not required for merge.
2. Proceed to commit/PR the two fix units (manager owns git) and re-confirm
   `scripts/gate.sh <log-dir>` stays `GATE: PASS` before the Phase 6 PR.

## Open questions / risks

- None blocking. Two uncovered defensive `None`-guards (`_rebuild_bucket`:207,
  `_bucket_from_maps`) are intentional symmetric branches (disclosed in baton
  013); no behavioral risk.

## References

- Fixes under review: [013](./013-2026-05-25-user-repo-n1-fix.md) (H1),
  [014](./014-2026-05-25-migrator-error-handling.md) (H2)
- Original findings: [006](./006-2026-05-24-6c-user-repo-review.md) (N+1 + order),
  [012](./012-2026-05-24-6f-migrator-review.md) (narrow except + shape)
- Code: `src/friendex/adapters/persistence/user_repo.py` (`_rebuild_many`,
  `_assemble`, `_bucket_from_maps`, `_children`);
  `src/friendex/adapters/persistence/migrate_json_to_sqlite.py`
  (`MigrationError`, `_record_context`, `_to_decimal`, `_load_json_object`, `main`)
- Tests: `tests/adapters/persistence/test_user_repo.py`,
  `tests/adapters/persistence/test_migrate_json.py`
- Gate logs: `baton-runner/br-2026-05-24-phase-6/gate-hardening-iter-1/`
- Issue: #2
