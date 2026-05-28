# Pass-Baton: Phase 17a config toggles + wiring implemented

**Date:** 2026-05-28
**Scope:** phase-17a
**Branch:** feat/phase-17a-toggles
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-28-phase-17
**HEAD:** a205e40 refactor: rename pass-baton skill and directory to baton-pass (#70)

## Where things stand

Phase 17a (the toggles + wiring slice of the Phase 17 hardening epic — see
`docs/04-migration-plan.md:858-890`) is code-complete on top of
`origin/main@a205e40`. All four acceptance criteria green; full gate
(`scripts/gate.sh baton-runner/br-2026-05-28-phase-17/gate-phase-17a-work/`)
reports `GATE: PASS`. No commits made (manager owns git).

What changed (7 files, all inside the locked allow-list):

- `src/friendex/adapters/config.py` — added `hedge_fund_base_apy_period:
  Literal["monthly", "annual"] = "monthly"` in the Hedge-fund section with an
  Open-Q8 docstring. (`sunday_buy_allowed` and `opt_out_blocks_trading` were
  already present from an earlier foresight pass — only the third field was
  missing.)
- `src/friendex/application/trading_service.py`
  - `buy()` line 334 now passes `allow_sunday=self._settings.sunday_buy_allowed`
    (was hard-coded `True`). `/sell`, `/short`, `/cover` still pass
    `allow_sunday=False`.
  - `_check_opt_in` converted from `@staticmethod` to an instance method that
    short-circuits when `self._settings.opt_out_blocks_trading is False`. The
    `OptedOut` raise is unchanged at default (`True`).
- `src/friendex/application/fund_service.py` — `accrue_apy` now passes
  `period=self._settings.hedge_fund_base_apy_period` to `compute_apy_accrual`
  (was hard-coded `"monthly"`).
- `.env.example` — documented `HEDGE_FUND_BASE_APY_PERIOD`, `SUNDAY_BUY_ALLOWED`,
  `OPT_OUT_BLOCKS_TRADING` with Open-Q citations.
- `tests/adapters/test_config.py` — no edit needed (Phase 17a env-override +
  default-shape tests for the three toggles were already present from the
  foresight pass; the new `hedge_fund_base_apy_period` Literal field made them
  all pass).
- `tests/application/test_trading_service.py` — 3 new Phase 17a tests
  (`test_buy_rejected_on_sunday_when_sunday_buy_allowed_is_false`,
  `test_check_opt_in_is_noop_when_opt_out_blocks_trading_is_false`,
  `test_check_opt_in_still_raises_when_toggle_default_true`).
- `tests/application/test_fund_service.py` — 2 new Phase 17a tests
  (`test_accrue_apy_uses_annual_period_when_setting_is_annual`,
  `test_accrue_apy_uses_monthly_period_by_default`).

## TDD RED captures (recorded before each implementation step)

**A1 RED** (`uv run pytest tests/adapters/test_config.py -x`):
```
E    AttributeError: 'Settings' object has no attribute 'hedge_fund_base_apy_period'
FAILED tests/adapters/test_config.py::test_defaults_match_target_architecture
```
Resolved by adding the Literal field to `Settings` (see config.py diff).

**A2 RED** (`uv run pytest ::test_buy_rejected_on_sunday_when_sunday_buy_allowed_is_false`):
The test wrote `default_settings.model_copy(update={"sunday_buy_allowed": False})`
then froze time to `SUNDAY_OPEN` and expected `MarketClosed`. Before the fix
the buy succeeded (no exception) because `buy()` hard-coded `allow_sunday=True`.
Resolved by switching that call-site to `self._settings.sunday_buy_allowed`.

**A3 RED** (`uv run pytest ::test_check_opt_in_is_noop_when_opt_out_blocks_trading_is_false`):
```
src/friendex/application/trading_service.py:315: OptedOut
E    friendex.domain.errors.OptedOut: <@target-1> has opted out of trading.
```
Resolved by converting `_check_opt_in` from `@staticmethod` to an instance
method that early-returns when `self._settings.opt_out_blocks_trading is False`.

**A4 RED** (`uv run pytest ::test_accrue_apy_uses_annual_period_when_setting_is_annual`):
```
E    AssertionError: assert Decimal('101.25') == Decimal('115.00')
```
Resolved by passing `period=self._settings.hedge_fund_base_apy_period` to
`compute_apy_accrual` in `accrue_apy`.

## Verification gate evidence

`bash scripts/gate.sh baton-runner/br-2026-05-28-phase-17/gate-phase-17a-work/`:
```
=== gate: pytest ($*: uv run pytest) ===
PASS pytest
=== gate: ruff-check ($*: uv run ruff check src tests alembic) ===
PASS ruff-check
=== gate: ruff-format ($*: uv run ruff format --check src tests alembic) ===
PASS ruff-format
=== gate: mypy ($*: uv run mypy src/friendex) ===
PASS mypy
----
GATE: PASS
```

Per-file coverage (≥ 85% gate):
- `src/friendex/adapters/config.py`: **100%** (92 stmts / 14 branches)
- `src/friendex/application/trading_service.py`: **93%** (270 stmts / 80 branches)
- `src/friendex/application/fund_service.py`: **91%** (102 stmts / 26 branches)

Dependency invariant: `git diff origin/main -- pyproject.toml uv.lock` → 0
bytes (byte-identical). No new runtime dependencies.

Containment: `git diff --name-only origin/main` lists exactly the 7
allow-listed files (config.py, trading_service.py, fund_service.py,
.env.example, test_config.py, test_trading_service.py, test_fund_service.py).

## Next steps

1. Manager: review the diff, commit (suggested message:
   `feat(config): wire Open-Q2/Q3/Q8 toggles into TradingService and FundService`),
   open PR against `main` referencing GitHub issue #2.
2. Future Phase 17 work-units (NOT in this slice): `/fund invest` filled in
   (Open-Q5), APY distribution to investors, intro distribution mechanism
   (Open-Q10) — see `docs/04-migration-plan.md:858-890`.

## Open questions / risks

- `_check_opt_in` signature changed from `@staticmethod` to instance method.
  All in-tree callers are `self._check_opt_in(target)` so the change is
  source-compatible; verified no external callers under `src/` or `tests/`.
- The Phase 17a config defaults preserve the Phase-8e/8c historic behaviour —
  flipping any toggle is a deliberate operator action, never automatic.

## References

- Spec: `docs/04-migration-plan.md` §Phase 17 lines 858-890
- Open-Qs: `docs/02-target-architecture.md` §Open-Questions Q2, Q3, Q8
- Code:
  - `src/friendex/adapters/config.py:81-100` (toggle fields)
  - `src/friendex/application/trading_service.py:312-322` (`_check_opt_in`)
  - `src/friendex/application/trading_service.py:334` (`buy` sunday wiring)
  - `src/friendex/application/fund_service.py:339-343` (`accrue_apy` period wiring)
- Gate logs: `baton-runner/br-2026-05-28-phase-17/gate-phase-17a-work/`
- Issue: #2 (phase status)
