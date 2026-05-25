# baton-runner run br-2026-05-24-phase-6
status: RUNNING
worktree: /home/alex/Friendex/.claude/worktrees/phase-6-repos
phase: 6 of 6  unit: WORK  review_iter: 0 of 3
current_baton: pass-baton/phase-6-repos/010-2026-05-24-6e-penalty-cooldown-state-repos-review.md
units_used: 10
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

## Run shape

Phase 6 of the Friendex migration plan ("Persistence: Repositories & JSON
Migrator") is split into 6 ordered sub-units, all committed to a single branch
`feat/phase-6-repos`; ONE draft PR opens at the end (matches the plan's
single-branch / 7-commit intent). Each sub-unit: WORK -> commit -> REVIEW
(gate + code-review + ecc-security-review) -> FIX (<=3) -> phase-exit digest.

- Work agent: general-purpose (has Skill tool for tdd/pass-baton).
- Spec: docs/04-migration-plan.md Section "Phase 6"; ADR-0002 (FK enforcement).
- Maps to GitHub issue #2 (Phase 6 box) -> PR body carries `Refs #2`.

## Sub-units (treated as phases in the loop)

- id: 6a-fk-migration  spec: plan Phase 6 + ADR-0002  readiness: READY
  work_agent: general-purpose
  scope: PRAGMA foreign_keys=ON listener in db.py; Alembic 0002 migration adding
    ON DELETE CASCADE to all child FKs (render_as_batch for SQLite); + 2 Phase 5
    carry-forwards (Decimal-quantisation assertions MEDIUM; real drift test LOW).
  branch: feat/phase-6-repos  pr: -  digest: baton-runner/br-2026-05-24-phase-6/digest-phase-6a.md
  units: 2  state: DONE (VERDICT CLEAN; 1 MEDIUM non-blocking: 0002 no-op on fresh DB / ADR narrative drift; 2 LOW)
- id: 6b-interfaces  spec: plan Phase 6  readiness: READY
  work_agent: general-purpose
  scope: application/interfaces.py - 6 Protocols (IUserRepo, IPriceRepo,
    IFundRepo, IPenaltyRepo, ITradeCooldownRepo, ISystemStateRepo); no adapters imports.
  digest: baton-runner/br-2026-05-24-phase-6/digest-phase-6b.md
  units: 2  state: DONE (VERDICT CLEAN; DTO placement judged sound; 2 LOW non-blocking)
- id: 6c-user-repo  spec: plan Phase 6  readiness: READY
  work_agent: general-purpose
  scope: SqlUserRepository + test; deletion-cascade test proves 6a FK wiring.
  digest: baton-runner/br-2026-05-24-phase-6/digest-phase-6c.md
  units: 2  state: DONE (VERDICT CLEAN; 1 MEDIUM N+1 in list_all + 2 LOW, non-blocking)
- id: 6d-price-fund-repos  spec: plan Phase 6  readiness: READY
  work_agent: general-purpose
  scope: SqlPriceRepository + SqlFundRepository + tests.
  digest: baton-runner/br-2026-05-24-phase-6/digest-phase-6d.md
  units: 2  state: DONE (VERDICT CLEAN; no findings)
- id: 6e-penalty-cooldown-state-repos  spec: plan Phase 6  readiness: READY
  work_agent: general-purpose
  scope: SqlPenaltyRepository + SqlTradeCooldownRepository + SqlSystemStateRepository + tests.
  digest: baton-runner/br-2026-05-24-phase-6/digest-phase-6e.md
  units: 2  state: DONE (VERDICT CLEAN; no findings, 2 NOTE-only)
- id: 6f-migrator  spec: plan Phase 6  readiness: READY
  work_agent: general-purpose
  scope: migrate_json_to_sqlite.py + json fixtures + idempotency test + __init__ re-exports.
  digest: -  units: 0  state: PENDING

## Resume point

6a-6e DONE (all VERDICT CLEAN, digests written). Next action: spawn WORK unit
for 6f-migrator (JSON->SQLite migrator + fixtures + idempotency test). After 6f
CLEAN: open the single Phase 6 draft PR (base main), then STATE=DONE.
