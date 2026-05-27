# Pass-Baton: Phase 8b review iter-2 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-8b
**Branch:** feat/phase-8b-price-tick
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 5888801 fix(phase-8b): address review findings (iter 1)

## Where things stand

Independent re-review of Phase 8b after the iter-1 fixes. **Gate is GREEN**
(`baton-runner/br-2026-05-25-phase-8/gate-phase-8b-iter-2/`: pytest 445
passed, ruff-check, ruff-format, mypy all PASS — `+6` over the 439 baseline:
1 H1 race test + 3 history-append tests + 2 all-time-high tests). Iter-1's
single HIGH (RMW race) and both MEDIUMs (history+ATH gap, K placeholder
docstring) are genuinely resolved or deferred-as-documented per contract;
both LOWs (import hoist + floor-stall divergence) are addressed; L3 folded
cleanly into the H1 refactor. No new dependencies (`pyproject.toml` /
`uv.lock` untouched). The five original ACs (B1-B5) still hold against
the same non-loosened tests — the fix was additive (move read inside lock,
add history append, ratchet ATH), not a rewrite of the original
behavioural surface. **VERDICT CLEAN.**

## Iter-1 finding resolution

### H1 (HIGH — RMW race) — RESOLVED, load-bearing test

Pre-fix `_write_price(stock, new_price)` is gone, replaced by
`_rmw_price(user_id, compute: Callable[[Stock], Decimal])`
(`src/friendex/application/price_tick_service.py:127-165`). The lock is
taken FIRST, then the in-lock `get` reads a fresh `Stock`, then the
injected `compute` closure derives `new_price` from that fresh snapshot.
The outer pre-lock `get` survives only as a stockless-user pre-filter —
the *price arithmetic* now uses the in-lock value. Verified by tracing
every tick path:

- `activity_price_tick` (line 169-196) — outer `get` pre-filter; `ret_pct`
  is loop-invariant w.r.t. stock (depends only on `account.today`, which
  the tick does not mutate), so it is bound via a default-arg closure
  trick (`_ret_pct: Decimal = ret_pct`) to avoid the Python late-binding
  loop-closure trap. `compute(stock_now)` then derives `proposed = stock_now.current
  * (1 + _ret_pct/100)` → `apply_floor_stall(stock_now.current, proposed,
  min_price)`. `_rmw_price` honours the read-inside-lock convention.
- `inactivity_decay_tick` (line 200-232) — outer `get` pre-filter; `compute`
  closes over loop-invariant `decay` + `min_price` only;
  `apply_inactivity_decay(stock_now.current, decay, min_price)` is computed
  from the in-lock value.
- `vc_boost_tick` (line 236-291) — outer `get` pre-filter; `compute` closes
  over loop-invariant `multiplier` + `min_price`; `proposed =
  stock_now.current * multiplier → apply_floor_stall(...)` uses the in-lock
  value.

**Load-bearing test verified.** `test_activity_tick_does_not_clobber_concurrent_upsert`
(`tests/application/test_price_tick_service.py:565-630`) uses a
`_BarrierPriceRepo` wrapper that parks the *second* `get` (the in-lock
re-read inside `_rmw_price`) on an `asyncio.Event`. While parked, the test
directly upserts a $200 marker on the inner repo (bypassing the wrapper —
modelling a concurrent writer who already released the lock). Releasing
the gate lets the RMW re-read; the post-tick price is asserted `>= $200`.
Cross-checked against pre-fix code at commit `1e57910`: pre-fix
`_write_price` would `upsert(replace(re_read_stock, current=new_price))`
where `new_price` was derived from the **pre-lock** $100, yielding ≈ $101.91
< $200 → test fails. The iter-1 fix baton confirmed this RED capture
verbatim. Genuine atomicity proof.

### M2 (MEDIUM — history + ATH gap) — RESOLVED, non-tautological tests

`_rmw_price` (lines 161-165) calls
`IPriceRepo.append_history(guild_id, user_id, PricePoint(price=new_price,
timestamp=now))` on every successful write and ratchets
`all_time_high = max(stock.all_time_high, new_price)` via
`dataclasses.replace` (line 156-159). Append and ATH update are inside
the same critical section as the price upsert — single source of truth.
No-op early-return at line 153-154 (`new_price == stock.current`) correctly
skips BOTH upsert and history append, so a stable stock doesn't pollute
history.

Tests verify per tick path:

- `test_activity_tick_appends_price_history` (line 638-675) — asserts
  `len(history_after) == 1` AND `history_after[0].price == after.current`
  (the appended point reflects the new price, not a sentinel — fails
  against a stub that just calls append with any value).
- `test_inactivity_decay_tick_appends_price_history` (line 678-707) — same
  shape, decay path.
- `test_vc_boost_tick_appends_price_history` (line 710-756) — same shape,
  VC boost path.
- `test_activity_tick_advances_all_time_high` (line 759-794) — seeds
  `current=100, ATH=100`, asserts `after.current > 100 AND after.all_time_high
  == after.current`. Fails if ATH is left at 100.
- `test_inactivity_decay_does_not_lower_all_time_high` (line 797-839) —
  seeds `current=100, ATH=150`, runs a decay tick that drops `current` to
  $96, asserts `after.current < 100 AND after.all_time_high == 150` —
  pins the ratchet semantics (would fail against an unconditional
  `all_time_high = new_price` assignment).

All five M2 tests load-bearing.

### M1 (MEDIUM — `activity_tick_k=0.5` placeholder) — DEFERRED-AS-DOCUMENTED

Default value **unchanged** at `0.5` per contract (the carry-forward note
explicitly says the back-solve "needs user"). The Settings field docstring
(`src/friendex/adapters/config.py:91-108`) now carries the explicit
`**TBD/placeholder.**` block:

- States the original spec leaves K parameterised (no value in
  `original-skeleton.md` nor the Phase 4 digest);
- States 0.5 was chosen to mirror `price_impact_k` *semantically* and is
  not derived from either spec source;
- Says "Verify with product before production";
- Points at the carry-forward note in `baton-runner/br-2026-05-23-p4p5`.

Clear enough that the next reader cannot mistake it for a calibrated value.
Resolution before Phase 9 wiring is still on the user — flagged in
"Outstanding" below.

### L1 (LOW — local imports) — RESOLVED

`from datetime import UTC, datetime` and `from friendex.domain.price_engine
import compute_activity_return` are now at module scope (lines 67, 72-76)
alongside `apply_floor_stall, apply_inactivity_decay`. No inline imports
remain in method bodies. `TYPE_CHECKING` block (lines 78-85) is tidy and
only holds truly type-only symbols.

### L2 (LOW — floor-stall divergence) — RESOLVED

Module docstring (lines 20-26) now carries the "Deliberate divergence from
the original spec" paragraph explicitly: at $100 with 4% decay both agree
($96.00); near the floor they diverge ($71 → $70.00 with `apply_inactivity_decay`
vs. ~$70.72 with `apply_floor_stall`). The `inactivity_decay_tick`
docstring (line 207-208) points back to it. Future readers can see
this is Phase-4-pinned semantics, not a bug.

### L3 (LOW — defensive double-None-check cost) — FOLDED INTO H1

The pre-fix double `get` (pre-lock + in-lock for None-check) is gone. The
RMW helper has exactly one `get` (in-lock), and the outer per-method
pre-filter `get` short-circuits stockless users without acquiring the lock.
No separate action needed.

## Light code-review + security pass on new code

Reviewed `git diff feat/phase-8a-activity...HEAD --
src/friendex/application/price_tick_service.py
src/friendex/adapters/config.py
tests/application/test_price_tick_service.py`. No NEW issues:

- **Closure late-binding trap avoided.** `activity_price_tick`'s
  `def compute(stock_now: Stock, _ret_pct: Decimal = ret_pct)` uses the
  default-arg pattern to bind `ret_pct` per-iteration — the most common
  Python closure bug. `inactivity_decay_tick` and `vc_boost_tick`'s
  closures only capture loop-invariants (declared above the loop), so
  they're safe by construction. Both patterns are Pythonic and explicit.
- **`all_time_high` ratchet is correct.** `max(stock.all_time_high,
  new_price)` runs inside the early-return guard, so a no-op tick
  doesn't churn ATH. A downward tick keeps ATH unchanged because the
  prior ATH is the max. Tested.
- **History order vs. upsert order.** Append happens AFTER upsert under
  the same lock. Per-call sequencing is fine for the application-layer
  contract (the repo is a Phase 8-fakes in tests and async SQLAlchemy in
  Phase 6 — neither raises mid-call atomicity guarantees as a current
  service concern). If a future repo could fail `append_history` after
  a successful `upsert`, the price moves without a history entry — but
  that's a Phase 9/repo-layer durability concern, not a Phase 8b
  ordering bug. Not flagged.
- **No new race introduced by hoisting the read inside the lock.** The
  outer pre-filter `get` is purely advisory (skips stockless users
  entirely) and is followed by an in-lock re-read — a delete landing
  between the two is correctly handled by the in-lock None-check at
  line 150-151.
- **No security regression.** No user-input handling here (repo ports
  abstract DB access), no `eval`/`exec`, no string-format SQL, no
  secret handling. Decimal + UTC invariants preserved end-to-end.
- **Test isolation.** `_BarrierPriceRepo` is a per-test wrapper, not a
  fixture — no global state. The `asyncio.Event`-based gate is
  deterministic (no `sleep`, no time-based flakiness). `wait_for(...,
  timeout=1.0)` is the right safety net.

## Original 5 ACs (B1-B5) still met against non-loosened tests

Verified by running the original 9 behavioural tests
(`tests/application/test_price_tick_service.py:1-501` pre-fix) plus
spot-checking the assertions against the post-fix code:

- B1 (activity tick positive bucket → price rises): unchanged, passes.
- B2 (activity tick negative K → price falls via floor_stall): unchanged,
  passes.
- B3 (inactivity decay near vs. past threshold): unchanged, passes.
- B4 (VC boost: only in-voice + non-expired entries boost; expired
  entries dropped): unchanged, passes.
- B5 (floor invariants on both paths): unchanged, passes.

Each was load-bearing against the original code (per the iter-1 review's
verification) and is still load-bearing — the post-fix code routes through
the same domain functions in the same order, so the assertions still
mutation-test the right behaviour.

## What is verified GOOD (do not regress in 8c-8f)

- **Read-INSIDE-lock pattern** is the canonical RMW shape for all per-user
  price mutations. `_rmw_price(user_id, compute)` is the single write site;
  composite key `f"{guild_id}:{user_id}"`; one `locked()` call per critical
  section.
- **Every successful price change appends a `PricePoint` AND ratchets
  `all_time_high`** — no-op writes (where `new_price == stock.current`)
  correctly skip both.
- **Pure orchestrator** — math stays in `domain/price_engine`; the service
  has exactly one piece of glue arithmetic
  (`current * (1 + ret_pct/100)`) mirroring the original
  `activity_price_step`.
- **Volatile state by parameter** — `vc_boost_tick` takes
  `Iterable[VcExtraBoost]` + returns survivors; storage stays at the
  Phase 9 task layer.
- **No new deps.** `pyproject.toml`/`uv.lock` untouched vs.
  `feat/phase-8a-activity`.

## Outstanding for next phase

1. **M1 still needs the user.** `activity_tick_k=0.5` is correctly tagged
   as a placeholder. Before Phase 9 wires the activity-tick loop, the
   user must back-solve K against a representative hourly bucket. The
   carry-forward in `baton-runner/br-2026-05-23-p4p5` and the docstring
   at `src/friendex/adapters/config.py:91-108` are the two pointers.
2. **Phase 8b digest needs refresh on CLEAN.** The pre-existing
   `baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md` was written
   against the iter-1 code (references `_write_price`, omits the
   read-INSIDE-lock convention, no append_history/ATH note). Updating it
   so 8c-8f inherit the post-fix conventions is part of this CLEAN exit.

## References

- Iter-2 gate: `baton-runner/br-2026-05-25-phase-8/gate-phase-8b-iter-2/`
- Iter-1 review baton: `baton-pass/phase-8b/002-2026-05-25-phase-8b-review-issues.md`
- Iter-1 fix baton: `baton-pass/phase-8b/003-2026-05-25-phase-8b-review-fixes.md`
- Iter-1 gate: `baton-runner/br-2026-05-25-phase-8/gate-phase-8b-iter-1/`
- Code reviewed: `src/friendex/application/price_tick_service.py` (291 lines, whole file);
  `src/friendex/adapters/config.py:91-111`
- Tests reviewed: `tests/application/test_price_tick_service.py` (839 lines, whole file)
- Pre-fix diff comparison: `git show 1e57910:src/friendex/application/price_tick_service.py`
- Spec: `docs/04-migration-plan.md:448-473` (§Phase 8b)
- Issue: #2 (phase status)
