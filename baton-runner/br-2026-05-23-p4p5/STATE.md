# baton-runner run br-2026-05-23-p4p5

status: RUNNING
worktree: /home/alex/Friendex/.claude/worktrees/br-2026-05-23-p4p5
phase: 2 of 2  unit: REVIEW  review_iter: 0 of 3
current_baton: pass-baton/phase-5-orm/001-2026-05-23-alembic-baseline-done.md
units_used: 5
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

## Decisions (user signoff 2026-05-23)
- numeric_policy: Decimal-at-boundary. Money/price params & returns are Decimal
  (match domain models); rate/factor tunables stay float (match Settings);
  convert to float only for transcendental math (ln), return Decimal quantised
  to currency precision (Decimal('0.01')). activity.py scores stay float.
- right_sizing: each phase split into 2 ordered work sub-units; ONE phase-level
  review + ONE digest + ONE stacked draft PR per phase.
- work_agent: general-purpose (only viable agent with Skill tool for tdd/pass-baton).
- gate: option A — scripts/gate.sh scopes ruff to `src tests` (pre-existing
  .githooks/gen_arch.py cruft excluded). Baseline gate: PASS.

## Phases
- id: phase-4  spec: docs/04-migration-plan.md §"Phase 4 — Domain Pure Functions" + Refs #2  readiness: THIN (Decimal correction applied; activity-K from docs/spec/original-skeleton.md)
    work_agent: general-purpose
    branch: feat/br-2026-05-23-p4p5/phase-4   pr: https://github.com/z0rd0n88/Friendex/pull/31 (draft, base main)   digest: baton-runner/br-2026-05-23-p4p5/digest-phase-4.md
    sub_units:
      - 4a: price_engine.py + activity.py (+ tests/domain/test_price_engine.py, test_activity.py, conftest.py)  state: DONE  baton: pass-baton/phase-4-domain-funcs/001-2026-05-23-price-engine-activity-done.md
      - 4b: market_hours.py + fund_math.py (+ tests/domain/test_market_hours.py, test_fund_math.py)  state: DONE  baton: pass-baton/phase-4-domain-funcs/002-2026-05-23-market-hours-fund-math-done.md
    units: 3  state: REVIEW-CLEAN (digest written; PR pending)
    review_verdict: CLEAN (iter 1). Findings (non-blocking): 2 MEDIUM (fund_math docstring wrongly says spec leaves calculate_net_worth undefined — it is at spec:320, formula is numerically equivalent so math correct; apply_floor_stall attenuation magnitude unpinned by tests), 2 LOW. Carried into PR #31 body as follow-ups.
    units: 3  state: DONE
- id: phase-5  spec: docs/04-migration-plan.md §"Phase 5 — Persistence: ORM & Alembic Baseline" + docs/02-target-architecture.md §Persistence Option B + Refs #2  readiness: READY
    work_agent: general-purpose
    branch: feat/br-2026-05-23-p4p5/phase-5 (off phase-4 tip)   pr: -   digest: baton-runner/br-2026-05-23-p4p5/digest-phase-5.md
    sub_units:
      - 5a: db.py + types.py + orm.py (+ tests/adapters/persistence/test_orm.py round-trip)  state: DONE  baton: pass-baton/phase-5-orm/000-2026-05-23-orm-roundtrip-done.md
      - 5b: alembic.ini + alembic/env.py + script.py.mako + versions/0001_baseline.py (+ test_migrations.py reversibility)  state: DONE  baton: pass-baton/phase-5-orm/001-2026-05-23-alembic-baseline-done.md
    units: 2  state: WORK-DONE (review next)
    gate_update: scripts/gate.sh ruff scope extended to `src tests alembic` for phase-5; validated GATE: PASS.
    review_notes:
      - 5a added unplanned types.py (DecimalText/UtcDateTime TypeDecorators) — review the extraction.
      - 5b no-diff check asserts table+column-name set equality (compare_metadata too noisy for SQLite TypeDecorator columns) — verify this is adequate.

## Deferred follow-ups (user: "make sure we come back to those") — resurface in final summary
1. docs/04-migration-plan.md Phase 4 signatures still say float → correct to Decimal.
2. Activity-K / scalarisation: no activity_return_k in Settings; ActivityBucket→scalar weighting needs a durable decision (maybe a Phase 2 config tunable).
3. .githooks/gen_arch.py ruff cruft (11 errors + 1 format) → own chore PR.
