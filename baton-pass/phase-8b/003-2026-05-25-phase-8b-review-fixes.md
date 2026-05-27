# Pass-Baton: Phase 8b review-iter-1 fixes — H1, M2, L1, L2 fixed; M1 documented

**Date:** 2026-05-25
**Scope:** phase-8b
**Branch:** feat/phase-8b-price-tick
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 995ef00 chore(phase-8b): review iter1 — VERDICT ISSUES

## Where things stand

Review-baton 002 issues addressed in this worktree. Gate is GREEN
(`uv run ruff check src tests && uv run ruff format --check src tests`,
`uv run mypy src/friendex`, `uv run pytest tests/application/ -v` all pass —
**94 application tests, 445 repo-wide, +6 over the 439 baseline**). Fixes
stayed within the three contract files. Iter-2 review can proceed.

## Fixes applied

### H1 — Read-modify-write race in tick methods (HIGH) — FIXED

The pre-lock `get` / post-lock `upsert` pattern is gone. Introduced a single
`_rmw_price(user_id, compute)` helper that takes the lock FIRST, then
re-reads the stock INSIDE the lock, computes `new_price` from the fresh
snapshot via the injected pure callable, and only then upserts + appends
history. All three tick methods (`activity_price_tick`,
`inactivity_decay_tick`, `vc_boost_tick`) now route their per-user write
through this helper; the outer `get` remains as a cheap pre-filter (skips
the lock entirely for stockless users) but the *price arithmetic* uses the
in-lock value, so a concurrent trade landing between the pre-filter and the
lock acquire is honoured, not clobbered.

Load-bearing RED-first test: `test_activity_tick_does_not_clobber_concurrent_upsert`
(`tests/application/test_price_tick_service.py:539-635`). A
`_BarrierPriceRepo` wrapper parks the *second* `get` (the in-lock re-read)
on an `asyncio.Event`; while parked, the test directly writes a marker
price ($200.00) via the underlying repo (bypassing the lock — modelling a
mutator that already held and released the same lock). Releasing the gate
lets the tick re-read; the RMW recomputes from the marker and the post-tick
price is `>= $200.00`. **Verified RED against the pre-fix code** (the test
captured `Decimal('101.91') < Decimal('200.00')` — i.e. the stale 100.00
read produced 101.91, which clobbered the marker), then green after the fix.

### M2 — Ticks now append_history + ratchet all_time_high (MEDIUM) — FIXED

`_rmw_price` calls `IPriceRepo.append_history(guild_id, user_id,
PricePoint(price=new_price, timestamp=datetime.now(tz=UTC)))` on every
successful write, and bumps `all_time_high = max(stock.all_time_high,
new_price)` via `dataclasses.replace` (immutability preserved; `max(...)`
ensures down-ticks never lower the ATH). Phase 11's
`/price` / `/trending` consumers will see tick-driven moves on the same
footing as trade-driven moves.

Load-bearing RED-first tests added:
- `test_activity_tick_appends_price_history` (line 645)
- `test_inactivity_decay_tick_appends_price_history` (line 686)
- `test_vc_boost_tick_appends_price_history` (line 717)
- `test_activity_tick_advances_all_time_high` (line 770)
- `test_inactivity_decay_does_not_lower_all_time_high` (line 807) — pins
  the ratchet semantics: a seeded ATH of $150.00 is preserved through a
  decay tick that drops `current` from $100 to $96.

### M1 — `activity_tick_k=0.5` documented as TBD/placeholder (MEDIUM) — DEFERRED PER CONTRACT

Per the review baton and the user-memory carry-forward note
(`baton-runner P4/P5 deferred — activity-K gap still open (needs user)`),
the default value was **not** changed. Instead the docstring on
`activity_tick_k` (`src/friendex/adapters/config.py:91-107`) now carries
an explicit `**TBD/placeholder.**` block noting:
- the original spec leaves K parameterised (no fixed value in
  `original-skeleton.md` nor the Phase 4 digest);
- the current 0.5 was chosen to mirror `price_impact_k` *semantically* but
  is not derived from either spec source;
- "Verify with product before production";
- pointer to the carry-forward `baton-runner/br-2026-05-23-p4p5` note.

**Still needs the user** to back-solve K against a representative hourly
bucket before Phase 9 wires the activity-tick loop.

### L1 — Local imports hoisted to module level — FIXED

`from datetime import UTC, datetime` (was inline in
`inactivity_decay_tick`) and `from friendex.domain.price_engine import
compute_activity_return` (was inline in `activity_price_tick`) are now at
module scope alongside `apply_floor_stall, apply_inactivity_decay`. No
behaviour change. The `TYPE_CHECKING` block is also tidied (only
`Callable`, `Iterable`, `datetime`, the adapter/application port types,
and `Stock`/`VcExtraBoost` remain there).

### L2 — Inactivity-decay floor-stall divergence documented — FIXED

The module docstring now carries a "Deliberate divergence from the original
spec" paragraph in the `inactivity_decay_tick` bullet, calling out that
the original passed the decayed proposal through `apply_floor_stall`
(attenuated drops near floor) whereas Phase 4 chose `apply_inactivity_decay`
(hard `min_price` clamp). Concrete numerical example included: $100 with
4% decay agree ($96.00); $71 with 4% diverge ($70.00 here vs ~$70.72
with floor-stall). The method's own docstring also points back to the
module docstring for the rationale.

### L3 — Folded into H1 (no separate action) — DONE

The pre-fix `_write_price` defensive in-lock `None` re-check is now the
RMW's natural guard. The double-`get` cost is gone (the helper has one
in-lock `get`, not a pre-lock + in-lock pair).

## Verification (gate-relevant)

```
$ uv run ruff check src tests && uv run ruff format --check src tests
All checks passed!
66 files already formatted

$ uv run mypy src/friendex
Success: no issues found in 32 source files

$ uv run pytest tests/application/ -v
... 94 passed in 0.49s

$ uv run pytest
... 445 passed in 6.52s
```

(Baseline before this fix: 439 passed. Delta: +6 = 1 H1 race test + 3 history
tests + 2 ATH tests.)

## Files touched (within the contract)

- `src/friendex/application/price_tick_service.py` — RMW refactor + history /
  ATH writes + module-scope imports + divergence docstring (whole-file
  rewrite via `Write`; net diff: `_write_price` → `_rmw_price` helper, three
  tick methods updated to pass a `compute` closure, module docstring
  expanded).
- `src/friendex/adapters/config.py` — only `activity_tick_k` docstring;
  default value unchanged.
- `tests/application/test_price_tick_service.py` — added `_BarrierPriceRepo`,
  6 new tests (1 H1 race + 5 M2 history/ATH).

No new dependencies; `pyproject.toml` / `uv.lock` untouched.

## Outstanding for iter-2 review / next phase

1. **M1 K value still unresolved.** The default is now correctly tagged
   as a placeholder, but somebody (likely the user) needs to back-solve
   K before Phase 9 wiring. Tracked in the user-memory carry-forward note
   and now also in `config.py:91-107`.
2. Whether the docstring-only M1 satisfies the reviewer, or whether they
   want a sub-issue filed on #2 as well, is iter-2's call.

## References

- Review baton being addressed: `baton-pass/phase-8b/002-2026-05-25-phase-8b-review-issues.md`
- Prior completion baton: `baton-pass/phase-8b/001-2026-05-25-phase-8b-complete.md`
- Spec: `docs/04-migration-plan.md:448-473` (§Phase 8b)
- Phase 8a lock-key discipline: `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md`
- Activity-K gap carry-forward: `baton-runner/br-2026-05-23-p4p5` (see user-memory note)
- Issue: #2 (phase status)
- Code touched:
  - `src/friendex/application/price_tick_service.py` (whole file)
  - `src/friendex/adapters/config.py:91-107`
  - `tests/application/test_price_tick_service.py` (additions, lines 539-841)
