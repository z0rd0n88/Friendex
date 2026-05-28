# Phase 17b — Exit Digest

Branch `feat/phase-17b-invest` · HEAD `865c921` · Gate PASS via
`gate-phase-17b-iter-1/`. Review CLEAN; M1-M4 mutations failed matched
tests under revert; one LOW finding (M5 in-place dict-aliasing test gap)
and three INFO carry-forwards.

## Public surface added (behaviours, not signatures)

1. **`FundService.invest(investor_id, fund_id, amount)`** is functional.
   `amount<=0` / missing fund / self-invest → `InvalidAmount` (each
   with the exact message strings: `"amount must be positive"`,
   `"fund does not exist"`, `"cannot invest in own fund"`).
   Missing/insufficient investor cash → `InsufficientFunds(need=quantised,
   have=account_cash_or_zero)`. Atomic: debits
   `account.cash_balance`, credits `fund.cash_balance`, increments
   `fund.investors[investor_id]` (initialising to `0` when absent). Body
   sits inside one `self._locks.locked(investor_key, fund_key)` call
   (non-reentrant — never nest); every Decimal write goes through
   `_quantise`. The investors dict is cloned before mutation
   (`new_investors = dict(fund.investors)`).
2. **`FundService.withdraw` caps at the manager's own share.**
   `manager_balance = fund.cash_balance - sum(investors.values())`;
   over-cap raises `FundInsufficientBalance(need=quantised_amount,
   have=manager_balance)` — note `have=` is now the manager share, not
   the gross fund balance.
3. **`FundService.accrue_apy` per-stake split.** Each fund's
   `manager_balance` and every investor stake earn their own
   `compute_apy_accrual(...)` independently; resulting `cash_balance =
   manager_balance + manager_accrual + sum(new_investor_stakes)`. Each
   investor stake grows by its own accrual. Zero / sub-cent total
   accrual → continue without upsert (idempotent no-op). Period is
   read from `self._settings.hedge_fund_base_apy_period` (Phase 17a pin).

## Decisions 17c MUST honour

1. **Cog ack carry-forward (REQUIRED).** `FundGroup.invest` in
   `fund_cog.py:237-265` does **not** call
   `interaction.response.send_message(...)`. Discord requires an
   acknowledgement within 3 s; 17c MUST add the public confirmation
   embed (mirror `/fund withdraw` / `/fund send_events`).
2. **Smoke-test STEPS pin (REQUIRED).**
   `scripts/smoke_test_commands.py` `STEPS[id=18]` still describes
   "deferred to Phase 17" + "NotImplementedError". 17c MUST rewrite
   both `name` and `expected` to describe the live invest path.
3. **Smoke-test pin (REQUIRED).**
   `tests/scripts/test_smoke_test_commands.py::test_fund_invest_step_notes_not_implemented_error`
   MUST be deleted (or replaced + renamed) once STEP id=18 is rewritten.
4. **Runbook update (REQUIRED).** `docs/runbook-smoke-test.md` follows
   STEP id=18; refresh once the script lands.
5. **Cog test pin (OPTIONAL).**
   `tests/adapters/discord_bot/cogs/test_fund_cog.py::test_fund_invest_propagates_not_implemented_uncaught`
   asserts a stale `NotImplementedError` re-raise via a mocked
   service — retire / replace with a positive happy-path assertion.
6. **Opt-in NOT checked on invest** — design decision: investing in a
   fund is a financial transaction, not a trade; `_check_opt_in` is a
   per-stock toggle. Do not introduce an opt-in gate on `invest` in 17c
   without an explicit spec change.
7. **Continuity invariants preserved.** Composite lock keys
   `f"{guild_id}:{user_or_fund_id}"`, non-reentrant `locked()` single
   call, reads-inside-lock RMW, every Decimal write through `_quantise`,
   no `discord` import in `domain/`/`application/`, no `try/except` in
   the cog. 17c MUST not regress any of these.

## Open / informational

- **LOW (test gap).** No test asserts `fund.investors` dict identity
  freshness on `invest` — defensive `dict(...)` clone is unenforced
  (mutation M5 stayed GREEN). Cheap fix: seed fund with a kept-on-the-test
  `investors_dict` ref, run `invest`, assert it stays empty. Defer to
  17c if not urgent.
- **No new deps** (zero `pyproject.toml`/`uv.lock` diff). Keep it.

Coverage (gate-phase-17b-iter-1/): `fund_service.py` 93%, application
layer 93% — both above gates.
