# Phase 12a Exit Digest — Listeners Foundation (reaction + member)

## Public surface

`src/friendex/adapters/discord_bot/listeners/`:

- `__init__.py` — empty (matches `cogs/__init__.py`).
- `reaction_listener.ReactionListener(commands.Cog)`
  - ctor: `*, activity_service_factory: Callable[[str], ActivityService]`
  - `async on_reaction_add(reaction: discord.Reaction, user: discord.User | discord.Member) -> None`
- `member_listener.MemberListener(commands.Cog)`
  - ctor: `*, discipline_service_factory: Callable[[str], DisciplineService]`
  - `async on_member_update(before: discord.Member, after: discord.Member) -> None`
  - `async on_member_ban(guild: discord.Guild, user: discord.User | discord.Member) -> None`

`tests/adapters/discord_bot/listeners/conftest.py` (12b inherits unchanged):

- Event factories: `fake_message`, `fake_member`, `fake_voice_state`.
- `AsyncMock(spec=Service)` fixtures + matching `<svc>_service_factory`
  (`Callable[[str], TService]`) for activity, discipline, voice_ping,
  trading, fund, daily, portfolio, stats.

## Conventions 12b MUST honour

1. **Per-guild factory ctor** (`Callable[[str], TService]`, kw-only); resolve
   via `factory(str(guild.id))` at event time. Never cache a service.
2. **Listeners are `commands.Cog` subclasses** with `@commands.Cog.listener()`.
3. **DomainError propagates uncaught.** No `try/except` in listener bodies.
4. **DM-narrow** at the event boundary: `if message.guild is None: return`
   before calling the factory.
5. **Bot-skip applies to ALL bots** (`author.bot`/`user.bot is True`),
   including the project bot itself (signoff decision 3).
6. **Kind literals are exactly `"timeout"` and `"ban"`** per Phase 8f
   `DisciplineService.apply_discipline_penalty(user_id, kind)`.
7. **Test idiom**: instantiate cog, `await cog.on_event(...)` directly with
   permissive `MagicMock` events; no `dpytest`.
8. **Mutation-hardened tests required** per Phase 11a §5 — every guard /
   skip / distinguishing literal needs a load-bearing pin.
9. **No module-level constants** in product code; tunables live in `Settings`.
10. **Discord imports allowed only in `listeners/`, `cogs/`, `embeds.py`** —
    `domain/`, `application/`, `adapters/persistence/`, `adapters/tasks/`
    stay discord-free.

## 12b carry-ins

- Conftest already covers message + voice + ping fixtures.
- Voice listener seeds per-guild `VcExtraBoost` via
  `VcBoostTask.set_store_for_guild` (Phase 9 digest §3).
- CF-1 (`replace(session, from_ping_message_ids=...)`) and CF-2
  (composite lock `f"{guild_id}:ping:{message_id}"`) are in B4/B5 scope.
