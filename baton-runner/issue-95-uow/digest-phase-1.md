# Phase 1 exit digest — issue-95-uow

**Scope:** Close GitHub issue #95 — wrap `LiquidationService._maybe_liquidate`'s
`cover_forced` call in a UoW envelope so a mid-helper persistence failure
during liquidation rolls every prior write back atomically.

**Verdict:** CLEAN (gate green, all 3 ACs verified load-bearing).
**HEAD:** `6ed91b3` on `fix/liquidation-uow` (branched off `origin/main` at `3d8683e`).
**Gate:** `scripts/gate.sh baton-runner/issue-95-uow/gate-phase-1-iter-1/` → PASS
(1061 pytest / ruff-check / ruff-format / mypy 75 files).

## Public surface added

- `friendex.application.liquidation_service.LiquidationService.__init__`
  gains a new kw-only kwarg:
  `unit_of_work: IUnitOfWork | None = None` (default `NullUnitOfWork()`).
  Stored as `self._uow: IUnitOfWork`. Mirrors `TradingService.__init__`
  exactly (same kwarg name, same kw-only position, same default).
- No new module-level functions, classes, or protocols. The
  `IUnitOfWork` / `NullUnitOfWork` types were already part of
  `friendex.application.unit_of_work` since Phase 8.

## Conventions / decisions a future phase must honour

- `LiquidationService` is now in the same atomicity-envelope category as
  `TradingService` and `FundService`: any future service that orchestrates
  multiple repo writes inside a single critical section MUST take the
  same kw-only `unit_of_work` seam and default it to `NullUnitOfWork`.
- The container threads ONE shared `SqlUnitOfWork` instance through every
  per-guild factory (`_make_trading_factory`, `_make_fund_factory`, and
  now `_make_liquidation_factory`). New per-guild service factories that
  need atomicity must pass `unit_of_work=self._unit_of_work` — do NOT
  construct a fresh `SqlUnitOfWork` per factory call.
- The `cover_forced` contract is documented but not runtime-enforced:
  callers MUST hold the actor+target lock AND open a UoW envelope
  before calling it. The two known call sites (`cover()` and
  `LiquidationService._maybe_liquidate`) both honour this. Any future
  third call site must do the same.
- Lock-first / transaction-second ordering: the `async with
  self._uow.transaction():` block is always nested INSIDE the
  `self._locks.locked(...)` block. Don't invert this — re-reads must be
  inside the lock so the transaction operates on a stable snapshot.
- `_cover_internal` itself NEVER opens its own UoW. It is callable from
  either `cover()` or `cover_forced`; both wrap the call. The internal
  helper assumes a transaction is in flight via `contextvars` (per the
  `SqlUnitOfWork` design comment at `container.py:189-192`).
- Rollback regression-test pattern: thread `FakeUnitOfWork` into BOTH
  the wrapping service and any inner service it calls, wrap any
  persistence port with the `_ExplodingPriceRepo`-style decorator from
  `tests/application/test_trading_service_atomicity.py:300`, assert
  `uow.rollbacks == 1` PLUS the explicit "money unchanged" pin.

## Non-blocking carry-forwards (optional polish)

- MEDIUM (cosmetic): `trading_service.py:773` lists "cooldown set"
  among the writes inside `_cover_internal`; the cooldown set is
  actually emitted by `cover()` after the inner call (still inside the
  same envelope, so the rollback claim holds). Pre-fix docstring had
  the same phrasing — not a regression.
- LOW: `LiquidationService` class docstring could note the new UoW
  seam (one line). Module docstring is already detailed.
