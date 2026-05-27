# Pass-Baton: Sub-unit 6d — SqlPriceRepository + SqlFundRepository

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** 29fe857 chore(phase-6): 6c review CLEAN + phase-exit digest

## Where things stand

Sub-unit 6d is **code-complete and gate-green** (changes not yet committed). Two
SQLAlchemy-backed repositories — the price/stock aggregate and the hedge-fund
aggregate — are implemented and proven end-to-end against an in-memory SQLite
engine with FK enforcement ON. Both conform to their Protocols **structurally,
no inheritance** (mypy gates the typed `conforming: IPriceRepo = repo` /
`conforming: IFundRepo = repo` assignments in the tests; mypy passes clean,
including on the test files).

### `src/friendex/adapters/persistence/price_repo.py` → `SqlPriceRepository`
Implements every `IPriceRepo` method: `get`, `upsert`, `delete`, `list_all`,
`append_history`, `get_history(*, since=None)`, `prune_history_older_than`.
Design choices honoring AC + prior digests:
- **History is append-only.** `upsert` only `merge`s the scalar `StockORM` row;
  it never rewrites history (the two live in separate tables, per the contract).
  History grows via `append_history` and reads back oldest-first (ordered by
  `recorded_at`) in `get_history`.
- **`prune_history_older_than` is a single bulk `DELETE WHERE recorded_at <
  :cutoff`** across every guild (AC1) — no load-then-delete loop. Returns the
  affected `rowcount`. Boundary is exclusive of the cutoff (rows *strictly*
  older are deleted; an on-cutoff row is retained — covered by a test).
  rowcount typed via `cast("CursorResult[object]", ...)` because
  `AsyncSession.execute` returns `Result[Any]` to mypy (cast is non-redundant
  under `warn_redundant_casts`).
- **`delete` does NO hand-rolled child cleanup** — single parent `DELETE`,
  relying on DB-level `ON DELETE CASCADE` (ADR-0002 / 6a). Cascade-to-history
  proven by `test_delete_cascades_to_history`.

### `src/friendex/adapters/persistence/fund_repo.py` → `SqlFundRepository`
Implements every `IFundRepo` method: `get`, `upsert`, `delete`, `list_all`,
`ensure_events_wallet`. Design choices:
- **`upsert` = whole-aggregate delete-then-insert** in one transaction (`merge`
  the `HedgeFundORM` scalar row, delete owned `FundInvestorORM` rows, re-insert
  from the `investors` dict). Matches the 6c aggregate pattern; investor
  add→remove round-trips wholesale (AC3, covered).
- **`ensure_events_wallet` is idempotent** (AC2): get-or-create for the per-guild
  `events_wallet` pseudo-fund — returns the existing wallet untouched (no balance
  mutation) when present; otherwise creates an empty $0.00 wallet
  (`fund_id="events_wallet"`, `name="Events Wallet"`, `manager_id="0"`). Two
  calls yield exactly one wallet row (asserted via a COUNT in the test).
- **`delete`** relies on DB-level CASCADE to drop investors (proven non-vacuous
  the same way 6c proved the user cascade).

### N+1 avoided (carry-forward lesson from the 6c review, MEDIUM)
Both `list_all` collection loaders use a **single grouped query** for children
(history grouped by `user_id`, investors grouped by `fund_id` via `defaultdict`
in `_load_history_by_user` / `_load_investors_by_fund`) instead of one child
query per parent — so listing N stocks/funds is 2 queries, not N+1.

### Re-exports
Added `SqlFundRepository` + `SqlPriceRepository` to
`src/friendex/adapters/persistence/__init__.py` `__all__` (alphabetised,
following the existing re-export pattern).

## TDD trail (RED → GREEN), per AC

Wrote both test files first. Initial run captured **RED** (actual output):
```
ModuleNotFoundError: No module named 'friendex.adapters.persistence.price_repo'
ModuleNotFoundError: No module named 'friendex.adapters.persistence.fund_repo'
```
Implemented both repos → **GREEN**: 24/24 new tests pass (13 price + 11 fund).
Per-AC coverage in tests:
- **AC1 (price):** scalar round trip with Decimal scale (`as_tuple().exponent`)
  + UTC `tzinfo`; structural conformance; cascade-to-history delete.
- **AC2 (fund):** investor round trip with Decimal scale; structural
  conformance; `ensure_events_wallet` create / idempotent (one wallet, balance
  preserved) / existing-balance-preserved; cascade-to-investors delete.
- **AC3:** `append_history`→`get_history` returns appended rows oldest-first
  (appended out of order to prove ordering is by `recorded_at`); `get_history
  (since=)` window; `prune_history_older_than` retains ONLY the window
  (strictly-older gone, on-cutoff + newer kept) and is cross-guild; investor
  add-then-remove round-trip.

## Verification (actual output)

`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/selfcheck-6d/` →
```
PASS pytest
PASS ruff-check
PASS ruff-format
PASS mypy
----
GATE: PASS
```
Full suite: **322 passed**. New-file coverage: `price_repo.py` 100%,
`fund_repo.py` 100%, persistence package total 96% (> the plan's 85% gate).
`mypy` also clean on the two new test files (structural conformance genuinely
type-checks).

Current blocking state: **none — ready for review.** Changes are staged in the
worktree (uncommitted, per containment). No new dependencies.

## Next steps

1. Review sub-unit 6d (the implementer→review→fix baton-runner step).
2. Then proceed to 6e (penalty/cooldown/system-state repos:
   `IPenaltyRepo` / `ITradeCooldownRepo` / `ISystemStateRepo` — note the
   split `upsert(dto)` signatures and the `purge_expired` / `get`-excludes-
   expired TTL semantics from the 6b digest).
3. 6f is the JSON→SQLite migrator. NOT touched here (out of scope).
4. When committing, follow the plan's commit boundary: one
   `feat(persistence): SqlPriceRepository + SqlFundRepository` commit.

## Open questions / risks

- None blocking. The events-wallet sentinels (`manager_id="0"`,
  `name="Events Wallet"`, $0.00) are reasonable defaults; if Phase 7+ services
  expect different metadata for the pseudo-fund, adjust the constants in
  `fund_repo.py` (they are module-level, not magic literals).

## References

- Code: `src/friendex/adapters/persistence/price_repo.py`,
  `src/friendex/adapters/persistence/fund_repo.py`,
  `src/friendex/adapters/persistence/__init__.py`
- Tests: `tests/adapters/persistence/test_price_repo.py`,
  `tests/adapters/persistence/test_fund_repo.py`
- Contract: `src/friendex/application/interfaces.py` (`IPriceRepo`, `IFundRepo`)
- Prior batons: [006](./006-2026-05-24-6c-user-repo-review.md);
  digests `digest-phase-6a.md` (FK/CASCADE), `digest-phase-6b.md` (Protocol
  surfaces), `digest-phase-6c.md` (repo construction + mapping + N+1 lesson)
- Plan: `docs/04-migration-plan.md` §Phase 6
- Issue: #2 (phase status); ADR-0001 (per-guild markets); ADR-0002 (SQLite FK)
