# Pass-Baton: Wrap LiquidationService cover call in UoW envelope (kickoff)

**Date:** 2026-06-05
**Scope:** issue-95-uow
**Branch:** fix/liquidation-uow
**Worktree:** /home/alex/Friendex/.claude/worktrees/issue-95-uow
**HEAD:** 3d8683e feat(review): wire mode presets to user-scope diff/slices/--prompt-prelude

## Where things stand

Closing GitHub issue #95 — `LiquidationService._maybe_liquidate` currently calls
`TradingService.cover_forced` outside any `_uow.transaction()` envelope, so a
mid-helper persistence failure during liquidation can persist a target stub
without the accompanying money writes. The user-facing `TradingService.cover()`
entry point already wraps the same body in a UoW. The fix is:

1. Inject `unit_of_work: IUnitOfWork | None = None` (kw-only, defaulting to
   `NullUnitOfWork()`) into `LiquidationService.__init__`, mirroring the
   `TradingService.__init__` signature (`src/friendex/application/trading_service.py:121`).
2. Wrap the `cover_forced(...)` call site in
   `async with self._uow.transaction():`.
3. Add a regression test pinning the rollback contract using `FakeUnitOfWork`
   (threaded into BOTH `TradingService` and `LiquidationService`) plus an
   `_ExplodingPriceRepo`-style decorator (mirrors
   `tests/application/test_trading_service_atomicity.py:300`).
4. Wire the container: `_make_liquidation_factory` already holds
   `self._unit_of_work` — pass `unit_of_work=self._unit_of_work` into the
   `LiquidationService(...)` ctor call (around
   `src/friendex/adapters/container.py:458`).
5. Replace the three "tracked in issue #95" docstring paragraphs with a short
   post-fix invariant sentence on each of:
   - `liquidation_service.LiquidationService._maybe_liquidate` (lines 146-157)
   - `trading_service.TradingService._cover_internal` (the "UoW envelope
     responsibility" paragraph, ~lines 769-784)
   - `trading_service.TradingService.cover_forced` (the parenthetical
     reference, ~line 717).

Reusable seams confirmed read:
- `IUnitOfWork` Protocol + `NullUnitOfWork` fallback live at
  `src/friendex/application/unit_of_work.py`.
- `FakeUnitOfWork` already snapshots `_store` (and `_history` for price repo).
- `_ExplodingPriceRepo` template at `tests/application/test_trading_service_atomicity.py:300-342`.
- `_make_services` helper at `tests/application/test_liquidation_service.py:116-145`
  needs an optional `unit_of_work` parameter threaded into BOTH constructors.

## Next steps

1. RED — add `test_liquidation_rolls_back_on_mid_helper_failure` in
   `tests/application/test_liquidation_service.py`; extend `_make_services` to
   accept `unit_of_work` and pass it to both ctors. Capture the failing output
   verbatim into this baton's next entry.
2. GREEN — add `unit_of_work: IUnitOfWork | None = None` (kw-only) to
   `LiquidationService.__init__`, wrap `cover_forced` call site in
   `async with self._uow.transaction():`.
3. Container wiring — pass `unit_of_work=self._unit_of_work` into
   `LiquidationService(...)` in `_make_liquidation_factory`.
4. Docstring cleanup — replace the three "tracked in issue #95" blocks.
5. Verify all gates: `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, `uv run mypy src/friendex`.
6. Update this baton with progress; write final phase-closure entry on COMPLETE.

## Open questions / risks

- Bail budget: ~50 tool calls / ~10 files touched. Files anticipated: 3
  source (`liquidation_service.py`, `trading_service.py`, `container.py`) +
  1 test (`test_liquidation_service.py`) + baton entries. Well within budget.

## References

- Issues: #95
- Source PR ref: #94 (review L2 introduced the documented gap)
- Spec: `src/friendex/application/trading_service.py:121-156` (signature
  to mirror), `src/friendex/application/trading_service.py:639-701` (cover() —
  the working pattern), `src/friendex/application/trading_service.py:703-729`
  (cover_forced delegation), `tests/application/test_trading_service_atomicity.py:300-389`
  (rollback test template).
