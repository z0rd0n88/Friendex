# Pass-Baton: Phase 12b complete — voice + message listeners + CF-1/CF-2/CF-4

**Date:** 2026-05-27
**Scope:** phase-12b
**Branch:** feat/phase-12b-listeners-voice
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-12
**HEAD:** ccc1384 chore(phase-12b): log 12b 529-abort + respawn

## Where things stand

All B1–B8 acceptance criteria GREEN under TDD; the work-unit is ready for
review. The tree contains 2 new product files (message_listener,
voice_listener), surgical fixes to 2 application files
(voice_session_store, voice_ping_service), and 2 new + 2 modified test
files. 750/750 pytest, ruff/format/mypy clean, ≥97% coverage on every
in-scope file. Nothing left to do for the work unit — next is the
manager's stage-commit + review iteration.

## What landed (B1–B8 summary)

**B1 `MessageListener`** — `on_message` skips bot authors + DMs, routes
to `ActivityService.record_message(author_id, has_attachment, is_reply,
channel_id)` (the actual signature; STATE.md's `has_media`/`mention_count`
paraphrase mapped to `has_attachment`/`role_mentions`). VC ping path
fires `VoicePingService.register_ping_message` iff the author is in
voice AND a role-mention matches `settings.vc_ping_role_ids` —
exact replica of `is_voice_ping_message` from
`docs/spec/original-skeleton.md:494-503`. 12/12 tests.

**B2 `VoiceListener`** — JOIN / LEAVE / SWITCH ordering implemented in
`on_voice_state_update`. Same-channel no-op (mute/deafen toggles) is
explicit. LEAVE / SWITCH `stay_minutes` is computed from
`VoiceSessionStore.get(user_id)` (`(now - session.start).total_seconds() / 60`);
`joined_from_ping = bool(session.from_ping_message_ids)`. A LEAVE
with no recorded session (restart-while-in-VC) is a silent no-op —
no defensible stay_minutes. 11/11 tests.

**B3 CF-4 wiring** — `VoiceListener` ctor takes a live
`VcBoostTask` instance (single-instance per Phase 9 digest §3) plus a
`voice_session_store_factory: Callable[[str], VoiceSessionStore]`.
After every JOIN / SWITCH (after rewarding the ping), the listener calls
`vc_boost_task.set_store_for_guild(guild_id, await voice_ping_service.collect_extra_boosts(now))`.
LEAVE does NOT seed — the responder has just left, there's no new roster.

**B4 CF-1** — `VoiceSessionStore.link_ping` rebuilds the session via
`replace(session, from_ping_message_ids=session.from_ping_message_ids | {message_id})`
under the existing lock. RED proof: under the pre-fix in-place mutation,
`test_link_ping_rebuilds_session_immutably` observed
`frozenset({4242}) == frozenset()` on the caller's snapshot.

**B5 CF-2** — `VoicePingService._reward_for_session` wraps the
cap-check + placement write under
`locked(f"{guild_id}:ping:{session.message_id}")` (new `_ping_lock_key`
helper), re-reads the session inside the lock, and short-circuits if
the responder was already credited. Boost + credit calls stay outside
the ping lock (they own their own per-user composite locks; no nested
locking risk). RED proof: with cap=1, the unlocked code boosted both
of two racing responders to $120.00 each — captured by a
`_BarrierPingSessionStore` that parks both callers inside `list_all`.

**B6 mutation-hardening** — load-bearing pins for: (a) `author.bot`
skip in message_listener (drops both services if reverted); (b) SWITCH
order via `mock_calls` positional indices (`leave_index < join_index`);
(c) CF-1 reversion (caller's snapshot would observe the mutation);
(d) CF-2 reversion (two responders both pass cap).

**B7 DomainError propagation** — both listeners have `pytest.raises(OptedOut)`
tests on at least one path; no `try/except` anywhere in listener bodies.

**B8 gate**:

```
$ uv run pytest
======================== 750 passed, 1 warning in 9.65s ========================

$ uv run ruff check src tests
All checks passed!

$ uv run ruff format --check src tests
135 files already formatted

$ uv run mypy src/friendex
Success: no issues found in 65 source files
```

Coverage on the 4 files in scope (12b targets):

```
message_listener.py        100%  (38/38 stmts, 12/12 branches)
voice_listener.py           98%  (48/48 stmts, 11/12 branches; one defensive exit)
voice_ping_service.py       97%  (defensive race-loss returns at lines 211, 216)
voice_session_store.py      98%  (no-op exit on unknown user)
```

All ≥ 80% gate.

## Decisions documented for review

1. **CF-4 boost-list source** = new `VoicePingService.collect_extra_boosts(now)`
   query — walks open ping sessions, emits one
   `VcExtraBoost(user_id, ping_time=session.timestamp, last_boost=now,
   end_time=session.timestamp + window)` per `extra_joiner` (recipe from
   `docs/spec/original-skeleton.md:559-563`). Kept the listener
   free of ping-session internals.
2. **`record_message` signature mismatch** with STATE.md paraphrase
   resolved in favour of the existing service contract
   (`author_id, has_attachment, is_reply, channel_id`). `mention_count`
   is not in the service; VC role-mentions are detected separately and
   forwarded into `register_ping_message` (correct architectural split:
   one signal per service).
3. **CF-2 lock scope** — locked only the cap-check + `_ping_sessions.set`
   write. The downstream `_apply_join_boost` + `_credit` calls stay
   outside (each owns its own per-user composite key; no deadlock risk
   with the LockManager's non-reentrant semantics, and no functional
   loss — a racer arriving after the placement write sees the fresh
   `first_10_joiners` and falls through to `extra_joiners`).
4. **`joined_from_ping`** for LEAVE/SWITCH = `bool(session.from_ping_message_ids)`.
   This honours the from-ping-set semantics from B4 (CF-1) — the
   original spec used a single `role_ping` boolean but the rebuild's
   set-of-message-ids is strictly more precise and matches
   `VoiceSession.from_ping_message_ids` already shipped in Phase 3.

## Files in this work-unit

New product:
- `src/friendex/adapters/discord_bot/listeners/message_listener.py`
- `src/friendex/adapters/discord_bot/listeners/voice_listener.py`

Modified product:
- `src/friendex/application/voice_session_store.py` (CF-1 immutable rebuild)
- `src/friendex/application/voice_ping_service.py` (CF-2 per-ping lock + `collect_extra_boosts`)

New tests:
- `tests/adapters/discord_bot/listeners/test_message_listener.py` (12 tests)
- `tests/adapters/discord_bot/listeners/test_voice_listener.py` (11 tests)

Modified tests:
- `tests/application/test_activity_service.py` (CF-1 RED test + existing-comment refresh)
- `tests/application/test_voice_ping_service.py` (CF-2 RED test + `collect_extra_boosts` test + `_BarrierPingSessionStore`)

No new deps. No `Settings` change. No domain change. No persistence change.

## Next steps

1. Manager stage-commits the 12b work into a new commit on
   `feat/phase-12b-listeners-voice`.
2. Independent review unit verifies the four mutations
   (bot-skip, SWITCH order, CF-1 revert, CF-2 revert) all flip the
   matching tests RED under revert.
3. Write `baton-runner/br-2026-05-27-phase-12/digest-phase-12b.md` so
   Phase 13 inherits: listener ctor shapes (4-dep voice listener,
   3-dep message listener), the new `collect_extra_boosts` query, the
   composite `f"{guild}:ping:{msg_id}"` lock key precedent, and the
   `joined_from_ping = bool(from_ping_message_ids)` rule.

## References

- Kickoff baton: `pass-baton/phase-12b/000-2026-05-27-phase-12b-kickoff.md`
- Mid-work baton: `pass-baton/phase-12b/001-2026-05-27-cf-1-cf-2-and-message-listener.md`
- STATE: `baton-runner/br-2026-05-27-phase-12/STATE.md`
- 12a digest: `baton-runner/br-2026-05-27-phase-12/digest-phase-12a.md`
- 8a LOW write-up (CF-1/CF-2 origins): `pass-baton/phase-8a/004-2026-05-25-guild-composite-lock-key.md`
- Phase 9 digest §3 (VcBoostTask seeding): `baton-runner/br-2026-05-25-phase-9/digest-phase-9.md`
- Spec for `is_voice_ping_message`: `docs/spec/original-skeleton.md:494-503`
- Spec for `vc_extra_boosts` recipe: `docs/spec/original-skeleton.md:559-563`
- Issue: #2
