# Pass-Baton: LiquidationService UoW envelope COMPLETE

**Date:** 2026-06-05
**Scope:** issue-95-uow
**Branch:** fix/liquidation-uow
**Worktree:** /home/alex/Friendex/.claude/worktrees/issue-95-uow
**HEAD:** 3d8683e feat(review): wire mode presets to user-scope diff/slices/--prompt-prelude (uncommitted — manager owns git)

## Where things stand

Issue #95 work-unit is **implementation complete and gate-green**, awaiting
manager commit + PR. All three acceptance criteria are satisfied; the full
test suite, ruff, mypy, and ruff-format pass.

### Acceptance criteria — state pinned

- **AC1 — UoW envelope wraps `cover_forced` call (DONE).** Inside
  `LiquidationService._maybe_liquidate` (in
  `src/friendex/application/liquidation_service.py`) the
  `await self._trading.cover_forced(...)` call is now wrapped in
  `async with self._uow.transaction():`. The `unit_of_work: IUnitOfWork |
  None = None` constructor kwarg is kw-only and defaults to
  `NullUnitOfWork()`, mirroring `TradingService.__init__` (lines 121-147
  of `trading_service.py`) verbatim.
- **AC2 — rollback regression test (DONE).** New test
  `test_liquidation_rolls_back_on_mid_helper_failure` in
  `tests/application/test_liquidation_service.py` uses a local
  `_ExplodingPriceRepo` decorator (mirroring the pattern at
  `tests/application/test_trading_service_atomicity.py:300`) plus
  `FakeUnitOfWork` threaded into BOTH `TradingService` and
  `LiquidationService` (via the extended `_make_services` helper). The
  injected mid-helper failure leaves the holder's cash unchanged, the
  short in full (10 shares), no `LiquidationEvent` is returned, and
  `uow.rollbacks == 1` — proving the envelope opened. RED captured in
  baton 001 BEFORE GREEN; GREEN now verified in 1061-test suite run.
- **AC3 — three "tracked in issue #95" docstring blocks replaced
  (DONE).** All three call sites carry a short post-fix invariant line:
  - `LiquidationService._maybe_liquidate` (former lines 146-157)
  - `TradingService._cover_internal` ("UoW envelope responsibility …" para)
  - `TradingService.cover_forced` (the parenthetical reference)
  Verified clean: `grep -rn "tracked in issue #95"` returns no matches in
  `src/`, `tests/`, or `docs/`.

### Container wiring

`src/friendex/adapters/container.py::_make_liquidation_factory` now passes
`unit_of_work=self._unit_of_work` into the `LiquidationService(...)` call,
matching how `_make_trading_factory` and `_make_fund_factory` already
thread the same `SqlUnitOfWork` instance. Production wiring is therefore
correct end-to-end.

### Gate output (verbatim)

```
$ uv run pytest
======================= 1061 passed, 1 warning in 25.79s =======================

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
174 files already formatted

$ uv run mypy src/friendex
Success: no issues found in 75 source files
```

### Files touched (5 — well under the 10-file budget)

1. `src/friendex/application/liquidation_service.py` — ctor kwarg
   + UoW envelope + docstring rewrite (AC1, AC2 GREEN, AC3 ①).
2. `src/friendex/application/trading_service.py` — `cover_forced` and
   `_cover_internal` docstring rewrites (AC3 ②③).
3. `src/friendex/adapters/container.py` — pass
   `unit_of_work=self._unit_of_work` into `LiquidationService(...)`
   construction.
4. `tests/application/test_liquidation_service.py` — extend
   `_make_services` to thread `unit_of_work` into BOTH ctors; add
   `_ExplodingPriceRepo` decorator + new
   `test_liquidation_rolls_back_on_mid_helper_failure` test (AC2).
5. `baton-pass/INDEX.md` — scope row pointing at the latest baton entry
   for the next session.
6. `baton-pass/issue-95-uow/` — three new baton-pass entries (this one
   plus 000 + 001).

## Next steps

1. Manager commits & opens a PR referencing **Closes #95** in the body
   (auto-close hook).
2. Independent review unit should verify:
   - The RED capture in `baton-pass/issue-95-uow/001` matches what the
     test produces under a clean revert of the `liquidation_service.py`
     ctor + envelope change (mutation: drop the `unit_of_work` kwarg →
     RED reproduces verbatim).
   - The mid-helper failure test is load-bearing: revert the
     `async with self._uow.transaction():` in `_maybe_liquidate`, the
     test should fail with `uow.rollbacks == 0` and the holder's cash
     debited.
   - No further reference to "tracked in issue #95" remains in source.
3. No new dependencies added (no `pyproject.toml` / `uv.lock` diff).

## Open questions / risks

- None. Containment honoured — only the worktree was written to; no
  ~/.claude, main checkout, or other worktrees touched.

## References

- Issues: [#95](https://github.com/z0rd0n88/Friendex/issues/95)
- Source PR (introduced the documented gap): #94 (review L2)
- Tracking: Refs #2 (not a phase PR — defect remediation)
- Code:
  - `src/friendex/application/liquidation_service.py:67-97` (ctor with
    `unit_of_work` kwarg + `_uow` storage).
  - `src/friendex/application/liquidation_service.py:184-192` (the
    `async with self._uow.transaction():` envelope around
    `cover_forced`).
  - `src/friendex/application/trading_service.py:121-147` (mirrored
    ctor pattern — read-only reference).
  - `tests/application/test_liquidation_service.py` — new test +
    `_ExplodingPriceRepo` decorator + extended `_make_services`.
- Previous batons: [000](./000-2026-06-05-uow-wrap-liquidation-kickoff.md),
  [001](./001-2026-06-05-red-test-captured.md).
