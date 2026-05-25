# Phase-6 hardening digest — H1 (N+1) + H2 (migrator errors)

**Status:** CLEAN (gate green; both fixes RED-verified under reversion; no new deps).

## H1 — SqlUserRepository batched child loads
- `list_all` / `list_active_in_last` now delegate to `_rebuild_many`: after the
  single parent SELECT, each child table (long, short, bucket, voice) loads once
  via `WHERE guild_id = :g AND <owner/user>_id IN (:ids)`, grouped in memory.
- **Constant-query-count guarantee:** <= 6 SELECTs per list call regardless of N
  (was ~5N+1). Guarded by `test_list_*_query_count_is_bounded` (RED at 21 for N=4).
- Voice channels load with `ORDER BY channel_id` (both `get` and list paths) for
  deterministic order. Output byte-equivalent to old per-user `_rebuild`.

## H2 — migrator MigrationError contract
- New `MigrationError(Exception)`: raised at the load / per-record-mapping boundary
  for corrupt-but-parseable data (bad JSON, non-dict top level, missing required
  key, non-numeric money, bad timestamp). Message names file + record + field;
  cause chained via `raise ... from`.
- `_record_context` maps `KeyError`/`ArithmeticError`/`ValueError`/`TypeError` per
  record; `main()` catches only `MigrationError` + `OSError` → `return 1` (no
  traceback). Programmer bugs (`AttributeError`, …) still propagate — no swallowing.
