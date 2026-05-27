# baton-runner run br-2026-05-27-phase-16
status: DONE
worktree: /home/user/Friendex/.claude/worktrees/br-2026-05-27-phase-16
phase: 1 of 1  unit: -  review_iter: 1 of 3 (CLEAN)
current_baton: baton-pass/phase-16/001-2026-05-27-review-clean.md
units_used: 2
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape:
#  Phase 16 of the migration plan: Production Smoke Test (Cutover).
#  Spec: docs/04-migration-plan.md §Phase 16 (lines 828-854).
#  Phase-15b carry-forward digest: baton-runner/br-2026-05-27-phase-15/digest-phase-15b.md
#  Single sub-phase, 2 files (~docs + script + test), within bail budget of 10.
#  Unit agent: python-pro (work + review + fix) — project default.
#  Branch: feat/phase-16-cutover (base origin/main@a1f40ab).
#  One ready-for-review PR stacked on origin/main.
#
# Signoff decisions (user 2026-05-27):
#  Q1. Driver shape: importable `STEPS: tuple[SmokeStep, ...]` frozen list +
#      a `main()` printer. Runbook references the script as the single
#      source of truth for the step list (no duplicated checklist in MD).
#      Testable via a unit test that asserts coverage of slash commands +
#      listeners + tasks.
#  Q2. Runbook scope: slash commands + listeners + background tasks.
#      Covers every command in CLAUDE.md, every listener event from the
#      Phase 12 surface (on_message text/media activity, on_reaction_add,
#      on_voice_state_update VC join/leave + ping-response timing,
#      on_member_update timeout/ban discipline), and every background loop
#      from the Phase 9 surface (15-min activity tick, short liquidation
#      sweep, daily streak rollover / month_start net-worth capture, hedge
#      fund APY accrual, early-withdrawal penalty decay, VC extra-boost).
#  Q3. Migrator pre-flight: yes, document
#      `uv run python -m friendex.adapters.persistence.migrate_json_to_sqlite
#       --guild-id <id> --dry-run --report` as an OPTIONAL pre-flight step
#      (greenfield deploys with no JSON skip it). Honour the Phase 15b
#      digest decisions (--report sorted alphabetically; orphan check is
#      warn-not-fail; --guild-id required).
#  Q4. Branch: feat/phase-16-cutover (matches migration-plan + earlier
#      phase precedent).
#
# Acceptance criteria locked at signoff (Phase 16):
#  AC1. scripts/smoke_test_commands.py defines a module-level immutable
#       `STEPS: tuple[SmokeStep, ...]` (frozen dataclass with at minimum
#       `id: int`, `category: Literal["startup", "slash", "listener",
#       "background", "shutdown"]`, `name: str`, `command: str`,
#       `expected: str`). Categories COVER:
#         - "startup": bot login (DISCORD_TOKEN), command tree sync (global
#           + optional DEV_GUILD_ID), structured-log "ready" line.
#         - "slash": every command in CLAUDE.md §Bot Commands —
#           /balance, /daily, /price, /mystock, /buy, /sell, /short,
#           /cover, /portfolio, /trending, /mystats, /optin, /optout,
#           plus /fund subcommands (create, invest, withdraw, info) per
#           Phase 11c digest. NOTE: /fund invest's expected outcome must
#           state "NotImplementedError surfaced as ephemeral user-facing
#           error" per Phase 8e §Open-Q5 + Phase 11c digest.
#         - "listener": on_message (text + media activity credit),
#           on_reaction_add (reaction credit + bot-reactor skip),
#           on_voice_state_update (VC join/leave finalisation +
#           ping-response timing per Phase 12b CF-2),
#           on_member_update (timeout + ban discipline penalty per
#           Phase 12a M3 kinds), opt-out blocks tradeability.
#         - "background": 15-min activity tick (ΔP = K · ln(1+score) with
#           activity_tick_k=0.3 per Phase 8-followup chore B), short
#           liquidation sweep at 1.5× entry (Phase 8f F1/F2/F3),
#           hedge-fund APY accrual, early-withdrawal penalty decay,
#           VC extra-boost (1.03× per cycle for retained responders).
#         - "shutdown": graceful close (bot.close() drains task loops).
#  AC2. `uv run python scripts/smoke_test_commands.py` (or
#       `python -m scripts.smoke_test_commands` if importable) prints a
#       deterministic, numbered checklist — one section per step with
#       id, category, name, command, expected outcome. The output is
#       byte-stable across runs (no timestamps, no shuffling) so the
#       operator can diff against a captured baseline. Exit code 0.
#  AC3. tests/scripts/test_smoke_test_commands.py asserts:
#         (a) every slash command in CLAUDE.md is represented exactly once
#             (test enumerates a hard-coded EXPECTED_SLASH_COMMANDS set;
#             a missing or extra step fails);
#         (b) every listener event from Phase 12a/12b digests is present;
#         (c) every background task from Phase 9 digest is present;
#         (d) `main()` prints all STEPS in id order (no shuffling);
#         (e) STEPS is immutable (mutating it raises) — exercised via
#             AttributeError or TypeError on attempted re-assignment;
#       RED first via TDD; each assertion captured in baton.
#  AC4. docs/runbook-smoke-test.md sections (in this order):
#         - "Pre-flight" (env vars: DISCORD_TOKEN required, DEV_GUILD_ID
#           optional; OPTIONAL migrator `--dry-run --report --guild-id`
#           per Phase 15b digest; alembic head check; `uv sync`).
#         - "Bot launch" (start the bot, observe structured "ready" log,
#           confirm slash command tree is visible in client).
#         - "Step-by-step verification" — points the operator at the
#           script as the source of truth and tells them to run
#           `uv run python scripts/smoke_test_commands.py` to print the
#           checklist; they then execute each step in the staging guild
#           and record pass/fail. The runbook itself does NOT enumerate
#           the steps (single source of truth = script).
#         - "Post-flight" (graceful shutdown via Ctrl-C; confirm DB file
#           created; confirm no ERROR-level log lines).
#         - "Sign-off" block: operator name, date, environment id,
#           DISCORD_TOKEN fingerprint (last 4 chars), pass/fail summary.
#  AC5. `uv run ruff check scripts/` + `uv run ruff format --check scripts/`
#       + `uv run mypy src/friendex` + full `uv run pytest` all green
#       (Phase-16 gate matrix row 16).
#  AC6. No new runtime dependencies (`pyproject.toml` / `uv.lock`
#       unchanged). No edit to `src/friendex/`. No edit to existing
#       tests except the new `tests/scripts/`.
#
# MUST honour from prior digests:
#  - Phase 15b: --dry-run does NOT open --target; --report alphabetical;
#    orphan check warn-not-fail; --guild-id required.
#  - Phase 14:  bot startup sequence (bind_runtime → task.start() →
#    tree.sync() global + optional DEV_GUILD_ID copy_global_to + sync).
#  - Phase 13:  cogs/listeners catch no DomainError; error_handler routes
#    DomainError -> ephemeral user_facing_message.
#  - Phase 12a/12b: kinds are "timeout"/"ban" literals; VC-ping rule
#    matches original-skeleton.md:494-503; AllowedMentions.none() on sends.
#  - Phase 11c: /fund subcommands are create/invest/withdraw/info;
#    /fund invest currently raises NotImplementedError (deferred to
#    Phase 17 per Open-Q5).
#  - Phase 9: activity_tick K=0.3 per Phase 8-followup chore B; reset
#    tasks N=1; sweep tasks N=2 per guild.
#  - Phase 8e: AlreadyClaimedToday under DomainError.
#
# Phases:
phases:
  - id: phase-16   spec: docs/04-migration-plan.md §Phase 16 (lines 828-854)
    readiness: READY  (post-signoff)
    unit_agent: python-pro
    branch: feat/phase-16-cutover  pr: https://github.com/z0rd0n88/Friendex/pull/69  digest: baton-runner/br-2026-05-27-phase-16/digest-phase-16.md
    units: 2  state: DONE   (review CLEAN iter-1, M1-M6 RED-under-mutation verified, 0 findings at any severity)
