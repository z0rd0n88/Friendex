# Pass-Baton: 6d review — SqlPriceRepository + SqlFundRepository (CLEAN)

**Date:** 2026-05-24
**Scope:** phase-6-repos
**Branch:** feat/phase-6-repos
**Worktree:** /home/alex/Friendex/.claude/worktrees/phase-6-repos
**HEAD:** c6610b2 feat(phase-6): SqlPriceRepository + SqlFundRepository

## Where things stand

Independent review of sub-unit **6d** (price + fund repositories) over the diff
`29fe857..c6610b2`. **VERDICT: CLEAN.** The deterministic gate is green, all four
acceptance criteria are met, the TDD evidence is real (the key prune-boundary
test fails under a `<=` reversion — verified by hand), there are no
CRITICAL/HIGH/MEDIUM findings, and no new dependencies. The two repos are ready
to proceed to sub-unit 6e. Current blocking state: **none.**

## Verification (actual output)

`scripts/gate.sh baton-runner/br-2026-05-24-phase-6/gate-phase-6d-iter-1/`:
```
PASS pytest
PASS ruff-check
PASS ruff-format
PASS mypy
----
GATE: PASS   (exit 0)
```
Full suite 322 passed; the 24 new repo tests pass in isolation
(`pytest test_price_repo.py test_fund_repo.py` → 24 passed). `mypy` also clean on
the two new test files explicitly (`Success: no issues found in 2 source files`)
— the `conforming: IPriceRepo = repo` / `conforming: IFundRepo = repo`
assignments genuinely type-check structural conformance (no ABC inheritance).

## AC verdicts

- **AC1 (price surface + bulk prune):** PASS. All 7 `IPriceRepo` methods present
  (`get`, `upsert`, `delete`, `list_all`, `append_history`, `get_history`,
  `prune_history_older_than`). `prune_history_older_than` is a **single bulk**
  `delete(PriceHistoryORM).where(recorded_at < cutoff)` returning `rowcount`
  (`price_repo.py:135-150`) — no load-then-delete loop. `delete` is a single
  parent DELETE relying on DB-level CASCADE (proven by
  `test_delete_cascades_to_history`).
- **AC2 (fund surface + idempotent wallet):** PASS. All 5 `IFundRepo` methods
  present. `ensure_events_wallet` (`fund_repo.py:115-133`) is get-then-create,
  but the create path goes through `upsert`'s `session.merge(...)` on a fixed PK
  `(guild_id, "events_wallet")` — so a repeat/concurrent call performs an UPDATE,
  never a duplicate INSERT → no `IntegrityError`, exactly one wallet row
  (`test_ensure_events_wallet_is_idempotent` asserts `COUNT == 1` + balance
  preserved). Genuinely idempotent.
- **AC3 (TDD, non-tautological):** PASS. RED captured as `ModuleNotFoundError`
  for both repo modules. The prune-window test
  (`test_prune_history_retains_only_window`) seeds an on-cutoff row whose
  timestamp **exactly equals** the cutoff (`_utc(2026,5,10,0)` for both) and
  asserts `deleted == 1` + on-cutoff retained — this **fails under a `<=`
  reversion** (would delete 2). Confirmed the boundary equality holds
  (`on_cutoff == cutoff` is `True`, `< cutoff` is `False`). Decimal scale checked
  via `as_tuple().exponent`; UTC `tzinfo` asserted on history points.
- **AC4 (no N+1):** PASS. The 6c digest explicitly flagged 6c's per-row child
  SELECTs and told 6d+ to batch. 6d complied: both `list_all` loaders use a
  **single grouped query** (`_load_history_by_user`, `_load_investors_by_fund`)
  keyed by guild + in-memory `defaultdict` grouping. Listing N rows = 2 queries,
  not N+1. `test_list_all_*` assert children are rebuilt (eager-loaded), not just
  scalars.

## Findings

No CRITICAL / HIGH / MEDIUM findings.

- **LOW (informational, no action):** All SQL goes through the SQLAlchemy
  expression API — no string interpolation, no injection surface; no
  `eval`/`exec`; no secrets. Session lifecycle is `async with self._sessionmaker()`
  (context-manager rollback on exception), matching the 6c-reviewed `user_repo.py`
  convention. `list_all`'s two reads are not wrapped in an explicit transaction —
  acceptable for reads; no bug.
- **LOW (carry-forward note, not a defect):** the gate runs `mypy src/friendex`
  only, so the test-file structural-conformance check is not gate-enforced. It
  passes today and the runtime `callable(getattr(...))` assertions backstop it,
  but 6e/6f reviewers should keep running mypy on the new test files explicitly.

## Next steps

1. 6d is CLEAN — proceed to **6e** (penalty / trade-cooldown / system-state
   repos: `IPenaltyRepo` / `ITradeCooldownRepo` / `ISystemStateRepo`). Note the
   split `upsert(dto)` signatures (cooldown/state carry `guild_id` *inside* the
   DTO) and the TTL semantics: `get` must exclude expired rows; `purge_expired`
   is a bulk DELETE `WHERE expires_at <= now` (mirror 6d's prune).
2. Then **6f** — the JSON→SQLite migrator (out of scope here).
3. Follow the established conventions in `digest-phase-6d.md`.

## References

- Review baton (this file); implementation baton:
  [007](./007-2026-05-24-6d-price-fund-repos.md)
- Code: `src/friendex/adapters/persistence/price_repo.py`,
  `src/friendex/adapters/persistence/fund_repo.py`,
  `src/friendex/adapters/persistence/__init__.py`
- Tests: `tests/adapters/persistence/test_price_repo.py`,
  `tests/adapters/persistence/test_fund_repo.py`
- Contract: `src/friendex/application/interfaces.py` (`IPriceRepo`, `IFundRepo`)
- Prior reviews: [006](./006-2026-05-24-6c-user-repo-review.md);
  digests `digest-phase-6a.md` (FK/CASCADE), `digest-phase-6c.md` (N+1 lesson)
- Gate logs: `baton-runner/br-2026-05-24-phase-6/gate-phase-6d-iter-1/`
- Issue: #2 (phase status); ADR-0001 (per-guild markets); ADR-0002 (SQLite FK)
