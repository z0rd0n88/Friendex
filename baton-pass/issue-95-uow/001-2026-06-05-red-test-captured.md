# Pass-Baton: RED test captured before GREEN implementation

**Date:** 2026-06-05
**Scope:** issue-95-uow
**Branch:** fix/liquidation-uow
**Worktree:** /home/alex/Friendex/.claude/worktrees/issue-95-uow
**HEAD:** 3d8683e feat(review): wire mode presets to user-scope diff/slices/--prompt-prelude

## Where things stand

TDD RED step complete. The new regression test
`test_liquidation_rolls_back_on_mid_helper_failure` plus the extended
`_make_services` helper are in place in
`tests/application/test_liquidation_service.py`. Running the new test BEFORE
any production code change fails as expected — the ctor does not yet accept
the `unit_of_work` kwarg the test threads in.

## RED output (verbatim, captured BEFORE GREEN)

```
$ uv run pytest tests/application/test_liquidation_service.py::test_liquidation_rolls_back_on_mid_helper_failure -xvs

>       liquidation = LiquidationService(
            guild_id=GUILD,
            user_repo=user_repo,
            price_repo=price_repo,
            fund_repo=fund_repo,
            cooldown_repo=cooldown_repo,
            lock_manager=lock_manager,
            settings=settings,
            trading_service=trading,
            unit_of_work=unit_of_work,  # type: ignore[arg-type]
        )
E       TypeError: LiquidationService.__init__() got an unexpected keyword argument 'unit_of_work'

tests/application/test_liquidation_service.py:151: TypeError
========================= 1 failed, 1 warning in 0.04s =========================
```

This is the precise contract drift the issue #95 fix closes: the test asks
the service for a `unit_of_work` injection seam, the current ctor refuses,
and the cover-path writes can therefore not be rolled back together.

## Next steps

1. GREEN AC1 — add `unit_of_work: IUnitOfWork | None = None` (kw-only) to
   `LiquidationService.__init__` and store as `self._uow` with
   `NullUnitOfWork()` default. Mirror `TradingService.__init__` (lines
   121-147 of `src/friendex/application/trading_service.py`).
2. GREEN AC2 — wrap the `cover_forced` call site at
   `src/friendex/application/liquidation_service.py:179` in
   `async with self._uow.transaction():`. Re-run the RED test — expect green.
3. AC3 — strip the three "tracked in issue #95" docstring paragraphs:
   - `LiquidationService._maybe_liquidate` (lines 146-157).
   - `TradingService._cover_internal` ("UoW envelope responsibility …" para).
   - `TradingService.cover_forced` (parenthetical reference, ~line 717).
4. Container wiring — pass `unit_of_work=self._unit_of_work` into the
   `LiquidationService(...)` call in
   `src/friendex/adapters/container.py::_make_liquidation_factory` (line 458).
5. Full gate: `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`,
   `uv run mypy src/friendex`.

## Open questions / risks

- None new. Continue.

## References

- Issues: #95
- RED test: `tests/application/test_liquidation_service.py` —
  `test_liquidation_rolls_back_on_mid_helper_failure` plus
  `_ExplodingPriceRepo` decorator + extended `_make_services`.
- Previous baton: [000](./000-2026-06-05-uow-wrap-liquidation-kickoff.md)
