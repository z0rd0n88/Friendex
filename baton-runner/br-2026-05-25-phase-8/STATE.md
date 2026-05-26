# baton-runner run br-2026-05-25-phase-8
status: RUNNING
worktree: /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
phase: 2 of 7  unit: REVIEW  review_iter: 1 of 3
current_baton: pass-baton/phase-8a/002-2026-05-25-phase-8a-complete.md
units_used: 3
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

# Run shape (user signoff 2026-05-25):
#  - Reorder per user: a dedicated test-double infra phase (phase-8-fakes) runs
#    FIRST so 8a-8f service tests have in-memory repos + conftest fixtures.
#  - 8a folds in the Phase-7 carry-forward fix (cancel-mid-acquire lock leak in
#    src/friendex/application/lock_manager.py: track acquired, release in finally).
#  - All units (work/review/fix) spawn as python-pro (project default, PR #41).
#  - Stacked draft PRs: fakes->main, 8a->fakes, 8b->8a, 8c->8b, 8d->8c, 8e->8d, 8f->8e.
#  - Master tracking issue: #2.

# Continuity digests threaded to work units (paths only; units read them):
#  - baton-runner/br-2026-05-25-phase-7/digest-phase-7.md         (LockManager conventions)
#  - baton-runner/br-2026-05-23-p4p5/digest-phase-4.md            (domain pure functions)
#  - baton-runner/br-2026-05-24-phase-6/digest-phase-6*.md        (repository semantics)
#  - src/friendex/application/interfaces.py                       (I*Repo Protocols)
#  - + accumulating digest-phase-8*.md as each phase completes.

phases:
  - id: phase-8-fakes  spec: "inline (test-double infra; derived from migration plan §8b fakes)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-8-fakes  base: main  pr: https://github.com/z0rd0n88/Friendex/pull/42  digest: baton-runner/br-2026-05-25-phase-8/digest-phase-8-fakes.md
    units: 2  state: DONE  review: iter-1 CLEAN; 1 LOW (fakes return mutable refs vs fresh objects — convention in digest) + 1 INFO
  - id: phase-8a  spec: "docs/04-migration-plan.md §Phase 8a (+ lock-leak carry-forward)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-8a-activity  base: feat/phase-8-fakes  pr: -  digest: baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md
    units: 0  state: RUNNING  note: "large (~10 criteria + lock fix); continuation-backed"
  - id: phase-8b  spec: "docs/04-migration-plan.md §Phase 8b (price tick; reuses fakes)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-8b-price-tick  base: feat/phase-8a-activity  pr: -  digest: baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md
    units: 0  state: PENDING
  - id: phase-8c  spec: "docs/04-migration-plan.md §Phase 8c (trading)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-8c-trading  base: feat/phase-8b-price-tick  pr: -  digest: baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md
    units: 0  state: PENDING  note: "most complex; 90% cov gate; continuation-backed"
  - id: phase-8d  spec: "docs/04-migration-plan.md §Phase 8d (portfolio + stats)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-8d-portfolio  base: feat/phase-8c-trading  pr: -  digest: baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md
    units: 0  state: PENDING
  - id: phase-8e  spec: "docs/04-migration-plan.md §Phase 8e (fund + daily)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-8e-fund-daily  base: feat/phase-8d-portfolio  pr: -  digest: baton-runner/br-2026-05-25-phase-8/digest-phase-8e.md
    units: 0  state: PENDING
  - id: phase-8f  spec: "docs/04-migration-plan.md §Phase 8f (liquidation + discipline)"  readiness: READY
    unit_agent: python-pro
    branch: feat/phase-8f-liq-disc  base: feat/phase-8e-fund-daily  pr: -  digest: baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md
    units: 0  state: PENDING
