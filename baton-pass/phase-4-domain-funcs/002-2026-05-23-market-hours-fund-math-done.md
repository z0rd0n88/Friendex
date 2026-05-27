# Pass-Baton: Phase 4 sub-unit 4b (market_hours + fund_math) complete

**Date:** 2026-05-23
**Scope:** phase-4-domain-funcs
**Branch:** feat/br-2026-05-23-p4p5/phase-4
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-23-p4p5
**HEAD:** 9aa3e54 feat(phase-4): price engine + activity domain functions

## Where things stand

Sub-unit **4b** of Phase 4 is **done and green** — the two remaining domain
pure-function modules plus their tests are implemented, fully tested, and pass
every scoped gate (ruff, ruff format, mypy, coverage). With 4a already landed,
**all four Phase 4 domain modules now exist.** Files for 4b are currently
**untracked** (not committed — the manager owns all git/PR actions).

Files created (all inside the worktree):
- `src/friendex/domain/market_hours.py`
- `src/friendex/domain/fund_math.py`
- `tests/domain/test_market_hours.py`
- `tests/domain/test_fund_math.py`

The shared `tests/domain/conftest.py` (from 4a) was reused, not modified.

## Type policy applied (Decimal-at-boundary — corrects the stale plan doc)

The plan doc's `float` money signatures are stale; the signed-off
Decimal-at-boundary policy was applied:
- `compute_apy_accrual(balance: Decimal, apy: float, period) -> Decimal`:
  annual = `balance * apy`; monthly = `balance * apy / 12`; quantised to
  `Decimal('0.01')` with `ROUND_HALF_EVEN`. `apy` (a rate) stays `float`,
  converted to Decimal via `str()` to avoid IEEE-754 noise.
- `compute_effective_apy(base_apy: float, penalty, now) -> float`: returns a
  **float** rate. No penalty / expired penalty (`penalty_until <= now`) →
  `base_apy`; active penalty → `max(0.0, base_apy - float(penalty.penalty_apr))`
  (Decimal penalty_apr converted to float at the combine point, floored at 0).
- `compute_net_worth(account, prices, fund) -> Decimal`: quantised to cents.
- No inputs are mutated anywhere (verified by no-mutation tests).

## Net-worth valuation decision (the modelling "gap")

`calculate_net_worth` is **referenced but never defined** in
`docs/spec/original-skeleton.md` (it's an incomplete external in the spec), so
the formula was derived from the task contract + the actual model fields:

```
net_worth = cash_balance
  + sum over longs  (shares * prices[target].current)
  + sum over shorts (locked_cash + locked_fund - shares * prices[target].current)
  + fund.investors.get(account.user_id, 0)   # only when fund is supplied
```

- A **short** contributes its locked collateral (deducted from cash when opened,
  released on cover) minus the current buy-back cost = collateral + unrealised
  short PnL. This matches the original's short mechanics (`locked_cash` +
  `locked_fund` on open, proportional release on cover; spec lines ~1175-1282).
- A position whose target has **no matching `Stock` in `prices`** contributes
  nothing for its price-valued component (defensive against missing market
  data) — covered by explicit tests for both long and short.
- Fund stake is read from `HedgeFund.investors[account.user_id]`; a fund the
  account is not invested in adds nothing.

## RED evidence per criterion (TDD — test-first, captured before impl)

- Ran `pytest tests/domain/test_market_hours.py tests/domain/test_fund_math.py`
  **before** the modules existed →
  `ModuleNotFoundError: No module named 'friendex.domain.market_hours'` and
  `... 'friendex.domain.fund_math'` (collection errors, 2 errors, all tests
  un-runnable). Implemented both modules → green.
- market_hours coverage (criterion 1): tests assert Sunday closed; Sat/Mon open
  in window; overnight wrap (02:00 open, 05:00 closed); exact open boundary
  inclusive (06:30 open, 06:29 closed) and close boundary exclusive (04:30
  closed, 04:29 open); `sunday_buy_allowed` flips Sunday (and still respects the
  window); plus a same-day (`open < close`) branch.
- fund_math coverage (criterion 2): monthly vs annual arithmetic
  (1200@0.15 → 15.00 / 180.00); effective APY for {no penalty, expired ignored,
  expiring-exactly-now ignored, active subtracted → 0.10, over-penalty floored
  to 0.0}; net worth {cash-only, zero-position, long-at-current, mixed
  long+short, fund stake included / ignored, missing-price branches};
  quantisation + no-mutation assertions throughout.

## Verification gate output (all green)

- `ruff check src/friendex/domain tests/domain` → **All checks passed!**
- `ruff format --check src/friendex/domain tests/domain` → **15 files already formatted**
- `mypy src/friendex/domain` → **Success: no issues found in 7 source files**
- Scoped coverage (module-import form):
  `pytest test_market_hours.py test_fund_math.py
  --cov=friendex.domain.market_hours --cov=friendex.domain.fund_math` →
  **market_hours.py 100%, fund_math.py 100%, 53 passed** (bar is ≥95%).
- Migration-plan gate over the full domain suite
  (`pytest tests/domain/ --cov=src/friendex/domain --cov-fail-under=95`) →
  **100.00% total, 217 passed** (no 4a regression).
- Full repo suite `pytest -q` → **244 passed** (no regressions).

## Next steps

1. Manager: commit these 4 files (e.g. `feat(domain): market hours predicates`
   + `feat(domain): hedge-fund & net-worth math`, or per repo's per-file
   commit guidance). Phase 4's four domain modules are then code-complete.
2. After commit, Phase 4 verification gate from `docs/04-migration-plan.md` can
   run clean over the whole `tests/domain/` tree.
3. Proceed to **Phase 5** (Persistence: ORM & Alembic baseline) per
   `docs/04-migration-plan.md`.

## Open questions / risks (deferred — do NOT block on these)

- **`sunday_buy_allowed` semantics:** when set, the toggle treats Sunday like
  any weekday for the *time-of-day* window (it does not force-open Sunday
  regardless of hour). This matches the original's "buy allowed on Sunday
  intentionally" comment (spec line ~1041) where the Sunday carve-out is a
  buy-only relaxation; the application layer decides when to pass it.
- **Net-worth formula is contract-derived, not spec-verbatim** (spec's
  `calculate_net_worth` is undefined). If a later phase finds the original
  valued shorts differently (e.g. ignoring released collateral), revisit
  `compute_net_worth` — the per-component breakdown is documented in the module
  docstring to make that adjustment localised.
- **APY period base:** monthly accrual is `annual/12` (linear split). The
  original labels `HEDGE_FUND_BASE_APY = 0.15` as "15% nominal monthly" in a
  comment (spec line ~68) but the Settings field and task contract treat it as
  an annual-style rate with a monthly divisor; if compounding/true-monthly is
  wanted later, only `compute_apy_accrual` changes.

## References

- Issue: #2 (master tracking — Phase 4 box)
- Predecessor: `baton-pass/phase-4-domain-funcs/001-2026-05-23-price-engine-activity-done.md`
- Plan: `docs/04-migration-plan.md` §Phase 4 (lines ~286-292)
- Behavioral spec: `docs/spec/original-skeleton.md` §market hours (110-132),
  §HEDGE FUND PENALTY & EVENTS (588-605), short mechanics (1175-1282)
- Code: `src/friendex/domain/market_hours.py`, `src/friendex/domain/fund_math.py`
