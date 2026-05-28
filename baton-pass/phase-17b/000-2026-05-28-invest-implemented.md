# Pass-Baton: Phase 17b — `/fund invest` live + per-stake APY split

**Date:** 2026-05-28
**Scope:** phase-17b
**Branch:** feat/phase-17b-invest
**Worktree:** /home/user/Friendex/.claude/worktrees/br-2026-05-28-phase-17
**HEAD:** 74847d1 chore(phase-17a): review CLEAN + phase-exit digest (work uncommitted — manager owns git)

## Where things stand

Phase 17b acceptance criteria B1–B6 are implemented and the four-check gate
is green. `FundService.invest` is now functional, `FundService.withdraw`
caps at the manager balance share (investor principal untouchable), and
`FundService.accrue_apy` does a per-stake split across the manager share
and every investor stake (threading the Phase 17a
`hedge_fund_base_apy_period` setting). The `/fund invest` cog docstring
and slash description were rewritten to describe live semantics; no cog
Python logic changed (the cog still lets DomainError propagate to the
Phase 13 tree-wide handler). Work is uncommitted in the worktree — the
manager handles every git mutation.

Reserved-for-17c items (left untouched per the Phase 17a digest):
`scripts/smoke_test_commands.py` STEPS[id=18] and
`tests/scripts/test_smoke_test_commands.py::test_fund_invest_step_notes_not_implemented_error`.
The cog test `test_fund_invest_propagates_not_implemented_uncaught` is
still asserting the cog re-raises `NotImplementedError` from a mocked
service — it still passes (the cog body is untouched), and the
NotImplementedError reference there is decoupled from the real service.
17c will be the natural place to retire that mock-driven assertion in
favour of a positive happy-path cog test if desired.

## RED-first captures

All B1 sub-clauses + B2 + B3 had RED tests already present in the
worktree from the seeded Phase-17b test additions; the failures captured
before implementation (single `uv run pytest -k "invest or withdraw_caps
or accrue_apy_splits"` run):

```
FAILED test_invest_zero_or_negative_amount_raises_invalid_amount
    NotImplementedError: FundService.invest is scaffolded per §Open-Q5; ...
FAILED test_invest_missing_fund_raises_invalid_amount
    NotImplementedError: FundService.invest is scaffolded per §Open-Q5; ...
FAILED test_invest_self_invest_is_blocked
    NotImplementedError: FundService.invest is scaffolded per §Open-Q5; ...
FAILED test_invest_missing_investor_account_raises_insufficient_funds
    NotImplementedError: FundService.invest is scaffolded per §Open-Q5; ...
FAILED test_invest_insufficient_cash_raises_insufficient_funds
    NotImplementedError: FundService.invest is scaffolded per §Open-Q5; ...
FAILED test_invest_happy_path_mutates_cash_and_investors
    NotImplementedError: FundService.invest is scaffolded per §Open-Q5; ...
FAILED test_invest_second_call_accumulates_stake
    NotImplementedError: FundService.invest is scaffolded per §Open-Q5; ...
FAILED test_withdraw_caps_at_manager_balance_when_investors_present
    DID NOT RAISE <class 'friendex.domain.errors.FundInsufficientBalance'>
FAILED test_accrue_apy_splits_single_investor_at_annual_period
    {'user-2': Decimal('200.00')} != {'user-2': Decimal('230.00')}
FAILED test_accrue_apy_splits_two_investors_at_annual_period
    {'investor-A': $1000, 'investor-B': $500} != {A:$1150, B:$575}
10 failed, 2 passed, 17 deselected
```

GREEN after implementation: `28 passed` (tests/application/test_fund_service.py)
and `822 passed` for the full suite.

## Verification gate

`bash scripts/gate.sh baton-runner/br-2026-05-28-phase-17/gate-phase-17b-work/`:

```
=== gate: pytest ===     PASS pytest
=== gate: ruff-check === PASS ruff-check
=== gate: ruff-format ===PASS ruff-format
=== gate: mypy ===       PASS mypy
----
GATE: PASS
```

- Touched file coverage: `src/friendex/application/fund_service.py` at **93%**
  (131 stmts / 7 missed) — above the 90% per-file gate. Missing lines are
  pre-existing defensive branches (events-wallet skip, etc.) — not new code.
- Application-layer coverage: **93%** (1184 stmts / 68 missed) — above the
  85% layer gate.
- Deps: `git diff origin/main -- pyproject.toml uv.lock` → zero output.
- Files modified (allow-list compliance):
  * `src/friendex/application/fund_service.py`
  * `src/friendex/adapters/discord_bot/cogs/fund_cog.py`
  * `tests/application/test_fund_service.py` (removed obsolete
    `test_e7_invest_raises_not_implemented`; E7 docstring rephrased)
- `tests/adapters/discord_bot/cogs/test_fund_cog.py` left untouched — the
  existing `test_fund_invest_propagates_not_implemented_uncaught` still
  passes (mock-driven; the cog body is unchanged).

## Decisions baked in for 17c

1. **Invest uses `InsufficientFunds`** (not a new error class). A missing
   investor account is treated the same as zero cash — both raise
   `InsufficientFunds(need=quantised_amount, have=Decimal("0.00"))`.
2. **Self-invest is `InvalidAmount("cannot invest in own fund")`** — Phase
   17b §Q2 pinned in a one-line comment at `fund_service.py` `invest()`.
3. **Withdraw `have=` field is now `manager_balance`**, not
   `fund.cash_balance`. Any 17c log / embed that surfaces the
   `FundInsufficientBalance.have` payload will see the *manager share*,
   not the gross fund balance.
4. **Accrue split** rebuilds `cash_balance` from `manager_balance +
   manager_accrual + sum(new_investors.values())` to avoid float drift
   from independent quantisations. Idempotency skip is now
   `manager_accrual + sum(investor_accruals) < _CENT`.
5. **Cog body unchanged.** `/fund invest` still does
   `await fund_service.invest(invoker, target, Decimal(str(amount)))` with
   no `try/except`. The Phase-13 tree-wide handler renders every
   `DomainError`.

## Next steps (for 17c)

1. Refresh `scripts/smoke_test_commands.py` STEP id=18 — the `/fund invest`
   step's `expected` block should describe a successful invest path (or
   the §Q2 self-invest rejection if the smoke script is invoking against
   the bot owner). Drop the "NotImplementedError" wording.
2. Update `tests/scripts/test_smoke_test_commands.py::test_fund_invest_step_notes_not_implemented_error`
   to reflect whatever the new STEP id=18 asserts (rename it, too — the
   `_notes_not_implemented_error` slug is now stale).
3. Consider retiring `test_fund_invest_propagates_not_implemented_uncaught`
   in `tests/adapters/discord_bot/cogs/test_fund_cog.py` in favour of a
   positive happy-path cog test (mocked `fund_service.invest` returns
   `None`, asserts the cog calls `interaction.response.send_message` with
   a public confirmation embed if one is added). Optional — the existing
   mock-driven assertion still passes.
4. Consider adding a public confirmation embed to `/fund invest` (mirrors
   `/fund withdraw` and `/fund send_events`, which both render an embed).
   The Phase 17b spec didn't require it; 17c is the natural place if the
   product team wants symmetric UX.

## Open questions / risks

- The `/fund invest` cog method returns silently on success — no
  `interaction.response.send_message` call. Discord requires every
  interaction to be acknowledged within 3 s or the user sees "this
  interaction failed". The Phase 17b spec didn't list a confirmation
  embed and the work-unit explicitly forbade Python changes to the cog
  body, so this is **carried forward to 17c**. If the smoke harness runs
  the live bot path it will surface this; flag for 17c verification.

## References

- Spec: `docs/04-migration-plan.md` §Phase 17 (lines 858-890)
- Phase 17a digest: `baton-runner/br-2026-05-28-phase-17/digest-phase-17a.md`
- Phase 16 digest (17c carry-forwards): `baton-runner/br-2026-05-27-phase-16/digest-phase-16.md`
- Issue: #2 (phase tracker)
- Gate log: `baton-runner/br-2026-05-28-phase-17/gate-phase-17b-work/`
- Code:
  * `src/friendex/application/fund_service.py` — `invest`, `withdraw`,
    `accrue_apy`
  * `src/friendex/adapters/discord_bot/cogs/fund_cog.py` — `/fund invest`
    docstring + slash description
  * `tests/application/test_fund_service.py` — Phase 17b B1-B3 tests at
    the bottom of the file
