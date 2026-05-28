# baton-runner run br-2026-05-28-phase-17
status: RUNNING
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-28-phase-17
phase: 1 of 3  unit: WORK  review_iter: 0 of 3
current_baton: -
units_used: 0
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 17 of the migration plan: Hardening & Deferred Items.
#  Spec: docs/04-migration-plan.md §Phase 17 (lines 858-890).
#  Phase-16 carry-forward digest: baton-runner/br-2026-05-27-phase-16/digest-phase-16.md
#  User signed off 2026-05-28 to SPLIT Phase 17 into 17a + 17b + 17c (stacked PRs).
#  Unit agent: python-pro (work + review + fix) — project default.
#  NOTE: origin/main (a205e40) renamed pass-baton → baton-pass (PR #70).
#  All unit prompts MUST reference the `baton-pass` skill, not the legacy name.
#  The baton output directory is `baton-pass/<scope>/NNN-<date>-<slug>.md`.
#
# Signoff decisions (user 2026-05-28):
#  Q1. Split: 17a = config toggles + wiring (sunday_buy_allowed,
#      hedge_fund_base_apy_period, opt_out_blocks_trading); 17b = /fund invest
#      + APY-to-investors split + manager-cap withdraw; 17c = Q10 auto-DM
#      intro on first /optin + smoke pin update + runbook updates.
#  Q2. Self-invest BLOCKED: investor_id == fund.manager_id raises
#      InvalidAmount("cannot invest in own fund"). Manager funds their own
#      balance via existing contribute/withdraw paths.
#  Q3. APY split = per-stake: manager_balance = cash_balance -
#      sum(investors.values()); manager accrual on manager_balance; each
#      investor accrual on their stake. Both via compute_apy_accrual at
#      the configured period.
#  Q4. Manager withdraw capped at manager_balance (investor principal
#      untouchable). Investor-withdraw (/fund divest) is explicitly
#      deferred — PR body notes this.
#  Q5. Toggle defaults: keep migration-plan defaults
#      (sunday_buy_allowed=True, hedge_fund_base_apy_period="monthly",
#      opt_out_blocks_trading=True). All no-behavior-change at default;
#      the value of phase 17 is making them switchable.
#  Q6. Q10 auto-DM on first /optin: AccountCog.optin calls a new
#      ActivityService.opt_in_and_consume_intro(user_id) -> bool that
#      atomically sets opt_in=True and, on first call (intro_shown=False),
#      flips intro_shown=True returning True; the cog then attempts
#      interaction.user.send(embed=build_intro_embed()); on Forbidden
#      (DMs closed) falls back to attaching the intro embed to the
#      ephemeral confirmation reply. /optout unchanged.
#  Q7. Phase-16 smoke pin: DELETE
#      test_fund_invest_step_notes_not_implemented_error AND rewrite
#      STEPS[id=18].expected to live-invest semantics.
#  Q8. Post-cutover bugs from Phase 16: none observed.
#
# Acceptance criteria locked at signoff:
#
# Phase 17a (config toggles + wiring):
#  A1. Settings (src/friendex/adapters/config.py) gains three fields:
#      - sunday_buy_allowed: bool = True
#      - hedge_fund_base_apy_period: Literal["monthly", "annual"] = "monthly"
#      - opt_out_blocks_trading: bool = True
#      .env.example documents all three with comments tying them to the
#      relevant §02-target-architecture.md Open-Q numbers. Unit test:
#      defaults match; env override flips each (e.g. SUNDAY_BUY_ALLOWED=false).
#  A2. TradingService.buy passes
#      allow_sunday=self._settings.sunday_buy_allowed (currently hard-coded
#      True at trading_service.py:334). Behavior unchanged at default. RED:
#      with settings.sunday_buy_allowed=False and a Sunday-10:00 datetime,
#      /buy raises MarketClosed; settings.sunday_buy_allowed=True permits.
#      /sell, /short, /cover unchanged (still allow_sunday=False).
#  A3. TradingService._check_opt_in early-returns when
#      self._settings.opt_out_blocks_trading is False (currently
#      unconditional raise at trading_service.py:312-315). Behavior
#      unchanged at default. RED: with settings.opt_out_blocks_trading=False
#      against an opt_in=False target, /buy/sell/short/cover proceed
#      past the opt-in check.
#  A4. FundService.accrue_apy passes
#      period=self._settings.hedge_fund_base_apy_period to
#      compute_apy_accrual (currently hard-coded "monthly" at
#      fund_service.py:340). Behavior unchanged at default. RED: with
#      settings.hedge_fund_base_apy_period="annual" on a $100 balance at
#      15% APY, accrual = $15.00 (vs $1.25 monthly).
#  A5. Full gate green (uv run pytest + ruff check src tests alembic +
#      ruff format --check src tests alembic + mypy src/friendex);
#      ≥85% coverage on touched files; no new deps (pyproject.toml +
#      uv.lock byte-identical).
#
# Phase 17b (/fund invest + APY-to-investors + manager-cap withdraw):
#  B1. FundService.invest(investor_id, fund_id, amount) becomes functional:
#       - amount <= 0 → InvalidAmount("amount must be positive")
#       - missing fund (fund_repo.get returns None) → InvalidAmount or
#         appropriate domain error (chosen by impl; doc in baton)
#       - self-invest blocked: investor_id == fund.manager_id →
#         InvalidAmount("cannot invest in own fund")
#       - investor cash insufficient → InsufficientFunds
#       - on success: debit investor account cash, credit fund.cash_balance,
#         increment fund.investors[investor_id] (+= amount or set if absent)
#       - atomic under composite locked(investor_lock_key, fund_lock_key)
#         (lock-manager is non-reentrant; reuse Phase 8e/8f compose pattern)
#      RED-first per sub-clause; record actual failing outputs in baton.
#  B2. FundService.withdraw caps amount at
#      fund.cash_balance - sum(investors.values()). Investor principal
#      untouchable by manager. RED: fund cash=$1000, investor stake=$400;
#      manager attempting $700 withdraw raises FundInsufficientBalance
#      (need=$700, have=$600); manager withdrawing $600 succeeds and
#      investor stake stays at $400.
#  B3. FundService.accrue_apy per-stake split:
#       - manager_balance = fund.cash_balance - sum(investors.values())
#       - manager_accrual = compute_apy_accrual(manager_balance,
#         effective_apy, period=settings.hedge_fund_base_apy_period)
#       - per investor: investor_accrual = compute_apy_accrual(stake,
#         effective_apy, period=...); new_investors[id] = old_stake +
#         investor_accrual
#       - upsert atomic: fund.cash_balance = manager_balance +
#         manager_accrual + sum(new_investors.values()); fund.investors
#         = new_investors
#       - skip when sum of all accruals < 1 cent (idempotency)
#      RED tests: single-investor split; two-investor split; investor
#      stake unchanged when manager-only (investors={}) — equivalent to
#      pre-17b behavior.
#  B4. fund_cog.py /fund invest docstring rewritten to describe live
#      success + error paths; "deferred per §Open-Q5" / "raises
#      NotImplementedError" language removed.
#  B5. Full gate green; ≥85% application coverage; no new deps
#      (pyproject.toml + uv.lock byte-identical).
#
# Phase 17c (Q10 auto-DM intro + smoke pin update + runbook):
#  C1. ActivityService.opt_in_and_consume_intro(user_id) -> bool (NEW):
#      atomic RMW that sets opt_in=True and, when intro_shown was False
#      flips intro_shown=True and returns True; on subsequent calls
#      (intro_shown=True) just ensures opt_in=True and returns False.
#      Locks the existing user-account lock key. Existing set_opt_in +
#      mark_intro_shown remain for callers that don't need the
#      first-time signal (e.g. /optout).
#  C2. AccountCog.optin calls opt_in_and_consume_intro; if it returns
#      True, attempts await interaction.user.send(embed=
#      build_intro_embed(), allowed_mentions=discord.AllowedMentions.none());
#      on discord.Forbidden falls back to ephemeral confirmation with
#      the intro embed attached. Normal (subsequent) path unchanged:
#      ephemeral "Opted in" confirmation. /optout unchanged. No new
#      try/except on DomainError (Phase 13 rule).
#  C3. scripts/smoke_test_commands.py STEPS[id=18].expected rewritten
#      to live invest semantics: happy path (deducts investor cash,
#      credits fund.cash_balance, records investor stake, public
#      reply) / self-invest blocked / insufficient investor cash. The
#      "NotImplementedError" and "deferred to Phase 17" / "Phase 11c"
#      language is removed. ids and category and ordering unchanged.
#  C4. tests/scripts/test_smoke_test_commands.py: delete
#      test_fund_invest_step_notes_not_implemented_error; add positive
#      pin test_fund_invest_step_describes_live_invest_path asserting
#      expected text contains "stake" AND lowercase expected DOES NOT
#      contain "notimplementederror" / "deferred". The /balance-style
#      coverage + listener-event + background-task tests stay green
#      unchanged.
#  C5. docs/runbook-smoke-test.md gains an "Invest flow" sub-section
#      (operator runs /fund invest happy / self-invest / insufficient
#      cash) AND an "Intro DM" sub-section (operator runs /optin on a
#      fresh account; expects a DM with the intro embed; ensures a
#      DM-closed account falls back to ephemeral).
#  C6. Full gate green; no new deps; smoke driver remains byte-stable
#      across two runs (new md5 baseline captured in the PR body).
#
# MUST honour from prior digests (every unit prompt carries):
#  - Phase 16: STEP 18 + test_fund_invest_step_notes_not_implemented_error
#    are the explicit Phase-17 carry-forward. Single-source-of-truth runbook
#    (script enumerates; runbook does NOT). STEPS tuple immutability +
#    id ordering load-bearing. No discord import in scripts/.
#  - Phase 15b: migrator flags out of scope here.
#  - Phase 14: bot startup + factory layout unchanged.
#  - Phase 13: cogs/listeners NEVER catch DomainError; tree-wide error
#    handler routes DomainError → ephemeral user_facing_message. NEW
#    feature surfaces (auto-DM intro) must not introduce a
#    try/except DomainError pattern.
#  - Phase 12a/12b: kinds "timeout"/"ban"; AllowedMentions.none() on every
#    send (including the new DM send).
#  - Phase 11c: /fund subcommands (create, invest, withdraw, info); cog
#    layer never catches DomainError; AllowedMentions.none() on sends.
#  - Phase 10: COLOR_* palette reused; embed builders are kw-only;
#    Decimal money formatting via _money helper.
#  - Phase 9: per-guild factories with N=2 sweep / N=1 reset; no discord
#    import in adapters/tasks/.
#  - Phase 8e: AlreadyClaimedToday under DomainError; compose lock keys
#    via f"{guild}:{user_or_fund}"; LockManager non-reentrant — never
#    nest locked() calls.
#  - Phase 8c: ITradeCooldownRepo.get(now=...) signature.
#  - Phase 3.1: Decimal money + UTC-aware datetimes (Decimal(str(float))
#    for float-sourced rates).
#  - ADR-0001: guild_id is a runtime ctor dimension; domain models
#    stay guild-agnostic.
#
# Branches:
#  phase-17a: feat/phase-17a-toggles, base origin/main@a205e40
#  phase-17b: feat/phase-17b-invest, base feat/phase-17a-toggles
#  phase-17c: feat/phase-17c-intro-runbook, base feat/phase-17b-invest
#
# Phases:
phases:
  - id: phase-17a  spec: docs/04-migration-plan.md §Phase 17 (lines 858-890) — config toggles split
    readiness: READY  (post-signoff)
    unit_agent: python-pro
    branch: feat/phase-17a-toggles  pr: -  digest: baton-runner/br-2026-05-28-phase-17/digest-phase-17a.md
    units: 0  state: PENDING
  - id: phase-17b  spec: docs/04-migration-plan.md §Phase 17 (lines 858-890) — /fund invest + APY split
    readiness: READY  (post-signoff)
    unit_agent: python-pro
    branch: feat/phase-17b-invest  pr: -  digest: baton-runner/br-2026-05-28-phase-17/digest-phase-17b.md
    units: 0  state: PENDING
  - id: phase-17c  spec: docs/04-migration-plan.md §Phase 17 (lines 858-890) — intro DM + smoke pin + runbook
    readiness: READY  (post-signoff)
    unit_agent: python-pro
    branch: feat/phase-17c-intro-runbook  pr: -  digest: baton-runner/br-2026-05-28-phase-17/digest-phase-17c.md
    units: 0  state: PENDING
