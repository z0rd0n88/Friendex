# Phase 12b exit digest — listeners (voice + message) + CF-1/CF-2/CF-4

Phase 13/14 inherit the surface below. Conventions here are load-bearing —
the next phase must not regress them.

## Public surface added

### `friendex.adapters.discord_bot.listeners.message_listener`

```python
class MessageListener(commands.Cog):
    def __init__(
        self,
        *,
        activity_service_factory: Callable[[str], ActivityService],
        voice_ping_service_factory: Callable[[str], VoicePingService],
        settings: Settings,
    ) -> None: ...
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None: ...
```

### `friendex.adapters.discord_bot.listeners.voice_listener`

```python
class VoiceListener(commands.Cog):
    def __init__(
        self,
        *,
        activity_service_factory: Callable[[str], ActivityService],
        voice_ping_service_factory: Callable[[str], VoicePingService],
        voice_session_store_factory: Callable[[str], VoiceSessionStore],
        vc_boost_task: VcBoostTask,                          # single-instance (Phase 9 §3)
        clock: Callable[[], datetime] | None = None,         # defaults to UTC now
    ) -> None: ...
    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member,
        before: discord.VoiceState, after: discord.VoiceState,
    ) -> None: ...
```

### Patched application functions

```python
# voice_session_store.VoiceSessionStore.link_ping  (CF-1)
#   Was: session.from_ping_message_ids.add(message_id)
#   Now: dataclasses.replace(session,
#            from_ping_message_ids=session.from_ping_message_ids | {message_id})
#   Held under the existing asyncio.Lock; no caller-visible signature change.

# voice_ping_service.VoicePingService._reward_for_session  (CF-2)
#   New composite lock key f"{guild_id}:ping:{message_id}" wraps the
#   cap-check + _ping_sessions.set write. Re-read inside the lock; downstream
#   _apply_join_boost + _credit run OUTSIDE the lock (each owns its own
#   per-user composite key — no nested lock on the non-reentrant manager).

# voice_ping_service.VoicePingService.collect_extra_boosts(now)  (CF-4)
#   New read-only query — emits one VcExtraBoost per extra_joiner across
#   every open ping session. Voice listener calls this after JOIN/SWITCH
#   and pushes the result into VcBoostTask.set_store_for_guild.
```

## Conventions Phase 13/14 MUST honour

- **Listener ctor shape.** 3-dep message listener (activity factory + voice-ping
  factory + settings); 4-dep voice listener (activity + voice-ping + session-store
  factories + a single `VcBoostTask` instance) + optional `clock`. Kw-only. Same
  as cogs.
- **Bot-skip ALL.** Both listeners `if author.bot/member.bot: return` at the
  top. Webhook senders included.
- **DM-narrow.** Message listener drops when `message.guild is None` before
  any service is touched.
- **Listeners never `try/except`.** `DomainError` (and everything else)
  propagates uncaught — Phase 13 owns the central handler. Same rule as cogs.
- **Composite lock keys.** Phase 8a's `f"{guild_id}:{user_id}"` precedent
  extends to `f"{guild_id}:ping:{message_id}"` for ping-RMW. Any new lock
  acquisition under multi-guild scope MUST be composite.
- **VcBoostTask seeding (Phase 9 §3).** The listener does not crack open
  ping-session internals — it calls `VoicePingService.collect_extra_boosts(now)`
  and forwards the list to `task.set_store_for_guild(guild_id, boosts)` after
  every JOIN/SWITCH. LEAVE does not seed (responder has just left).
- **`joined_from_ping = bool(session.from_ping_message_ids)`.** Set-of-msg-ids
  is more precise than the original bot's single boolean.
- **SWITCH order.** Leave OLD → join NEW → reward on NEW → seed task.
  Mutation-hardened (`leave_index < join_index`) — do not flip.
- **No `discord` import in `domain/`, `application/`,
  `adapters/persistence/`, `adapters/tasks/`.** Phase 10 invariant.
  Listeners are the third allowed `discord`-importing layer.

## Carry-forward LOWs

1. **CF-2 mutation pin is partial.** The CF-2 RED test goes green again if
   only the LockManager wrapper is removed but the inner `_ping_sessions.get`
   re-read is kept — the store's own internal `_lock` happens to serialise
   reads with prior writes in this barrier scenario. Add a second test that
   defeats serialisation via the store lock (e.g. a no-op `LockManager` swap
   or a deeper barrier inside the store). Defer to phase-13 test-strengthening
   sweep alongside the phase-8a deferred LOWs.

2. **`_BarrierPingSessionStore._barrier_fired` is one-shot** but the
   semantics are not documented; a future test reusing the helper across
   waves would get an instant-release surprise. Add a doc comment.

## Sources

- Spec for `is_voice_ping_message`: `docs/spec/original-skeleton.md:494-503`
- Spec for `vc_extra_boosts` recipe: `docs/spec/original-skeleton.md:559-563`
- Phase 9 digest §3 — VcBoostTask seeding
- Phase 8a digest §LOWs — composite lock-key origin
- Phase 12a digest — kw-only ctor, bot-skip, DM-narrow, no try/except, callback-direct tests
