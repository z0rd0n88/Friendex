# Pass-Baton: Phase 8a — services GREEN, raising coverage above gate

**Date:** 2026-05-25
**Scope:** phase-8a
**Branch:** feat/phase-8a-activity
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** b33a441 chore(phase-8-fakes): review CLEAN — digest + review baton

## Where things stand

All 11 acceptance criteria implemented TDD (RED recorded, then GREEN):

- **A11 lock leak** — GREEN. RED was `TimeoutError`. `lock_manager.py` `locked()`
  now acquires inside `try`, tracks `acquired`, releases only held locks.
- **A1-A6 ActivityService** — GREEN (8 tests).
- **A7-A10 VoicePingService** — GREEN (6 tests).

Gate so far: `ruff check` PASS, `ruff format --check` PASS, `mypy src/friendex`
PASS (31 files), 19 target tests PASS. Coverage of the 4 new/modified modules
is exactly 85% (gate `--cov-fail-under=85`) — too tight. Adding tests for the
spec-listed public methods that currently lack direct coverage
(`record_reaction`, `handle_voice_join`, `set_opt_in`, `mark_intro_shown`,
`reset_week_buckets`, VoiceSessionStore helpers) to lift it well clear.

## Files written / modified

- M `src/friendex/application/lock_manager.py` (A11 fix)
- M `src/friendex/adapters/config.py` (tunables — see decl below)
- A `src/friendex/application/voice_session_store.py`
- A `src/friendex/application/activity_service.py`
- A `src/friendex/application/voice_ping_service.py`
- A `tests/application/test_activity_service.py`
- A `tests/application/test_voice_ping_service.py`
- M `tests/application/test_lock_manager.py` (added A11 test)

## DECLARED — new Settings tunables (config gaps surfaced wiring 1st services)

`voice_ping_first_n_joiners=10`, `voice_ping_join_boost=1.20`,
`voice_stay_boost=1.50`, `voice_stay_bonus_minutes=60.0`,
`voice_ping_base_points=5.0`, `voice_ping_fast_multiplier=3.0`,
`voice_ping_medium_multiplier=2.0`, `voice_ping_slow_multiplier=1.0`,
`voice_ping_host_credit=0.5`, `photo_bonus_points=10.0`.
Defaults taken verbatim from `docs/spec/original-skeleton.md`.

## Design decisions (CARRY FORWARD)

- **guild_id is a service CONSTRUCTOR arg** (per-guild service instances), since
  spec method signatures omit guild_id but repos require it. Consistent with
  ADR-0001; Phase 14 wires one service per guild.
- `joined_from_ping` params are kept for signature fidelity; the original applies
  the long-stay boost for any stay >= threshold regardless, so the flag is
  currently unused in the math (documented in code).
- No new third-party dependencies.

## Next steps

1. Add coverage tests for the remaining public methods + store helpers.
2. Re-run full gate; update this scope's baton if anything shifts.

## References

- Plan: `docs/04-migration-plan.md` §"Phase 8a"
- Spec: `docs/spec/original-skeleton.md` (505-586 voice ping; 636-765 handlers)
- Prior baton: `pass-baton/phase-8a/000-2026-05-25-activity-voiceping-services.md`
- Issue: #2
