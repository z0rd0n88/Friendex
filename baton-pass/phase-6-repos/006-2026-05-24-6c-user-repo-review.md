# Pass-Baton: 6c SqlUserRepository review — VERDICT CLEAN

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** e0a73c8 feat(phase-6): SqlUserRepository

## Where things stand

Independent review of sub-unit **6c** (`SqlUserRepository`) — reviewer did not
implement it. Reviewed the diff `fbb66a1..HEAD`. **VERDICT: CLEAN.** The gate is
green, all four acceptance criteria are met, the AC3 deletion-cascade keystone
was *independently re-proven non-vacuous*, no new dependencies were added, and
the `persistence/__init__.py` edit is a benign re-export. No CRITICAL or HIGH
findings. The MEDIUM/LOW items below are advisory follow-ups, none blocking; 6c
may proceed to commit and on to 6d. Current blocking state: **none**.

## Verification (actual output)

`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/gate-phase-6c-iter-1/`:
```
PASS pytest
PASS ruff-check
PASS ruff-format
PASS mypy
----
GATE: PASS   (exit 0)
```
`user_repo.py` coverage 98% (74 stmts, 1 missed = line 189, the disclosed
unreachable defensive bucket-absent branch). Full user-repo suite: 9 passed.

## AC verification (all met)

- **AC1 (full IUserRepo surface + mypy structural conformance):** repo
  implements `get`/`upsert`/`delete`/`list_all`/`list_active_in_last` — exactly
  the 5 methods in `interfaces.py::IUserRepo` (cross-checked). `conforming:
  IUserRepo = repo` typed assignment in `test_user_repo.py:157` gates structural
  conformance; mypy passes. ✅
- **AC2 (full-aggregate round trip):** `test_upsert_then_get_round_trips_full_aggregate`
  persists longs, shorts, BOTH buckets, and voice channels, asserts whole-object
  `result == account`, plus explicit Decimal-scale checks via
  `as_tuple().exponent` (`_same_scale`) and tz-aware assertions on every
  datetime. ✅
- **AC3 (deletion-cascade keystone — NON-VACUOUS, independently confirmed):**
  `test_delete_cascades_to_all_children` asserts children exist before delete
  (`before[...] == 1/2/1/2/3`) then asserts all five counts `== 0` after.
  `delete()` issues a *single parent DELETE* (`user_repo.py:83-91`) — no
  hand-rolled child cleanup — so the zero-orphan assertion can only pass via
  DB-level CASCADE. **Reviewer re-ran the same upsert+delete on an engine built
  *without* the PRAGMA listener (FK OFF): it left 1 orphan `long_positions` row.**
  The test therefore genuinely exercises PRAGMA + ON DELETE CASCADE. ✅
- **AC4 (mypy + ruff clean):** gate confirms. ✅

## Findings by severity

### MEDIUM — N+1 query pattern in `list_all` / `list_active_in_last`
`user_repo.py:105,125` rebuild each row via `_rebuild`, which fires 3 child
SELECTs + up to 2 voice-channel SELECTs per user (`user_repo.py:145-198`). For N
users that is ~5N queries. Not a correctness bug (local SQLite, guild-bounded N)
but `interfaces.py:119` flags `list_active_in_last` as on the activity-tick /
inactivity-decay hot path. **Fix (deferrable):** batch the children with a single
`IN (user_ids)` query per child table (or `selectinload`-style eager load) and
group in memory. Later repos (6d–6f) face the same shape — see digest.

### LOW — voice-channel list order is implementation-defined
`_rebuild_bucket` (`user_repo.py:190-197`) loads `VoiceUniqueChannelORM` with no
`ORDER BY`; the round-trip test asserts list order (`== ["c1","c2"]`,
`test_user_repo.py:205`). It passes because SQLite returns composite-PK rows in
insertion/rowid order, but the equality is latently order-fragile if the backend
or query plan changes. **Fix (optional):** add a deterministic `.order_by(
VoiceUniqueChannelORM.channel_id)` (and accept set-equality in the test), or
document the reliance. Domain treats the field as a `list`, so order is not
semantically meaningful — low priority.

### LOW — `_delete_children` redundant with CASCADE (intentional, documented)
`upsert` explicitly deletes all four child tables (`user_repo.py:225-258`) before
re-insert, even though child FKs cascade from the parent. The parent row is
`merge`d (kept), so children would NOT cascade-delete on upsert — the explicit
deletes are *required* here, not redundant; the docstring's rationale is sound.
No action; noted so 6d–6f reuse the same merge-parent + explicit-child-wipe
pattern rather than assuming cascade covers upsert.

## Security review (ecc-security-review) — no issues

- **SQLi:** all statements are SQLAlchemy Core (`select`/`delete`/`func.count`)
  with bound params; zero string interpolation. CLEAN.
- **Secrets:** none; the test guild id is a fake fixture value. CLEAN.
- **eval/exec/unsafe deser:** none. **Async blocking:** all DB ops awaited inside
  `async with` sessions (no leaks). **Mutable defaults:** none. CLEAN.

## Scope-creep check

`persistence/__init__.py` (nominally 6f's `__all__` work) only adds a
`SqlUserRepository` re-export + `__all__ = ["SqlUserRepository"]`
(`git diff fbb66a1..HEAD -- .../__init__.py`). Benign, breaks nothing, imports
clean. No `pyproject.toml` / `uv.lock` change → **no new dependency.**

## Next steps

1. Commit 6c as one `feat(persistence): SqlUserRepository` per the plan boundary.
2. Proceed to 6d (`SqlPriceRepository`: `append_history`, `get_history(since=)`,
   `prune_history_older_than`).
3. Optional pre-9 follow-up: batch the N+1 in `list_all` /
   `list_active_in_last` before background loops start hammering them.

## References

- Code: `src/friendex/adapters/persistence/user_repo.py`,
  `src/friendex/adapters/persistence/__init__.py`
- Tests: `tests/adapters/persistence/test_user_repo.py`
- Contract: `src/friendex/application/interfaces.py` (`IUserRepo`)
- Work baton under review: [005](./005-2026-05-24-6c-user-repo.md)
- Prior: [002](./002-2026-05-24-6a-fk-migration-review.md) (FK/CASCADE),
  [004](./004-2026-05-24-6b-interfaces-review.md) (IUserRepo surface)
- Digest: `baton-runner/br-2026-05-24-phase-6/digest-phase-6c.md`
- Issue: #2; ADR-0002 (SQLite FK enforcement)
