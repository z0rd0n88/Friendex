# Pass-Baton: Phase 8a fix — composite `(guild_id, user_id)` lock key

**Date:** 2026-05-25
**Scope:** phase-8a
**Branch:** feat/phase-8a-activity
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD (pre-fix):** c104f3b chore(phase-8a): review iter1 — VERDICT ISSUES

## Where things stand

Applied the HIGH fix from baton `003-2026-05-25-phase-8a-review.md`: every
`lock_manager.locked(...)` call site in `ActivityService` and `VoicePingService`
now composes `f"{self._guild_id}:{user_id}"` rather than passing the bare
`user_id`, restoring the ADR-0001 per-guild market isolation invariant against
a single shared `LockManager` (Phase 14 wiring). All gate checks pass (ruff,
ruff-format, mypy, pytest 430 passed — previously 429 + the new isolation
test). Two LOW findings were left untouched per scope ("deferred to Phase 12";
the reviewer's rationale was that there is no concurrent/real caller until
listeners drive these paths).

## What changed

### TDD: RED-first isolation test (load-bearing)

Added `test_same_user_in_two_guilds_does_not_serialise_on_shared_lock_manager`
in `tests/application/test_activity_service.py`. The test:

1. Builds **two** `ActivityService`s with different `guild_id` (guild A vs
   guild B) but a **single shared `LockManager`** — exactly the Phase 14
   topology.
2. Wraps `FakeUserRepo` in a `_BarrierUserRepo` whose `upsert` parks on an
   `asyncio.Barrier(2)`: both calls must arrive before either proceeds.
3. Drives concurrent `record_message(USER, ...)` on both services and asserts
   the `asyncio.gather` completes inside a 1.0s `wait_for`.

**Captured RED output (against bare-`user_id` keying, on `c104f3b`):**

```
FAILED tests/application/test_activity_service.py::test_same_user_in_two_guilds_does_not_serialise_on_shared_lock_manager
tests/application/test_activity_service.py:476: in upsert
    await self._barrier.wait()
...
E   asyncio.exceptions.CancelledError
The above exception was the direct cause of the following exception:
tests/application/test_activity_service.py:535: in test_same_user_in_two_guilds_does_not_serialise_on_shared_lock_manager
    await asyncio.wait_for(
E   TimeoutError
============================== 1 failed in 1.06s ===============================
```

Guild B's `record_message` was serialised behind guild A's held lock (both
keyed `"5001"`), so it never reached the barrier — exactly the violation
ADR-0001 forbids. The test is genuinely load-bearing: reverting the
`_lock_key` helper would put the suite back into this timeout.

### GREEN: composite key in both services

`src/friendex/application/activity_service.py`:
- Added `_lock_key(self, user_id) -> str` returning `f"{self._guild_id}:{user_id}"`.
- Rewrote the four `locked(...)` call sites (`_mutate`, `_apply_stay_boost`,
  `reset_today_buckets` loop, `reset_week_buckets` loop) to pass
  `self._lock_key(...)`.
- Updated module docstring to document the composite key contract.

`src/friendex/application/voice_ping_service.py`:
- Added the same `_lock_key` helper.
- Rewrote the two `locked(...)` call sites (`_apply_join_boost`, `_credit`) to
  pass `self._lock_key(...)`.
- Updated module docstring likewise.

`tests/application/test_activity_service.py`:
- Added `_BarrierUserRepo` test double + the new isolation test described
  above. (Imports `asyncio` at module scope.)

No other files touched. No new dependencies (`pyproject.toml` / `uv.lock`
untouched).

### Verification (live output, this worktree, post-fix)

```
$ uv run ruff check src tests
All checks passed!

$ uv run ruff format --check src tests
64 files already formatted

$ uv run mypy src/friendex
Success: no issues found in 31 source files

$ uv run pytest tests/application/ -v
============================== 79 passed in 0.44s ==============================

$ uv run pytest
============================= 430 passed in 6.34s ==============================
```

Phase 8a's original 79 application tests + the new isolation test all PASS,
and no other tests regressed (430 total, up from 429).

## Deliberately deferred (per scope of this baton)

The reviewer's two LOW findings were left in place as deliberate carries
forward to Phase 12, where listeners actually drive these paths. Their
rationale (no concurrent/real caller until Phase 12) is preserved as-is:

1. **LOW — `VoiceSessionStore.link_ping` in-place set mutation**
   (`src/friendex/application/voice_session_store.py:~61`). Volatile in-memory
   state, store-owned, no caller in Phase 8a; fix when wired by Phase 12
   listeners — rebuild with `replace(session, from_ping_message_ids=session.from_ping_message_ids | {message_id})`
   under the lock. **Carry into 8b–8f digest** ("volatile state still follows
   the immutability rule") so it does not establish a precedent.
2. **LOW — `reward_voice_ping_response` RMW not atomic across awaits**
   (`src/friendex/application/voice_ping_service.py:~131-146`). Per-user
   `LockManager` keys on the responder/host, not the ping session; concurrent
   responders could race the cap-check vs. write. No concurrent caller until
   Phase 12; fix when listeners drive it — either a store-level lock spanning
   the RMW or a ping-`message_id`-keyed lock for the duration. **Carry into
   8b–8f digest** ("session-level RMW atomicity matters for ping sessions").

Both are surfaced to whoever writes `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md`
so the convention propagates.

## Next steps (handoff)

1. Re-review for CLEAN — the HIGH is closed by a RED-verified load-bearing
   test + a localised structural fix at six call sites. Both deferred LOWs are
   documented with rationale (above) and carried into the digest plan.
2. Write `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md` capturing the
   composite-key convention as a rule for 8b–8f (TradingService, FundService,
   etc.) and the two deferred LOWs as Phase 12 follow-ups.
3. Squash-and-merge the iter-2 work to close Phase 8a on issue #2; clean up
   the worktree and branch after the auto-delete.

## References

- Review under fix: `baton-pass/phase-8a/003-2026-05-25-phase-8a-review.md`
- Work baton (pre-review): `baton-pass/phase-8a/002-2026-05-25-phase-8a-complete.md`
- ADR mandating the key: `docs/adr/0001-per-guild-markets.md:72`
- Phase-7 digest rule 5: `baton-runner/br-2026-05-25-phase-7/digest-phase-7.md`
- Issue: #2 (phase status)
- Files changed in this fix:
  - `src/friendex/application/activity_service.py`
  - `src/friendex/application/voice_ping_service.py`
  - `tests/application/test_activity_service.py`
