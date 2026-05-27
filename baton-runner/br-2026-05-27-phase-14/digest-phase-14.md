# Phase 14 exit digest — Bot Factory & Entry Point

Approved 2026-05-27 (baton 002). Branch `feat/phase-14-bot-factory`, HEAD `0d4fbf1`.

## Public surface added

- `adapters/discord_bot/bot.py::build_bot(settings, container) -> commands.Bot` —
  builds `commands.Bot(commands.when_mentioned, intents=Intents.all())`. Inner
  `setup_hook` (attribute-assign, `# type: ignore[method-assign]`) runs at login:
  `register_with(bot)` → `bind_runtime(bot)` → `task.start()` ×8 →
  `bot.tree.sync()` global → if `settings.dev_guild_id is not None`,
  `tree.copy_global_to(guild=Object(id=...))` + per-guild sync.
- `adapters/container.py::Container.bind_runtime(bot) -> None` — in-place swap:
  `setattr(task, "_iter_guild_ids", ...)` on every task (closure re-reads
  `bot.guilds` per tick); `self._liquidation_task._notifier =
  _make_liquidation_notifier(bot)`.
- `adapters/container.py::_make_liquidation_notifier(bot)` — resolves
  `bot.get_guild(int(event.guild_id)).system_channel`; WARN-and-skip on either
  missing; sends `build_liquidation_notification_embed(event)` with
  `AllowedMentions.none()`.
- `application/liquidation_events.py::LiquidationEvent.guild_id: str` — new
  required first field. Populated by `LiquidationService` from `self._guild_id`.
- `main.py::amain` fills the Phase-13 `NotImplementedError` seam with
  `bot = build_bot(...); await bot.start(settings.discord_token)`;
  `engine.dispose()` in `finally`.

## Phase 15+ MUST honour

1. Slash-only — `commands.when_mentioned` is inert. New surfaces on `bot.tree`
   or Cog `app_commands`.
2. `setup_hook` is the single startup seam — no second assignment, no `bot.run`
   shim. Pre-gateway work goes inside the existing closure.
3. Order: `register_with` → `bind_runtime` → `task.start` → tree sync. Tasks
   started before the swap see `_empty_guild_ids` and no-op.
4. `AllowedMentions.none()` on every reply/dispatch path.
5. Type-ignore budget = 3 in src/ (`call-arg` Settings, `method-assign`
   on_error, `method-assign` setup_hook). New ignores justified at review.
6. `LiquidationEvent.guild_id` required — every producer must populate it.
7. Integration tests drive cog callbacks directly against a stub
   `discord.Interaction`; dpytest does not drive `app_commands.Command`.

Carry-forward LOW: two bind_runtime WARN tests set
`logging.getLogger("friendex.adapters.container").disabled = False` —
`alembic/env.py` `fileConfig(disable_existing_loggers=True)` bleeds. One-line
fix in `alembic/env.py` deferred.

Tests: 7 `test_bot_factory.py`, 19 `test_container.py` (13 + 6 bind_runtime),
2 `tests/integration/test_full_command_flow.py`. Suite 786 passed. No new deps.
