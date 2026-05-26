# Pass-Baton: Phase 8a — Activity & Voice Ping services + lock-leak fix

**Date:** 2026-05-25
**Scope:** phase-8a
**Branch:** feat/phase-8a-activity
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** b33a441 chore(phase-8-fakes): review CLEAN — digest + review baton

## Where things stand

In-flight implementation of Phase 8a (migration plan §"Phase 8a — Activity &
Voice Ping Services"). Progress so far:

- **A11 (lock leak fix) — DONE, GREEN.** RED-first test
  `test_cancel_mid_acquire_releases_already_held_locks` added to
  `tests/application/test_lock_manager.py`. RED output:
  `TimeoutError` (lock "a" leaked → re-acquire hung). Fixed
  `src/friendex/application/lock_manager.py` `locked()` to acquire inside the
  `try` and track an `acquired` list, releasing only held locks in `finally`.
  All 5 lock tests pass.
- **voice_session_store.py — written** (VoiceSessionStore + VoicePingSessionStore;
  dict + asyncio.Lock wrappers; volatile by design).
- **Settings tunables added** (see Open questions). Config compiles.

Remaining: ActivityService (A1-A6), VoicePingService (A7-A10), their tests,
final gate.

## Key design decisions (CARRY FORWARD)

- **guild_id flow:** service methods do NOT take `guild_id` (per spec
  signatures), but every repo method does. RESOLUTION: `guild_id` is a
  **constructor parameter** of `ActivityService` and `VoicePingService` — one
  service instance per guild economy (consistent with ADR-0001 per-guild
  markets; Phase 14 wiring builds one per guild). Tests pass an explicit
  `guild_id` to the constructor.
- **Immutability:** models are `@dataclass` (NOT frozen) but the fakes return
  stored references. Per fakes-digest convention, NEVER mutate in place; build
  a `dataclasses.replace`d copy and round-trip via `upsert`. ActivityBucket
  has mutable list/`.set` fields — when bumping a bucket counter, replace the
  whole bucket + the account.
- **Locks:** every mutating method serialises under
  `async with lock_manager.locked(user_id)`. One `locked()` call per critical
  section. The injected `LockManager` is a singleton (never construct per call).

## Settings tunables added (DECLARED — new config gaps surfaced)

Added to `src/friendex/adapters/config.py` (defaults from
`docs/spec/original-skeleton.md`):
`voice_ping_first_n_joiners=10`, `voice_ping_join_boost=1.20`,
`voice_stay_boost=1.50`, `voice_stay_bonus_minutes=60.0`,
`voice_ping_base_points=5.0`, `voice_ping_fast_multiplier=3.0`,
`voice_ping_medium_multiplier=2.0`, `voice_ping_slow_multiplier=1.0`,
`voice_ping_host_credit=0.5`, `photo_bonus_points=10.0`.

## Next steps

1. Write `test_activity_service.py` RED tests A1-A6, then
   `src/friendex/application/activity_service.py`.
2. Write `test_voice_ping_service.py` RED tests A7-A10, then
   `src/friendex/application/voice_ping_service.py`.
3. Run gate: ruff, mypy, pytest (test_activity_service, test_voice_ping_service,
   test_lock_manager).

## References

- Plan: `docs/04-migration-plan.md` §"Phase 8a — Activity & Voice Ping Services"
- Spec: `docs/spec/original-skeleton.md` lines 505-586 (voice ping), 636-765
  (on_message / voice state)
- Lock fix finding: `baton-runner/br-2026-05-25-phase-7/baton-phase-7-review-iter-1.md` (MEDIUM)
- Fakes: `tests/application/fakes/fake_repos.py`, fixtures in `conftest.py`
- Issue: #2 (phase status)
