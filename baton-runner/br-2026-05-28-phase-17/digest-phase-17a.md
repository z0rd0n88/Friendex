# Phase 17a — Exit Digest

Branch `feat/phase-17a-toggles` · HEAD `5c294fe` · Gate PASS.

## Public surface added

Three new `friendex.adapters.config.Settings` fields. Defaults preserve
historic behaviour; flipping any is a deliberate operator action.

| Field | Type | Default | Open-Q | Read by |
|---|---|---|---|---|
| `sunday_buy_allowed` | `bool` | `True` | Q2 | `TradingService.buy()` |
| `opt_out_blocks_trading` | `bool` | `True` | Q3 | `TradingService._check_opt_in()` |
| `hedge_fund_base_apy_period` | `Literal["monthly","annual"]` | `"monthly"` | Q8 | `FundService.accrue_apy()` |

`.env.example` documents `SUNDAY_BUY_ALLOWED`, `OPT_OUT_BLOCKS_TRADING`,
`HEDGE_FUND_BASE_APY_PERIOD` with Open-Q citations.

## Decisions Phase 17b/17c MUST honour

1. **`FundService.accrue_apy` reads `settings.hedge_fund_base_apy_period`.**
   17b's per-investor APY-split MUST thread the same setting through to its
   `compute_apy_accrual` calls — never hard-code `"monthly"` again.
2. **`/buy` consults `settings.sunday_buy_allowed`** (sell/short/cover stay
   hard-coded `False`). 17b's `/fund invest` / `/fund withdraw` are NOT trade
   directions — decide market-hour gating explicitly, do not inherit silently.
3. **`_check_opt_in` is now an instance method** that early-returns when
   `opt_out_blocks_trading is False`. All four direction call-sites use
   `self._check_opt_in(target)`. 17b's invest path SHOULD reuse the same
   instance method, not duplicate the toggle check.
4. **Settings Literal pattern.** New cadence-style settings follow
   `Literal[...] = <default>` + Open-Q docstring; pydantic-settings v2
   rejects non-Literal env values (pinned in test_config.py).
5. **No new deps** in 17a (`pyproject.toml`/`uv.lock` byte-stable). Keep it.
6. **Phase 16 carry-forwards untouched.** `STEPS[id=18]` and
   `test_fund_invest_step_notes_not_implemented_error` belong to 17c.

Coverage (gate-phase-17a-iter-1/): config 100%, trading_service 93%,
fund_service 91% — all above the 85% per-file gate.
