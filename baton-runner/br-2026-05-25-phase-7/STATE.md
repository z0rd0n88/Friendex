# baton-runner run br-2026-05-25-phase-7
status: RUNNING
worktree: /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-7
phase: 1 of 1  unit: REVIEW  review_iter: 1 of 3
current_baton: baton-runner/br-2026-05-25-phase-7/baton-phase-7-work.md
units_used: 1
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }
phases:
  - id: phase-7  spec: docs/04-migration-plan.md §Phase 7 (+ issue #2)  readiness: READY
    work_agent: general-purpose
    branch: feat/phase-7-locks  pr: -  digest: baton-runner/br-2026-05-25-phase-7/digest-phase-7.md
    units: 1  state: RUNNING
    decisions:
      - API shape per migration plan: only public `locked()`; private `_ensure_lock(uid)`; NO public `acquire()`.
      - PR base: main (single-phase run, not stacked).
