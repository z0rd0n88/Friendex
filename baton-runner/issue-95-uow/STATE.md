# baton-runner run issue-95-uow

status: DONE
worktree: /home/alex/Friendex/.claude/worktrees/issue-95-uow
phase: 1 of 1
unit: -
review_iter: 1 of 3 (CLEAN on iter 1)
current_baton: baton-pass/issue-95-uow/003-2026-06-05-review-clean.md
units_used: 2
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

phases:
  - id: phase-1
    spec: https://github.com/z0rd0n88/Friendex/issues/95
    readiness: READY
    unit_agent: python-pro
    branch: fix/liquidation-uow
    pr: https://github.com/z0rd0n88/Friendex/pull/99
    digest: baton-runner/issue-95-uow/digest-phase-1.md
    units: 2
    state: DONE
    acceptance_criteria:
      - "AC1: LiquidationService._maybe_liquidate calls cover_forced inside self._uow.transaction()."
      - "AC2: A test pins the rollback — an injected mid-helper failure leaves the target stub un-persisted (partial money writes rolled back). Uses FakeUnitOfWork + an exploding repo decorator."
      - "AC3: The 'tracked in issue #95' notes are stripped from docstrings on liquidation_service._maybe_liquidate, trading_service._cover_internal, and trading_service.cover_forced."
    review_findings:
      critical: 0
      high: 0
      medium: 1
      low: 1
      note: "Both M and L are cosmetic per review baton 003; non-blocking."

notes:
  - "Single-phase run; CLEAN on review iter 1."
  - "Gate: scripts/gate.sh → PASS (1061 pytest, ruff check, ruff format, mypy 0 errors)."
  - "PR opened ready-for-review against main; merge style = rebase (repo convention)."
  - "Post-merge cleanup pending: worktree remove, branch -D, fetch --prune, pull main."
