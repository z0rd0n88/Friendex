# Pass-Baton: CF-1 + CF-2 + collect_extra_boosts + message_listener green

**Date:** 2026-05-27
**Scope:** phase-12b
**Branch:** feat/phase-12b-listeners-voice
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-12
**HEAD:** ccc1384 chore(phase-12b): log 12b 529-abort + respawn

## Where things stand

In-flight. B4 (CF-1), B5 (CF-2), the `collect_extra_boosts` CF-4 query,
and B1 (message_listener) are complete and green. Still to do: B2 (voice
listener with JOIN/LEAVE/SWITCH + CF-4 wiring), B3 (CF-4 task-seed call
inside voice listener — note: requires the listener ctor to take a live
`VcBoostTask`), then B6/B7 final sweep + B8 gate.

## What's done (with captured RED proof)

### B4 (CF-1) — VoiceSessionStore.link_ping immutable rebuild

- RED: `test_link_ping_rebuilds_session_immutably_without_mutating_prior_set`
  captured `frozenset({4242}) == frozenset()` failure under pre-fix
  `session.from_ping_message_ids.add(message_id)` semantics.
- GREEN: `src/friendex/application/voice_session_store.py` — `link_ping`
  now rebuilds via `replace(session, from_ping_message_ids=session.from_ping_message_ids | {message_id})`.
- Confirmed `tests/application/test_activity_service.py` 17/17 pass.
- Adjusted the existing `test_voice_session_store_set_get_pop_and_link_ping`
  comment to drop the "in place" phrasing (now matches immutable rebuild).

### B5 (CF-2) — reward_voice_ping_response RMW atomicity

- RED: `test_concurrent_responders_respect_cap_under_per_ping_lock` with
  `voice_ping_first_n_joiners=1` and a `_BarrierPingSessionStore` that
  parks both responders inside `list_all` then releases — under pre-fix
  code both responders' stocks landed at $120.00 (2× boost), violating
  cap.
- GREEN: `src/friendex/application/voice_ping_service.py` — added
  `_ping_lock_key(message_id) → f"{guild_id}:ping:{message_id}"`;
  `_reward_for_session` now wraps the cap-check + placement write under
  `locked(self._ping_lock_key(...))`, re-reads the session inside the
  lock, and short-circuits if the responder was already credited by a
  racing coroutine. The user-scope locks (`_apply_join_boost`, `_credit`)
  remain outside the ping lock — they take their own composite keys, no
  nested locking risk.
- Confirmed `tests/application/test_voice_ping_service.py` 12/12 pass.

### CF-4 plumbing query — `VoicePingService.collect_extra_boosts(now)`

- RED: `test_collect_extra_boosts_emits_one_entry_per_extra_joiner` —
  `AttributeError: 'VoicePingService' object has no attribute 'collect_extra_boosts'`.
- GREEN: walks open ping sessions, emits one
  `VcExtraBoost(user_id, ping_time=session.timestamp, last_boost=now, end_time=session.timestamp+window)`
  per `extra_joiner`. Listener (B3) will push this list into
  `VcBoostTask.set_store_for_guild(guild_id, ...)` after every voice
  join/switch.

### B1 (message_listener) — 12/12 tests green

- `src/friendex/adapters/discord_bot/listeners/message_listener.py` new.
- Ctor kw-only: `activity_service_factory`, `voice_ping_service_factory`,
  `settings` — per-guild factory routing at event time.
- `is_voice_ping_message` rule replicated:
  `member.voice and member.voice.channel` AND
  `any(role.id in settings.vc_ping_role_ids for role in message.role_mentions)`.
  With default empty config no message is ever a ping.
- Bot-skip (signoff 3): drops ALL `author.bot is True`.
- DM-skip: `message.guild is None → return`.
- `record_message` called with `(author_id, has_attachment, is_reply, channel_id)`
  per the existing `ActivityService` contract; `mention_count` from
  STATE.md is paraphrase — not in the service contract.
- DomainError propagates uncaught (B7 satisfied for this listener).
- Tests cover: text path, reply+media flags, VC ping success, VC ping
  no-voice/wrong-role/empty-config skip, DM skip, bot skip
  (mutation-hardened — B6), DomainError propagation (B7),
  factory routing, cog/listener decorator sanity.

## Files changed so far

- `src/friendex/application/voice_session_store.py` — CF-1 fix
- `src/friendex/application/voice_ping_service.py` — CF-2 fix + new
  `collect_extra_boosts` query (with `VcExtraBoost` import + timedelta)
- `src/friendex/adapters/discord_bot/listeners/message_listener.py` — new
- `tests/application/test_activity_service.py` — CF-1 RED test (+ comment
  refresh on existing test)
- `tests/application/test_voice_ping_service.py` — CF-2 RED test +
  `collect_extra_boosts` test + `_BarrierPingSessionStore`
- `tests/adapters/discord_bot/listeners/test_message_listener.py` — new

## Remaining work

1. **B2 voice_listener** — `on_voice_state_update` with JOIN/LEAVE/SWITCH
   in the order spec'd by STATE.md (B2). Uses `fake_voice_state` from
   conftest; computes `stay_minutes` for LEAVE/SWITCH from the
   `VoiceSessionStore` `.get` snapshot (the ActivityService is responsible
   for popping in `handle_voice_leave`, but the listener owns the
   stay-minutes math from `(now - session.start).total_seconds() / 60`).
   - Actually: re-read `handle_voice_leave(user_id, channel_id, stay_minutes, joined_from_ping)` — listener must compute stay_minutes from the live session before invoking. Confirm `joined_from_ping` interpretation; original spec line 731 used `session["role_ping"]` (boolean). Decision: pass `joined_from_ping = bool(session.from_ping_message_ids)` so the rule honors the from-ping-set semantics established in B4.
2. **B3 CF-4 seed** — voice listener ctor must take a `VcBoostTask` (live
   instance, not a factory — task is a singleton per Phase 9 digest §3);
   after JOIN/SWITCH, call
   `task.set_store_for_guild(guild_id, await voice_ping_service.collect_extra_boosts(now))`.
3. **B6 mutation-hardening** — add a test asserting SWITCH order
   (leave-old THEN join-new), and verify the CF-1/CF-2 tests catch their
   reversion (already do — confirmed RED-first).
4. **B8 gate** — `uv run pytest`, ruff check, ruff format --check, mypy.
   Then coverage check on listener files + modified application files.

## Next steps

1. Write `tests/adapters/discord_bot/listeners/test_voice_listener.py`
   covering JOIN / LEAVE / SWITCH / bot-skip / DomainError propagation /
   CF-4 seed call / mutation-hardened SWITCH ordering.
2. Implement `src/friendex/adapters/discord_bot/listeners/voice_listener.py`.
3. Run full gate; update this baton or write the completion baton.

## References

- STATE: `baton-runner/br-2026-05-27-phase-12/STATE.md`
- Kickoff: `baton-pass/phase-12b/000-2026-05-27-phase-12b-kickoff.md`
- Phase 9 digest §3 (VcBoostTask): `baton-runner/br-2026-05-25-phase-9/digest-phase-9.md`
- Phase 8a LOW write-up (CF-1, CF-2 carry-forwards): `baton-pass/phase-8a/004-2026-05-25-guild-composite-lock-key.md`
- Spec for `is_voice_ping_message`: `docs/spec/original-skeleton.md:494-503`
- Issue: #2
