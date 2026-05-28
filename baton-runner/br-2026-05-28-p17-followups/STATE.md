# baton-runner run br-2026-05-28-p17-followups
status: DONE
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-28-p17-followups
phase: 1 of 1  unit: -  review_iter: 1 of 3 (CLEAN)
current_baton: baton-pass/p17-followups/001-2026-05-28-review-clean.md
units_used: 2
pause_reason: -
budgets: { global_ceiling: 25, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 17 follow-ups bundled as a single small phase.
#  Base: origin/main @ 994f3d9 (after Phase 17a/b/c merged via #71/#72/#73).
#  Unit agent: python-pro (project default).
#  Branch: feat/p17-followups (single PR, base main).
#
# Acceptance criteria:
#  F1. tests/application/test_fund_service.py — add a test pinning
#      fund.investors dict-identity freshness in FundService.invest.
#      Seed a fund with original_investors_dict={}, keep a ref; call
#      invest(...); assert original_investors_dict is unchanged AND
#      the upserted fund's investors dict is a fresh object (not
#      the same as original_investors_dict). Mutation: making invest
#      mutate the input dict in place must fail this test (this is
#      Phase 17b review's M5 gap).
#  F2. tests/adapters/discord_bot/cogs/test_account_cog.py — add a
#      test pinning opt_in_and_consume_intro → send/send_message
#      call order in AccountCog.optin. Use a MagicMock() parent with
#      .attach_mock for activity_service.opt_in_and_consume_intro,
#      interaction.user.send, and interaction.response.send_message;
#      assert parent.mock_calls[0] is the opt_in_and_consume_intro
#      call. Mutation: reversing the order (ack-before-consume) must
#      fail this test (this is Phase 17c review's M2 gap).
#  F3. src/friendex/adapters/discord_bot/cogs/account_cog.py — in
#      the discord.Forbidden fallback branch, add a structured log
#      line so DM-closed users become observable. Use the established
#      project logger pattern (look at how other cogs/listeners log;
#      Phase 2 structured-logging adapter). Level: info. Keys at
#      minimum: user_id + guild_id; do NOT log embed contents. Add a
#      caplog (or matching pattern) test asserting the line fires
#      under the Forbidden path; mutation (removing the log call)
#      must fail.
#  F4. Verification gate:
#       - bash scripts/gate.sh baton-runner/br-2026-05-28-p17-followups/gate-work/
#       - GATE: PASS (pytest + ruff check + ruff format --check + mypy).
#       - ≥85% coverage on touched files.
#       - No new runtime deps; git diff origin/main -- pyproject.toml uv.lock
#         must be empty.
#       - File allow-list:
#         * src/friendex/adapters/discord_bot/cogs/account_cog.py
#         * tests/application/test_fund_service.py
#         * tests/adapters/discord_bot/cogs/test_account_cog.py
#         * (optional) one module-level logger import target if it
#           doesn't yet exist in account_cog.py (declare in baton).
#       - No edits outside this list. Especially NOT scripts/, docs/,
#         alembic/, domain/, persistence/.
#
# MUST honour (Phase 17 invariants from the merged digests):
#  - Decimal-at-the-boundary + UTC-aware datetimes (Phase 3.1).
#  - Composite lock keys f"{guild_id}:{user_id_or_fund_id}"; LockManager
#    non-reentrant (Phase 8e).
#  - No `discord` import in domain/, application/, persistence/, tasks/,
#    or scripts/ (Phase 9 boundary; Phase 17c reaffirmed).
#  - Cogs/listeners NEVER catch DomainError (Phase 13). The only
#    try/except permitted in /optin is the existing narrow
#    discord.Forbidden; F3 adds ONE structured log call inside
#    that existing except branch — does NOT widen scope.
#  - AllowedMentions.none() on every send (unchanged).
#  - STEPS tuple immutability (Phase 16) — no scripts/ edits in F.
#  - Phase 17a digest: hedge_fund_base_apy_period setting still wired
#    through fund_service.accrue_apy (unchanged in F).
#  - Phase 17b digest: invest semantics + composite lock + per-stake
#    APY split unchanged (F1 only adds a test, no product edit).
#  - Phase 17c digest: opt_in_and_consume_intro contract unchanged
#    (F2 only adds a test; F3 only adds a log call inside an existing
#    except branch).
#
# Phases:
phases:
  - id: p17-followups  spec: phase-17 review carry-forwards (LOW M5, LOW M2, INFO Forbidden-log)
    readiness: READY  (post-signoff)
    unit_agent: python-pro
    branch: feat/p17-followups  pr: -  digest: baton-runner/br-2026-05-28-p17-followups/digest-p17-followups.md
    units: 0  state: PENDING
