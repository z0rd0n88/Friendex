# baton-runner run br-2026-05-27-phase-15
status: RUNNING
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-15
phase: 1 of 2  unit: WORK  review_iter: 0 of 3
current_baton: -
units_used: 0
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 15 of the migration plan: JSON-to-SQLite Migration Verification.
#  Spec: docs/04-migration-plan.md §Phase 15 (lines 793-824).
#  Phase-14 carry-forward digest: baton-runner/br-2026-05-27-phase-14/digest-phase-14.md
#  User signed off 2026-05-27 to SPLIT Phase 15 into 15a + 15b (stacked PRs).
#  Unit agent: python-pro (work + review + fix) — project default.
#
# Signoff decisions (user 2026-05-27):
#  Q1. Split: 15a = realistic fixtures + integration test against existing
#      migrator (no flag changes). 15b = migrator --dry-run + --report +
#      orphan consistency check, stacked on 15a.
#  Q2. Orphan consistency check is WARN-NOT-FAIL — logger.warning per orphan,
#      never raised, migrator continues + exits 0. Test asserts no
#      MigrationError + uses caplog to confirm warnings emit on a seeded orphan.
#  Q3. --guild-id stays REQUIRED on the migrator CLI (the Phase-15 spec
#      example that omits it is updated). ADR-0001 per-guild scope.
#  Q4. --report on --dry-run shows would-have-migrated counts (counts captured
#      pre-rollback inside the transaction).
#  Q5. events_wallet is counted on the `funds` table line in --report
#      (matches the migrator's existing accounting in migrate() return dict).
#
# Phases:
phases:
  - id: phase-15a  spec: docs/04-migration-plan.md §Phase 15 (lines 793-824) — fixtures + test split
    readiness: READY
    unit_agent: python-pro
    branch: feat/phase-15a-fixtures   pr: -   digest: baton-runner/br-2026-05-27-phase-15/digest-phase-15a.md
    units: 0   state: PENDING
  - id: phase-15b  spec: docs/04-migration-plan.md §Phase 15 (lines 793-824) — migrator flags + orphan check split
    readiness: READY
    unit_agent: python-pro
    branch: feat/phase-15b-migrate-flags  pr: -  digest: baton-runner/br-2026-05-27-phase-15/digest-phase-15b.md
    units: 0   state: PENDING

# Acceptance criteria locked at signoff:
#
# Phase 15a (realistic fixtures + integration test):
#  A1. Create tests/fixtures/json/realistic/{users,prices,funds,fund_penalties}.json
#      with the following non-vacuous shape (mirrors existing
#      tests/fixtures/json/* schema; UTC-naive ISO timestamps):
#        - users.json: 50 entries. Each has cash_balance, net_worth,
#          month_start_net_worth, portfolio.long (>=1 holding for >=30 users),
#          portfolio.short (>=1 short for >=20 users — some `frozen: true`),
#          activity.today AND activity.yesterday populated (text_msgs,
#          media_msgs, voice_minutes, voice_unique_channels list,
#          reaction_count, reply_count, role_ping_joins, ping_responses),
#          daily_streak across the full spectrum (0, 1, 3, 6, 7, 14, 30),
#          last_daily_claim timestamp varying.
#        - prices.json: 50 entries keyed by user_id. Each has current,
#          history (>=3 datapoints with ascending timestamps spanning ~24h),
#          high_24h, low_24h, all_time_high. >=20 stocks reference shared
#          user_ids in users.json (long/short targets); the remaining
#          stocks may target absent users (the existing migrator already
#          handles this — orphan-check is a Phase-15b concern).
#        - funds.json: 30 entries with name, manager_id (id in users.json),
#          cash_balance, investors (>=10 funds have >=2 investors,
#          remaining have 0 or 1). PLUS one `events_wallet` pseudo-fund
#          per existing schema.
#        - fund_penalties.json: 10 entries keyed by user_id (mix of users
#          in users.json), penalty_apr in {0.05, 0.10}, penalty_until at
#          various stages of expiry (some hours away, some days, none
#          already expired since penalty store filters expired on read).
#  A2. tests/integration/test_migration_realistic.py — new file. Uses
#      sqlite+aiosqlite:///:memory: target, guild_id="999". Exercises:
#       (a) Run the migrator against tests/fixtures/json/realistic/.
#       (b) Spot-check at least 5 read-side service methods (use UserRepo,
#           FundRepo, PriceRepo, PenaltyRepo) against derived expectations
#           computed from the JSON fixtures (e.g., total long positions,
#           total fund investors, sum of cash, a single user's portfolio,
#           a single fund's investor map, a single price's history length).
#       (c) Re-run the migrator a second time; assert row counts per table
#           are IDENTICAL to the first run (idempotency).
#      Test must be marked @pytest.mark.asyncio.
#
# Phase 15b (migrator --dry-run + --report + orphan consistency check):
#  B1. --dry-run flag (argparse store_true). When set, the migrator wraps
#      writes inside a transaction that is explicitly ROLLED BACK before
#      engine.dispose(); no rows persist. Exits 0 on success.
#  B2. --report flag (argparse store_true). When set, after the migration
#      (or dry-run) prints the per-table counts dict to stdout in a stable
#      format (one line per table: `<table>: <count>`). Counts always
#      reflect what WAS or WOULD HAVE BEEN written (i.e., captured before
#      rollback on dry-run).
#  B3. Post-migration consistency check (runs unconditionally): every
#      LongPosition.target_user_id must exist in user_accounts; for each
#      orphan, emit logger.warning(...) including owner_id and target_id.
#      Never raise. The check fires on both real and dry-run paths.
#      events_wallet is excluded from the user_accounts check (it's a fund,
#      not a user).
#  B4. tests/integration/test_migration_realistic.py extended (or sibling
#      test_migration_dry_run.py) with:
#       - --dry-run path: no rows persist to the target (re-query yields 0
#         users / 0 long_positions / 0 funds).
#       - --report path: capsys captures stdout, asserts each table line
#         is present with the expected count.
#       - Orphan path: a tiny derived fixture introduces one
#         LongPosition.target_user_id that is NOT in users.json; caplog
#         captures the warning and asserts the orphan target_id appears
#         in the message; no MigrationError raised; CLI exit code 0.
#
# Phase-15a MUST honour (Phase-14 carry-forwards still apply):
#  H1. Decimal+UTC invariant — fixture timestamps remain ISO strings; the
#      migrator parses them; do not re-shape its parsing in 15a.
#  H2. Per-guild scope — every fixture row is migrated under one guild_id
#      (`"999"` chosen for the test); read-back assertions key by
#      (guild_id, user_id).
#  H3. No new deps. No pyproject.toml/uv.lock churn.
#  H4. AllowedMentions/discord boundary irrelevant (no discord_bot touch).
