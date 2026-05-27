# baton-runner run br-2026-05-27-phase-12
status: RUNNING
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-12
phase: 2 of 2  unit: WORK  review_iter: 0 of 3
current_baton: pass-baton/phase-12a/003-2026-05-27-phase-12a-review.md
units_used: 3
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 12 of the migration plan splits along VoicePingService coupling.
#  Spec: docs/04-migration-plan.md §Phase 12 (lines 702-732).
#  Split into 2 sub-phases (each ≤ 7 files, ordered by dependency):
#    12a — foundation + simple listeners: __init__s, conftest, reaction, member
#    12b — VoicePing plane: message + voice listeners + CF-1/CF-2/CF-4 carry-forward fixes
#  Unit agent: python-pro (work + review + fix) per project default — confirmed at signoff.
#  Stacked branches: feat/phase-12a-listeners-simple (base origin/main@ea4b7b2)
#                    feat/phase-12b-listeners-voice  (base feat/phase-12a-listeners-simple)
#  One ready-for-review PR per sub-phase, stacked.
#
# Signoff decisions (user 2026-05-27):
#  1. 2-sub-phase split as proposed.
#  2. CF-2 lock scope: message_id-keyed — composite key f"{guild_id}:ping:{message_id}".
#  3. Bot-skip rule: skip ALL author.bot is True (incl. other bots).
#  4. on_member_update timeout edge: fire ONLY on None → set (not extensions, not un-timeouts).
#  5. Listener error policy: let exceptions propagate (no try/except; consistent with cogs,
#     Phase 13 owns central handling).
#
# Established conventions Phase 12 MUST honour (from Phase 8/9/10/11 digests):
#  - Per-guild service factories: ctor takes service_factory: Callable[[str], TService];
#    listener resolves per-guild service via factory(str(guild_id)) at event time.
#  - Listeners are commands.Cog instances (registered like cogs in Phase 13).
#  - Composite lock keys f"{guild_id}:{user_id}" everywhere (Phase 8a). For CF-2,
#    extend to f"{guild_id}:ping:{message_id}" for the ping-RMW span.
#  - Money is Decimal; datetimes UTC-aware (Phase 3.1).
#  - DomainError propagates uncaught (Phase 13 handles centrally; same rule as cogs).
#  - allowed_mentions=AllowedMentions.none() on every send echoing user input
#    (likely moot for listeners; assert if any listener sends a reply).
#  - VcBoostTask is single-instance; voice-ping listener calls set_store_for_guild
#    to seed per-guild VcExtraBoost (Phase 9 digest §3).
#  - No discord import in domain/, application/, adapters/persistence/, adapters/tasks/.
#    Listeners are the third allowed discord-importing layer (after embeds.py + cogs/).
#  - Test idiom: callback-direct invocation for slash commands; for listener events,
#    instantiate the cog and call `await cog.on_message(fake_message)` directly.
#    dpytest is for message events but adds heavy fixture overhead; direct call is the
#    canonical idiom in this repo (matches 11a-11c cog test pattern).

# Continuity digests (consumed by every Phase-12 work-unit):
#  - baton-runner/br-2026-05-26-phase-11/digest-phase-11a.md  (cog conventions: factory ctor, propagate DomainError, no module-level constants, mutation-hardening bar)
#  - baton-runner/br-2026-05-26-phase-11/digest-phase-11c.md  (AllowedMentions.none() for echoed inputs)
#  - baton-runner/br-2026-05-26-phase-10/digest-phase-10.md   (no discord beyond embeds/cogs/listeners)
#  - baton-runner/br-2026-05-25-phase-9/digest-phase-9.md     (VcBoostTask.set_store_for_guild seeding)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md    (composite lock keys; the two deferred LOWs to fix in 12b)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md    (DisciplineService.apply_discipline_penalty contract)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8e.md    (FundService — not directly used, just context)

phases:
  - id: phase-12a  spec: "docs/04-migration-plan.md §Phase 12 (slice: __init__s, conftest, reaction_listener, member_listener)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-12a-listeners-simple  base: origin/main@ea4b7b2
    pr: -  digest: baton-runner/br-2026-05-27-phase-12/digest-phase-12a.md
    work_commit: 22494bc  review_clean_baton: pass-baton/phase-12a/003-2026-05-27-phase-12a-review.md
    units: 3  state: DONE (work + invalid-iter1 + iter1-retry CLEAN)
    acceptance_criteria: |
      A1. src/friendex/adapters/discord_bot/listeners/__init__.py created (empty per the package convention from cogs/).
      A2. tests/adapters/discord_bot/listeners/__init__.py created (empty).
      A3. tests/adapters/discord_bot/listeners/conftest.py exposes:
          - fake_message(*, author_id: int, guild_id: int, content: str = "", is_bot: bool = False, reference_id: int | None = None, mentions: list[int] | None = None) → MagicMock
          - fake_member(*, user_id: int, guild_id: int, timed_out_until: datetime | None = None) → MagicMock
          - fake_voice_state(*, channel_id: int | None) → MagicMock
          - {activity,voice_ping,discipline}_service: AsyncMock(spec=Service) + matching factory fixtures
      A4. reaction_listener.py registers ReactionListener(commands.Cog) with on_reaction_add.
          - Delegates to ActivityService.record_reaction(user_id=str(user.id))
          - Self-reaction (reactor == message.author) is silently ignored
          - Bot reactions ignored (user.bot is True)
          - Per-guild factory ctor: activity_service_factory: Callable[[str], ActivityService]
      A5. member_listener.py registers MemberListener(commands.Cog) with on_member_update and on_member_ban.
          - on_member_update fires apply_discipline_penalty(user_id, "timeout") ONLY when
            before.timed_out_until is None and after.timed_out_until is not None
          - Extensions (set → later-set) and un-timeouts (set → None) do NOT fire
          - on_member_ban fires apply_discipline_penalty(user_id, "ban") for guild.id
          - Per-guild factory ctor: discipline_service_factory: Callable[[str], DisciplineService]
      A6. Mutation-hardened tests per cog convention (11a digest §5):
          - At least one test that fails if the bot-skip is dropped (reaction)
          - At least one test that fails if the None→set guard is dropped (timeout)
          - At least one test that fails if the kind argument flips "timeout"↔"ban"
      A7. DomainError propagates uncaught (no try/except in listeners). Test verifies
          propagation via pytest.raises on at least one error path per listener.
      A8. Gate green: pytest, ruff check, ruff format --check, mypy.
          ≥80% line coverage on the two new listener files + conftest.
  - id: phase-12b  spec: "docs/04-migration-plan.md §Phase 12 (slice: message_listener + voice_listener + CF-1/CF-2/CF-4)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-12b-listeners-voice  base: feat/phase-12a-listeners-simple
    pr: -  digest: baton-runner/br-2026-05-27-phase-12/digest-phase-12b.md
    units: 0  state: PENDING
    acceptance_criteria: |
      B1. message_listener.py registers MessageListener(commands.Cog) with on_message.
          - Skip ALL author.bot is True (incl. other bots and self)
          - Skip DM (no guild)
          - Call ActivityService.record_message(user_id, has_media, is_reply, mention_count)
            with the parameters detected from the message
          - Call VoicePingService.register_ping_message(host_id, channel_id, message_id, now)
            ONLY when the author is in a voice channel AND the message mentions
            >=1 voice-channel role / pings the channel (per original-skeleton spec —
            the work unit should consult docs/spec/original-skeleton.md for the exact rule
            and replicate it; if no clear rule, narrow to "@mention of role used for VC pings"
            and document the choice in the baton).
          - Reply-credit detection: message.reference is not None passes is_reply=True
      B2. voice_listener.py registers VoiceListener(commands.Cog) with on_voice_state_update.
          - Bot user ignored
          - JOIN (before.channel is None, after.channel is not None):
              ActivityService.handle_voice_join(user_id, channel_id, now)
              THEN VoicePingService.reward_voice_ping_response(responder_id, channel_id, now)
              THEN VcBoostTask.set_store_for_guild(guild_id, [...]) — wire per Phase 9 digest §3
          - LEAVE (before.channel is not None, after.channel is None):
              ActivityService.handle_voice_leave(user_id, channel_id, now)
          - SWITCH (both non-None, before.channel != after.channel):
              FINALIZE old THEN CREATE new — handle_voice_leave(old) FIRST, handle_voice_join(new) SECOND
              THEN reward_voice_ping_response on the new channel
      B3. CF-4 (Phase 9 wiring): voice listener's ctor takes the live VcBoostTask instance.
          On join/switch, after rewarding ping responses, seed the per-guild VcExtraBoost
          store via task.set_store_for_guild(guild_id, [...]). Source of the boosts list:
          query the VoicePingService for the current session's reward roster (or derive from
          the just-applied reward; the work unit chooses the cleanest pull point and documents).
      B4. CF-1 fix — VoiceSessionStore.link_ping immutable rebuild:
          - Pre-fix code mutates session.from_ping_message_ids in place.
          - Replace with: replace(session, from_ping_message_ids=session.from_ping_message_ids | {message_id})
            under the existing lock.
          - RED-first test: a load-bearing test that captures the in-place mutation
            (e.g. snapshot the session object pre-call, assert NOT-mutated post-call when
            the in-place semantics are reverted).
      B5. CF-2 fix — reward_voice_ping_response RMW atomicity:
          - Add per-ping LockManager acquisition via composite key
            f"{guild_id}:ping:{session.message_id}" spanning the in-loop cap-check + write
            (the section currently at voice_ping_service.py:142-148 inside _reward_for_session).
          - RED-first test: two concurrent responders to the SAME ping serialise via a
            barrier; under the unlocked code both pass the cap-check and BOTH get the boost,
            exceeding cap. Under the fix, exactly cap responders get the boost.
      B6. Mutation-hardened tests per cog convention:
          - Test fails if author.bot skip is dropped (message_listener)
          - Test fails if SWITCH order flips (leave-old before join-new)
          - Test fails if CF-1 reverted to in-place mutation
          - Test fails if CF-2 lock dropped (concurrent over-credit)
      B7. DomainError propagates uncaught. Tests verify propagation on at least one path
          per listener.
      B8. Gate green: pytest, ruff check, ruff format --check, mypy.
          ≥80% line coverage on the two new listener files + the two modified
          application files (voice_session_store.py, voice_ping_service.py — only the
          changed lines need to be covered; pre-existing coverage preserved).
