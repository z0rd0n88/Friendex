# baton-runner run br-2026-05-27-phase-14
status: DONE
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-14
phase: 1 of 1  unit: -  review_iter: 1 of 3 (CLEAN)
current_baton: pass-baton/phase-14/002-2026-05-27-review-iter-1.md
units_used: 2
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 14 of the migration plan: Bot Factory & Entry Point.
#  Spec: docs/04-migration-plan.md §Phase 14 (lines 765-789).
#  Phase-13 carry-forward digest: baton-runner/br-2026-05-27-phase-13/digest-phase-13.md
#  Single sub-phase (~8 files, within bail budget of 10).
#  Unit agent: python-pro (work + review + fix) — project default.
#  Branch: feat/phase-14-bot-factory (base origin/main@4c591f0).
#  One ready-for-review PR stacked on origin/main.
#
# Signoff decisions (user 2026-05-27):
#  Q1. Command sync: global `bot.tree.sync()` always; if `settings.dev_guild_id`
#      is set, ALSO `tree.copy_global_to(guild=Object(dev_guild_id))` +
#      `tree.sync(guild=...)` for instant dev visibility. (Spec's
#      `settings.guild_id` is stale — current field is `dev_guild_id`.)
#  Q2. Runtime binding: add `Container.bind_runtime(bot) -> None`. Swaps each
#      task's `iter_guild_ids` to walk `bot.guilds`; replaces LiquidationTask's
#      `_noop_notifier` with a real dispatcher. Preserves Phase-13 ctor
#      signature + the "8 built, none started" test.
#  Q3. Liquidation notifier target: `bot.get_guild(int(event.guild_id))
#      .system_channel`; if guild or system_channel missing, log WARNING and
#      skip. No new Settings field. AllowedMentions.none() on send.
#  Q4. Branch: baton-runner convention — `feat/phase-14-bot-factory` in
#      fresh worktree.
#
# Acceptance criteria locked at signoff (Phase 14):
#  AC1. src/friendex/adapters/discord_bot/bot.py::build_bot(settings, container)
#       -> commands.Bot — constructs Bot(command_prefix=commands.when_mentioned,
#       intents=discord.Intents.all()). Assigns bot.setup_hook to an async
#       callable that: (a) calls container.bind_runtime(bot), (b) starts every
#       task in container.tasks (task.start()), (c) `await bot.tree.sync()`
#       globally, (d) IF settings.dev_guild_id is not None: also
#       `bot.tree.copy_global_to(guild=discord.Object(settings.dev_guild_id))`
#       and `await bot.tree.sync(guild=...)`. Uses direct attribute assignment
#       to `setup_hook` (mirrors Phase-13's `bot.tree.on_error =` pattern —
#       carries the existing method-assign type-ignore at most once more).
#  AC2. src/friendex/adapters/container.py::Container.bind_runtime(bot) -> None
#       — for every task in self.tasks, sets task._iter_guild_ids to an async
#       callable returning [str(g.id) for g in bot.guilds]. For the
#       LiquidationTask (matched by isinstance OR a stored reference), replaces
#       its `_notifier` attribute with `_make_liquidation_notifier(bot)` —
#       a closure that on event: bot.get_guild(int(event.guild_id))
#       .system_channel.send(embed=build_liquidation_notification_embed(event),
#       allowed_mentions=AllowedMentions.none()); else log WARNING + skip.
#       Mutation is in-place on the existing single-instance tasks (the test
#       pinning "8 built, none started" continues to pass because bind_runtime
#       is invoked separately from __init__).
#  AC3. src/friendex/main.py::amain — replace the
#       `raise NotImplementedError("Phase 14: build_bot + bot.start")` line
#       with: bot = build_bot(settings, container); await
#       container.register_with(bot); await bot.start(settings.discord_token).
#       Keep `engine.dispose()` in finally. Carry the existing
#       `Settings()  # type: ignore[call-arg]`.
#  AC4. tests/adapters/discord_bot/test_bot_factory.py — RED-first smoke:
#       (a) build_bot returns commands.Bot with Intents.all();
#       (b) bot.setup_hook is set and not the default;
#       (c) after running setup_hook with stubbed `bot.tree.sync` + empty
#          bot.guilds, every task in container.tasks has is_running() True;
#       (d) bot.add_cog was called for each of 11 entries (7 cogs + 4
#          listeners) by virtue of pre-setup_hook register_with;
#       (e) bind_runtime swapped iter_guild_ids on every task (assert task
#          ._iter_guild_ids is no longer `_empty_guild_ids`);
#       (f) LiquidationTask notifier was replaced (no longer `_noop_notifier`).
#       Use direct attribute inspection + monkeypatched bot.tree.sync; dpytest
#       not required if it complicates setup_hook capture.
#  AC5. tests/integration/__init__.py (empty) + tests/integration/
#       test_full_command_flow.py — end-to-end with in-memory SQLite +
#       Alembic upgrade head; bot built via build_bot; container.register_with
#       called. Drive /daily, /buy <target> 1, /portfolio via dpytest (or
#       equivalent app_commands invocation harness). Assert each emits the
#       expected embed shape and the post-state in the DB reflects the trade.
#       If dpytest cannot drive slash commands cleanly, fall back to invoking
#       the cog's command callback directly with a fake Interaction stub
#       (mirroring Phase-11 cog tests) — RECORD the choice in the baton.
#  AC6. tests/adapters/test_container.py — add tests for bind_runtime(bot):
#       given a bot with two stub guilds, every task's iter_guild_ids returns
#       both guild ids; LiquidationTask's notifier dispatches to system_channel
#       with AllowedMentions.none() (use Mock guild + system_channel); when
#       system_channel is None, no send occurs and a WARNING is logged.
#
# Type-ignore budget: 3 carried (method-assign in error_handler.py, call-arg
# on Settings() in main.py, attr-defined in tests). Phase 14 may add ONE more
# method-assign for `bot.setup_hook =` if the discord.py stub flags it; budget
# becomes 4. Acceptable if justified inline; review will challenge any beyond.
#
# Spec deviations from docs/04-migration-plan.md §Phase 14 (recorded for PR body):
#  - settings.guild_id → settings.dev_guild_id (field renamed pre-Phase 13).
#  - Sync is "global + optional dev-guild instant", not "home-guild only".
#  - main.py modified (spec says "Files modified: none" — Phase 13 STATE.md
#    already flagged this inaccuracy at its signoff).
#  - bind_runtime + real notifier go onto container.py (also modified) —
#    Phase 13 digest items #2-#3 require this.

phases:
  - id: phase-14  spec: docs/04-migration-plan.md §Phase 14  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-14-bot-factory  pr: https://github.com/z0rd0n88/Friendex/pull/65
    digest: baton-runner/br-2026-05-27-phase-14/digest-phase-14.md
    units: 2  state: DONE
