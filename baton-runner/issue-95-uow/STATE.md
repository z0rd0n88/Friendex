# baton-runner run issue-95-uow

status: RUNNING
worktree: /home/alex/Friendex/.claude/worktrees/issue-95-uow
phase: 1 of 1
unit: REVIEW
review_iter: 0 of 3
current_baton: baton-pass/issue-95-uow/002-2026-06-05-uow-fix-complete.md
units_used: 1
pause_reason: -
budgets: { global_ceiling: 75, phase_thrash: 20, bail_calls: 50, bail_files: 10 }

phases:
  - id: phase-1
    spec: https://github.com/z0rd0n88/Friendex/issues/95
    readiness: READY
    unit_agent: python-pro
    branch: fix/liquidation-uow
    pr: -
    digest: baton-runner/issue-95-uow/digest-phase-1.md
    units: 1
    state: WORK_DONE
    acceptance_criteria:
      - "AC1: LiquidationService._maybe_liquidate calls cover_forced inside self._uow.transaction()."
      - "AC2: A test pins the rollback — an injected mid-helper failure leaves the target stub un-persisted (partial money writes rolled back). Uses FakeUnitOfWork + an exploding repo decorator, mirroring tests/application/test_trading_service_atomicity.py::test_short_rolls_back_user_and_fund_when_price_write_fails."
      - "AC3: The 'tracked in issue #95' notes are stripped from docstrings on liquidation_service._maybe_liquidate, trading_service._cover_internal, and trading_service.cover_forced."
    work_files_touched: 5
    work_self_report:
      - "1061 pytest pass, ruff check pass, ruff format pass, mypy clean (0 errors, 75 source files)."
      - "RED captured in baton 001 before GREEN; AC2 test verifies cash/short/event/rollback-count contract."

notes:
  - "Single-phase run (3 ACs, ~3 source files + 1 test file — fits one phase)."
  - "Branch fix/liquidation-uow already created from main@3d8683e; PR base = main."
  - "User pre-approved via plan; no signoff prompt required."
