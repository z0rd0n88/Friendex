# baton-runner run br-2026-05-27-phase-13
status: RUNNING
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-13
phase: 1 of 1  unit: WORK  review_iter: 0 of 3
current_baton: -
units_used: 0
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 13 of the migration plan: Error Handler & Container Wiring.
#  Spec: docs/04-migration-plan.md §Phase 13 (lines 735-761).
#  Single sub-phase (6 files, within bail budget of 10).
#  Unit agent: python-pro (work + review + fix) per project default — confirmed at signoff.
#  Branch: feat/phase-13-container (base origin/main@85bb0fc).
#  One ready-for-review PR.
#
# Signoff decisions (user 2026-05-27):
#  1. Error hook: tree.on_error only, unwrap CommandInvokeError.
#     - Map DomainError → ephemeral embed with user_facing_message.
#     - Map PersistenceError → log ERROR + ephemeral "Internal error, please try again".
#     - Fallthrough Exception → log CRITICAL + ephemeral "Unexpected error".
#     - Drop prefix-only branches (MissingRequiredArgument, MemberNotFound) — unreachable under slash-only.
#  2. main.py vs Phase 14: Phase 13 ships main.py WITHOUT bot.start. main.py builds
#     settings + structured logging + engine + sessionmaker + Container, then raises
#     NotImplementedError("Phase 14: build_bot + bot.start") at the bot-construction marker.
#     Phase 14 will modify main.py to import build_bot and call bot.start (accept that
#     Phase 14's "Files modified: none" line in the plan is slightly inaccurate).
#  3. Container test depth: construction + registration counts. Assert exactly
#     7 cogs, 4 listeners, 8 tasks built. No tasks started. Spot-check per-guild
#     factories are callable with a string guild_id.
#
# Acceptance criteria locked at signoff (Phase 13):
#  AC1. src/friendex/adapters/discord_bot/error_handler.py — register_error_handler(bot, settings)
#       registers handler on bot.tree.on_error. Unwraps app_commands.errors.CommandInvokeError
#       (chained) to .original. Maps per signoff Q1.
#  AC2. src/friendex/adapters/container.py — Container(settings, sessionmaker). Builds repos
#       → LockManager → per-guild service factories (Callable[[str], TService]) for every
#       application service that takes guild_id → 8 tasks (single-instance) → 7 cogs → 4
#       listeners. Exposes register_with(bot) that add_cog's every cog/listener and calls
#       register_error_handler(bot, settings).
#  AC3. src/friendex/main.py — async def amain(): loads Settings, configures structured
#       logging, creates engine + async_sessionmaker, builds Container, then raises
#       NotImplementedError("Phase 14: build_bot + bot.start"). Disposes engine in finally.
#       def main(): asyncio.run(amain()). Re-exported from friendex.__init__.
#  AC4. tests/adapters/discord_bot/test_error_handler.py — at minimum:
#       (a) DomainError → ephemeral embed with user_facing_message verbatim
#       (b) PersistenceError → logger.error called + generic ephemeral reply
#       (c) Unknown Exception → logger.critical called + generic ephemeral reply
#       (d) CommandInvokeError(original=DomainError(...)) → unwrapped and routed to (a)
#  AC5. tests/adapters/test_container.py — Container(settings, fake_sessionmaker) constructs
#       without raising. Asserts: 7 cogs, 4 listeners, 8 tasks. No task is_running(). Each
#       per-guild service factory is callable with a string guild_id and returns the
#       expected service type. register_with(fake_bot) adds every cog/listener and registers
#       the error handler.
#
# Established conventions Phase 13 MUST honour (from Phase 8–12 digests):
#  - Per-guild service factories: Callable[[str], TService]; built around the shared repos
#    + LockManager + sessionmaker. Cogs/listeners ctor takes the factory, not the service.
#  - LockManager is a single instance shared across all per-guild services.
#  - Money is Decimal; datetimes UTC-aware (Phase 3.1).
#  - DomainError propagates uncaught from cogs/listeners — Phase 13 owns the central
#    handler.
#  - VcBoostTask is single-instance; voice listener calls set_store_for_guild
#    (Phase 9 digest §3). The container constructs VcBoostTask once and passes it to
#    the voice listener.
#  - No discord import in domain/, application/, adapters/persistence/, adapters/tasks/.
#    error_handler.py and container.py are allowed to import discord (adapters/discord_bot/
#    and adapters/ are the discord-aware layers).
#  - All slash-command replies that echo user input should pass AllowedMentions.none()
#    — the error handler echoes only canned strings and the DomainError user_facing_message
#    (which is constructed from validated game state), so allowed_mentions defaults are
#    safe; nevertheless reply with allowed_mentions=AllowedMentions.none() for defence
#    in depth (Phase 10 I2 carry-forward).
#  - No try/except DomainError in cogs/listeners (continue to be the rule).
#  - Cogs are commands.Cog subclasses; listeners are commands.Cog subclasses (registered
#    via add_cog).
#
# Cog inventory (7):
#   AccountCog, AdminCog, DailyCog, FundCog, PortfolioCog, StatsCog, TradingCog
#
# Listener inventory (4):
#   MessageListener, VoiceListener, ReactionListener, MemberListener
#
# Task inventory (8):
#   ActivityTickTask, DailyResetTask, FreezeCheckTask, InactivityDecayTask,
#   LiquidationTask, MonthlyRolloverTask, VcBoostTask, WeeklyResetTask
#
# Continuity digests (consumed by the Phase-13 work-unit):
#  - baton-runner/br-2026-05-27-phase-12/digest-phase-12a.md (listener ctor shape; bot-skip; no try/except)
#  - baton-runner/br-2026-05-27-phase-12/digest-phase-12b.md (voice + message listener deps; VcBoostTask seeding)
#  - baton-runner/br-2026-05-26-phase-11/digest-phase-11a.md (cog ctor convention; propagate DomainError)
#  - baton-runner/br-2026-05-26-phase-11/digest-phase-11b.md (cog conventions continued)
#  - baton-runner/br-2026-05-26-phase-11/digest-phase-11c.md (FundGroup wired via FundCog.group; AllowedMentions.none())
#  - baton-runner/br-2026-05-26-phase-10/digest-phase-10.md (embed builders surface; COLOR_*; no discord beyond embeds/cogs/listeners)
#  - baton-runner/br-2026-05-25-phase-9/digest-phase-9.md (task surface; VcBoostTask single-instance; set_store_for_guild)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md … 8f (service signatures)
#  - baton-runner/br-2026-05-25-phase-7/digest-phase-7.md (LockManager)
#  - baton-runner/br-2026-05-24-phase-6/digest-phase-6.md (repo Protocols)

phases:
  - id: phase-13  spec: "docs/04-migration-plan.md §Phase 13 (lines 735-761) + signoff decisions in STATE.md"
    readiness: READY
    unit_agent: python-pro
    branch: feat/phase-13-container  base: origin/main@85bb0fc
    pr: -
    digest: baton-runner/br-2026-05-27-phase-13/digest-phase-13.md
    units: 0
    state: RUNNING
