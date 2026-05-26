# baton-runner run br-2026-05-26-phase-11
status: RUNNING
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-26-phase-11
phase: 1 of 3  unit: REVIEW  review_iter: 1 of 3
current_baton: pass-baton/phase-11a/002-2026-05-26-phase-11a-complete.md
units_used: 1
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 11 of the migration plan splits naturally along cog boundaries.
#  Spec: docs/04-migration-plan.md §Phase 11 (lines 660-698).
#  Split into 3 sub-phases (each ≤ 9 files, ordered by dependency):
#    11a — foundation + simple cogs: conftest + account, daily, admin
#    11b — read-only cogs: portfolio, stats
#    11c — mutation-heavy cogs: trading, fund
#  Unit agent: python-pro (work + review + fix) per project default — confirmed at signoff.
#  Stacked branches: feat/phase-11a-cogs-foundation (base origin/main@2b5c6b1)
#                    feat/phase-11b-cogs-read (base feat/phase-11a-cogs-foundation)
#                    feat/phase-11c-cogs-trade (base feat/phase-11b-cogs-read)
#  One draft PR per sub-phase, stacked.
#
# Signoff decisions (user 2026-05-26):
#  1. Proceed with 3-sub-phase split as proposed.
#  2. Cogs propagate DomainError uncaught — Phase 13 will catch centrally.
#  3. Cogs read interaction.guild.id (assume guild context — no DM handling).
#
# Established conventions Phase 11 MUST honour (from Phase 9/10 digests):
#  - Per-guild service factories: ctor takes `service_factory: Callable[[str], TService]`;
#    cog calls factory(str(interaction.guild.id)) to obtain the per-guild instance.
#  - Cogs are the second `discord`-importing layer (embeds is the first).
#  - Reuse COLOR_* + builders from src/friendex/adapters/discord_bot/embeds.py;
#    never redefine palette or hand-roll embeds in cogs.
#  - Money is Decimal at every boundary; datetimes are UTC-aware (Phase 3.1).
#  - I2 from Phase 10 review (carry-forward): every `send_message`/`followup.send`
#    in fund_cog (Phase 11c) passes `allowed_mentions=discord.AllowedMentions.none()`.

# Continuity digests (consumed by every Phase-11 work-unit):
#  - baton-runner/br-2026-05-26-phase-10/digest-phase-10.md       (embed builders)
#  - baton-runner/br-2026-05-25-phase-9/digest-phase-9.md         (service_factory ctor shape)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md        (per-guild + composite lock key)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md        (Trading API)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md        (PortfolioSnapshot + StatsService)
#  - baton-runner/br-2026-05-25-phase-8/digest-phase-8e.md        (FundService + DailyService)

phases:
  - id: phase-11a  spec: "docs/04-migration-plan.md §Phase 11 (slice: __init__, conftest, account/daily/admin cogs)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-11a-cogs-foundation  base: origin/main@2b5c6b1
    pr: -  digest: baton-runner/br-2026-05-26-phase-11/digest-phase-11a.md
    units: 1  state: RUNNING (review pending)
    work_commit: 035e99a
    work_baton: pass-baton/phase-11a/002-2026-05-26-phase-11a-complete.md
    acceptance_criteria: |
      A1. tests/adapters/discord_bot/cogs/__init__.py created (empty).
      A2. tests/adapters/discord_bot/cogs/conftest.py exposes a `fake_interaction()`
          factory returning a MagicMock with .response.send_message, .response.defer,
          .followup.send as AsyncMock, plus .user.id, .guild.id populated; plus
          fixtures wrapping every application service used by 11a/b/c as AsyncMock.
      A3. account_cog.py registers /balance, /optin, /optout (all ephemeral).
          /balance calls PortfolioService.portfolio_snapshot → build_balance_embed.
          /optin and /optout call ActivityService.set_opt_in(user_id, True/False).
      A4. daily_cog.py registers /daily (public). Calls DailyService.claim_daily(now)
          → build_daily_embed. Tests cover happy + AlreadyClaimedToday propagation.
      A5. admin_cog.py registers /help (ephemeral, build_help_embed) and /game_intro
          (gated by app_commands.checks.has_permissions(manage_guild=True),
          public, build_intro_embed). Test verifies the check is present.
      A6. Each cog accepts service factories (Callable[[str], TService]) in __init__
          per Phase-9 convention; resolves per-guild service via
          factory(str(interaction.guild.id)).
      A7. Tests invoke `Cog.command.callback(cog, interaction, ...)` directly
          (slash-command-aware — dpytest is for message events, not interactions).
      A8. Gate green: pytest (all suites), ruff check, ruff format --check, mypy.
          ≥80% line coverage on the three new cog files.
  - id: phase-11b  spec: "docs/04-migration-plan.md §Phase 11 (slice: portfolio + stats cogs)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-11b-cogs-read  base: feat/phase-11a-cogs-foundation
    pr: -  digest: baton-runner/br-2026-05-26-phase-11/digest-phase-11b.md
    units: 0  state: PENDING
    acceptance_criteria: |
      B1. portfolio_cog.py /portfolio [user] — default user=invoker; calls
          PortfolioService.portfolio_snapshot → build_portfolio_embed → ephemeral.
      B2. stats_cog.py /trending (public, build_trending_embed),
          /mystats (ephemeral, build_mystats_embed),
          /price <user> (ephemeral, build_price_embed),
          /mystock (ephemeral; same builder as /price with user=invoker).
      B3. Tests cover default-self and explicit-user for /portfolio; ephemeral
          flag asserted on every ephemeral path; public on /trending.
      B4. Gate green + ≥80% coverage on the two new cog files.
  - id: phase-11c  spec: "docs/04-migration-plan.md §Phase 11 (slice: trading + fund cogs)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-11c-cogs-trade  base: feat/phase-11b-cogs-read
    pr: -  digest: baton-runner/br-2026-05-26-phase-11/digest-phase-11c.md
    units: 0  state: PENDING
    acceptance_criteria: |
      C1. trading_cog.py registers /buy /sell /short /cover, each
          (user: discord.Member, shares: app_commands.Range[int, 1, None]).
          Each delegates to TradingService.{buy,sell,short,cover}
          (actor_id=str(interaction.user.id), target_id=str(user.id), shares)
          and renders the matching confirmation embed (public).
      C2. fund_cog.py defines `class FundGroup(app_commands.Group, name="fund")`
          with subcommands create [name], info [user], withdraw <amount>,
          send_events <amount>, invest <user> <amount> (invest raises
          NotImplementedError per §Open-Q5). info is ephemeral; mutations public.
      C3. Carry-forward I2 (Phase 10 review): every send in fund_cog passes
          `allowed_mentions=discord.AllowedMentions.none()`. Tests assert this kwarg.
      C4. Cogs do NOT catch DomainError — Phase 13 will. Tests verify DomainError
          propagates via pytest.raises on at least one error path per command.
      C5. Gate green + ≥80% coverage on the two new cog files.
