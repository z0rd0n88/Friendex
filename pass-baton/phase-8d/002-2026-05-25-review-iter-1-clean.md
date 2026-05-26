# Pass-Baton: Phase 8d review iter-1 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-8d
**Branch:** feat/phase-8d-portfolio
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** db7365b feat(phase-8d): portfolio + stats services (read-only)

## Where things stand

Phase 8d (Portfolio + Stats read-only services) reviewed. Gate green
(pytest 500 passed, ruff check, ruff format, mypy clean across 37 source
files). All 10 ACs (D1–D10) verified non-tautologically with concrete
numbers and load-bearing assertions. Architecture invariants from the
work-unit baton are honoured: read paths take NO locks, the one write
path (`capture_month_start_net_worth`) takes ONE per-user `locked()`
**inside** the for-loop body (Phase 7 deadlock rule), composite lock key
`<guild>:<user>` matches 8a/8c conventions, snapshots round-trip via
`dataclasses.replace`, and all four read-model dataclasses are
`frozen=True`. No new deps, no new `Settings` fields, no
`ITradeCooldownRepo` touch (M2 carry-forward stays out of scope per
spawn instructions). **VERDICT: CLEAN — no findings of any severity.**

## Findings by severity

### CRITICAL — none

### HIGH — none

### MEDIUM — none

### LOW — 1

- **L1 — Lazy `Decimal` import in `_zero_price()` is stylistic noise.**
  `stats_service._zero_price()` does `from decimal import Decimal`
  inside the function "so the `TYPE_CHECKING` block stays the sole
  top-level import". The cost saved is negligible (a single guarded
  attribute lookup on a cached module); the cost paid is one extra
  import call per missing-stock leaderboard row plus a slightly
  surprising read. A plain top-level non-TYPE_CHECKING import would be
  more idiomatic. Not blocking — the helper is documented and the
  branch is rare in production. No fix requested.

### INFO

- **AC verification — every D1–D10 is load-bearing.**
  - **D1** (long-only): cash 500 + 5×100 + 10×50 = 1500 ✓ exact `Decimal`.
  - **D2** (short-only): cash 1000 + (600+400) − 10×90 = 1100 ✓.
  - **D3** (mixed): cash 500 + 5×200 + (600+400) − 10×90 = 1600 ✓
    asserted with one concrete `Decimal("1600.00")`, not a non-zero
    guard. Domain semantics match `fund_math.compute_net_worth`.
  - **D4** (frozen short): cash 200 + (700+300) − 10×80 = 400 ✓
    `frozen=True` flag is set on the position and the math still
    counts it — proves the read path doesn't filter by frozen.
  - **D5** (capture sweep): three users with distinct net worths
    (1000 / 2000 / 120) each get **both** `net_worth` and
    `month_start_net_worth` written; final `list_all` confirms the
    exact set `{ACTOR, TARGET_A, TARGET_B}`. Round-trip via
    `dataclasses.replace` (no mutation).
  - **D6** (sort desc): three users in unsorted insertion order;
    asserts `["high", "mid", "low"]` AND `scores == sorted(scores,
    reverse=True)` AND 1-indexed ranks. Would catch a `reverse=False`
    flip immediately.
  - **D7** (zero filter): two zero-bucket users + one active;
    snapshot contains exactly `["active"]` with `score > 0`. Dropping
    the zero filter would surface all three.
  - **D8** (limit): 20 users default → 15 returned with correct
    head/tail (`u-00` / `u-14`); explicit `limit=3` → 3 with correct
    head set. Two distinct tests cover default + kwarg.
  - **D9** (24h window): explicit `now`-anchored points BOTH inside
    (110/90/100 at −6h/−12h/−1h) AND outside (999 at −48h, 1 at
    −25h) the window; asserts high=110 (the in-window high) and
    low=90 (the in-window low). Bounded by `freeze_time(now)` so the
    service's `datetime.now(tz=UTC) - 24h` lands deterministically.
    The outside-low at −25h is exactly the kind of point a 48h-wide
    bug would let in. Bonus empty-history test pins the fallback.
  - **D10** (tier coverage): 20-user population; tests the four
    distinct tiers (Elite at rank 1, High at rank 6, Medium at rank
    11, Low at rank 20) — exceeds the "at least 2 distinct tiers"
    requirement. Math reconciles with `get_engagement_tier` cuts
    (5/30/70%). Bonus single-user test pins the empty-population
    fallback to "Low".

- **24h window boundary semantics.** `stats_service.get_price_stats`
  builds `since = datetime.now(tz=UTC) - timedelta(hours=24)` and
  passes it to `IPriceRepo.get_history(..., since=since)`. The Fake's
  `since` filter is `p.timestamp >= since` (inclusive) which matches
  the documented Phase-6c adapter convention (`recorded_at >= since`).
  Test point at exactly `-25h` is OUTSIDE the inclusive `>=`
  window. ✓

- **Read paths are lockless.** Confirmed by grep:
  `portfolio_service.calculate_net_worth`,
  `portfolio_service.portfolio_snapshot`,
  `stats_service.trending_snapshot`, `stats_service.user_stats`,
  `stats_service.get_price_stats` contain no `locked(` and no
  `LockManager` call. Only `capture_month_start_net_worth` uses it.

- **Per-user lock, not whole-sweep.** `async with
  self._locks.locked(self._lock_key(account.user_id))` lives at
  `portfolio_service.py:174`, **inside** the `for account in
  accounts` loop (line 173). One lock acquired per iteration,
  released before the next. Cannot deadlock against a concurrent
  trade.

- **Composite lock key.** `_lock_key(user_id)` returns
  `f"{self._guild_id}:{user_id}"` — matches the 8a (`activity:` /
  `voice:`) and 8c trade-key conventions, and means the shared
  Phase-14 `LockManager` cannot serialise unrelated guilds against
  each other.

- **Immutability preserved.** `capture_month_start_net_worth`
  builds the updated `UserAccount` via `dataclasses.replace(fresh,
  net_worth=..., month_start_net_worth=...)` then `upsert`s — no
  attribute assignment, no aliasing.

- **Read-model dataclasses are frozen + distinct from domain
  models.** `PortfolioSnapshot`, `TrendingEntry`, `PriceStats`,
  `UserStats` all `@dataclass(frozen=True)`; the rationale (embed
  builder cannot mutate mid-render, embeds carry only display-ready
  fields) is documented in `snapshot_models.py` module docstring.

- **Domain math 100% delegated.** No `compute_net_worth` /
  `calculate_trending_score` / `get_engagement_tier` reimplementation
  in either service. `portfolio_service` calls
  `compute_net_worth` once per account; `stats_service` calls
  `calculate_trending_score` for every account in the trending /
  user-stats paths and `get_engagement_tier` once. The 24h
  high/low is a `max()/min()` over a `Decimal` list — trivial
  enough that no domain helper is warranted.

- **No new deps; no new `Settings` fields.** `git diff
  2965238..HEAD -- pyproject.toml` and `-- src/friendex/adapters/
  config.py` both empty. The two module-level constants
  (`_DEFAULT_TRENDING_LIMIT = 15`, `_PRICE_STATS_WINDOW = 24h`) are
  acceptable as service-internal defaults; if either needs to be
  tunable later, promote to `Settings` in that phase.

- **No `ITradeCooldownRepo` touch.** `grep -n "cooldown"` on all
  three new prod files returns nothing. M2 protocol-drift
  carry-forward from Phase 8c remains deferred to a follow-up
  outside 8d's contract — confirmed.

- **Coverage gap analysis.** Implementer reported 92% combined on
  the three new modules; 7 uncovered lines all reduce to two
  defensive-`None` patterns (missing user, missing stock) and the
  `_zero_price()` fallback. None are load-bearing logic. The lone
  testable gap (the per-user lock around
  `capture_month_start_net_worth` would still pass if removed
  because there is no concurrent trade in D5) is acknowledged in
  the work-unit baton; spec has no concurrency-test AC for 8d and
  the lock is concurrency-defensive rather than functional, so I
  do not require a new test.

## Verification (gate)

```
$ bash scripts/gate.sh baton-runner/br-2026-05-25-phase-8/gate-phase-8d-iter-1/
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

## Open questions / risks

- None for 8d. M2 (`ITradeCooldownRepo.get` protocol drift) and the
  stale `Stock.high_24h` / `Stock.low_24h` fields (which 8d ignores
  and recomputes dynamically) remain known carry-forwards for a
  later cleanup unit, not 8d's problem.

## Next steps

1. Orchestrator: write the phase-exit digest to
   `baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md` and
   commit + open the PR for Phase 8d using the three-commit
   boundary the implementer suggested (snapshot models / services /
   tests). Reference `Refs #2`.
2. Phase 8e/8f spawn can now consume `PortfolioSnapshot`,
   `TrendingEntry`, `PriceStats`, `UserStats` as stable read-model
   contracts. The `_personal_fund_cash` and `_current_price`
   helpers are good extraction candidates if 8e/8f need them.

## References

- Spec: `docs/04-migration-plan.md` §Phase 8d (~lines 504–529)
- Work-unit baton: `pass-baton/phase-8d/001-2026-05-25-portfolio-stats-green.md`
- Kickoff baton: `pass-baton/phase-8d/000-2026-05-25-portfolio-stats-kickoff.md`
- Prior digests:
  `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md`,
  `digest-phase-8b.md`, `digest-phase-8a.md`,
  `digest-phase-8-fakes.md`
- Issue: #2 (Phase 8d box)
- Code: `src/friendex/application/portfolio_service.py`,
  `src/friendex/application/stats_service.py`,
  `src/friendex/application/snapshot_models.py`
- Tests: `tests/application/test_portfolio_service.py`,
  `tests/application/test_stats_service.py`
