# Pass-Baton: Phase 8b review iter-1 — VERDICT ISSUES

**Date:** 2026-05-25
**Scope:** phase-8b
**Branch:** feat/phase-8b-price-tick
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 1e57910 feat(phase-8b): price tick service

## Where things stand

Independent review of `PriceTickService` (Phase 8b). **Gate is GREEN**
(`baton-runner/br-2026-05-25-phase-8/gate-phase-8b-iter-1/`: pytest 439
passed, ruff-check, ruff-format, mypy all PASS). The five acceptance
criteria B1-B5 are met and each test is load-bearing (verified by tracing
the assertion against `apply_floor_stall` / `apply_inactivity_decay`
semantics — e.g. B5's near-floor test uses `activity_tick_k=-1000.0` so an
unclamped delta WOULD breach the floor; B5's at-floor inactivity test
asserts `current == floor` where a missing floor would yield 67.20 ≠ 70).
`reset_24h_high_low` is correctly absent (only mentioned in docstring per
Phase-3a correction 4). No new dependencies (`pyproject.toml` / `uv.lock`
untouched).

**One HIGH** lands the verdict at ISSUES: every tick method does
read-modify-write across an `await` boundary outside its lock — the lock
in `_write_price` is theatrical because `new_price` was computed from a
*pre-lock* read. A concurrent trade landing between the outer `get` and
the lock acquisition will be silently clobbered when the tick writes its
stale-derived price under the lock. Two MEDIUMs and three LOWs flagged.

## Findings by severity

### CRITICAL — none

### HIGH

**H1. Read-modify-write across `await` is non-atomic; `_write_price`'s
lock is theatrical.**
- **Files:**
  - `src/friendex/application/price_tick_service.py:144-154`
    (`activity_price_tick`)
  - `src/friendex/application/price_tick_service.py:178-190`
    (`inactivity_decay_tick`)
  - `src/friendex/application/price_tick_service.py:225-246`
    (`vc_boost_tick`)
  - `src/friendex/application/price_tick_service.py:109-123`
    (`_write_price` — the locked write that uses the stale derivative)
- **Symptom:** Each tick reads `stock = await self._price_repo.get(...)`
  *outside* the lock, computes `new_price = f(stock.current, ...)` from
  that snapshot, then enters `async with self._locks.locked(...)` in
  `_write_price` which re-reads `current` only to guard against `None`,
  and unconditionally `upsert`s `replace(current, current=new_price)`. The
  `new_price` was derived from the pre-lock value. Between the pre-lock
  `get` and the lock acquisition, a concurrent trade (Phase 8c) can land
  via the same lock key and write a new current; the tick then clobbers
  it because the write under the lock does NOT recompute from the
  re-read.
- **Why it matters:** The whole point of the composite-key lock convention
  (Phase 8a digest rule 2) is to serialise *all* writers on
  `f"{guild_id}:{user_id}"`. The tick honours the key shape but loses the
  atomicity that justifies the key. This is the same anti-pattern Phase
  8a deferred for `reward_voice_ping_response` to Phase 12 — but ticks
  run on the Phase 9 background loops side-by-side with command-handler
  trades, so the race window is real, not theoretical, and Phase 9 wiring
  is the *next* phase after this one.
- **Why the test suite didn't catch it:** No test races a tick against a
  concurrent trade on the same `(guild, user)`. The 9 behavioural tests
  all run a tick in isolation against a static repo state.
- **Fix:** Compute the new price *inside* the lock from the re-read
  value. Replace the current `_write_price(stock, new_price)` indirection
  with an in-lock RMW closure, e.g. (sketch — not a literal patch):
  ```python
  async def _rmw_price(
      self, user_id: str, compute: Callable[[Stock], Decimal | None]
  ) -> None:
      async with self._locks.locked(self._lock_key(user_id)):
          current = await self._price_repo.get(self._guild_id, user_id)
          if current is None:
              return
          new_price = compute(current)  # `compute` is the domain-pure delta
          if new_price is None or new_price == current.current:
              return
          await self._price_repo.upsert(
              self._guild_id, replace(current, current=new_price)
          )
  ```
  Each tick method then closes over its inputs (`account.today`,
  `decay`, `multiplier`, `min_price`) and passes a `compute` callable.
  The outer pre-lock `get` can stay as a cheap pre-filter (skip users
  with no stock) but the *price* used to compute `new_price` MUST come
  from the in-lock read. Add a test that interleaves a tick and a
  simulated trade-style upsert on the same key via a barrier (mirroring
  the Phase 8a two-guild isolation test pattern) — the test should
  observe last-writer-wins where the writer is whichever path read the
  freshest current, not blanket clobber.

### MEDIUM

**M1. `activity_tick_k = 0.5` default is a guess, not spec-derived.**
- **File:** `src/friendex/adapters/config.py:96`
- **Symptom:** The original spec (`docs/spec/original-skeleton.md:42-43,
  801-816`) declares `ACTIVITY_TICK_MINUTES = 15` but never declares a K
  for the activity-tick price-return formula — and the Phase 4 digest
  (`baton-runner/br-2026-05-23-p4p5/digest-phase-4.md:52-57`) explicitly
  records that `compute_activity_return` uses a *new* `k·ln(1+score)`
  form (not the original's `log10` form, no age-decay), with K left
  parameterised. The phase-8b complete baton acknowledges K=0.5 was
  picked to "match `price_impact_k`" semantically — but the docstring
  on `activity_tick_k` itself (config.py:93-96) correctly notes it is
  "distinct from `price_impact_k`". So the default is not derivable from
  either the original spec or the Phase 4 digest; it is a sensible-looking
  but unsourced value. The carry-forward note in user memory ("activity-K
  gap still open — needs user") confirms this is a known open question
  from earlier phases.
- **Why it matters:** The activity tick is the load-bearing periodic
  price input on a live bot (every 15 min for every user). A wrong K
  silently mis-scales every tick. Calibrating K against a few
  representative bucket sizes before Phase 9 wiring is cheap;
  calibrating after the bot is live is not.
- **Fix:** EITHER (a) leave 0.5 with an explicit "PLACEHOLDER — calibrate
  before Phase 9 wiring" docstring tag plus a tracking note on issue #2,
  OR (b) ask the user for the intended target (e.g. "what % move should
  a 50-text-msg, 30-voice-minute hour produce?") and back-solve.
  Non-blocking for *this* PR's contract, but should land before Phase 9
  background-task hookup.

**M2. Ticks do not append to price history nor update `all_time_high`.**
- **Files:**
  - `src/friendex/application/price_tick_service.py:109-123`
    (`_write_price` is the single write site for all three ticks)
  - Compare: `docs/spec/original-skeleton.md:284-299` (the original
    `update_price_record` updates `current`, `all_time_high`, appends a
    `PricePoint`, and prunes history older than 24h)
  - `src/friendex/application/interfaces.py:151-172` —
    `IPriceRepo.append_history` / `prune_history_older_than` already
    exist; nothing in `application/` calls them yet.
- **Symptom:** Per Phase-3a correction 4 (referenced in
  `price_tick_service.py:28-30`), `high_24h` / `low_24h` are computed
  *dynamically from history*, not stored. But the tick paths write
  `current` without appending a `PricePoint`, so a downstream
  `high_24h`/`low_24h` view over `history` will be missing every
  tick-driven price move. `all_time_high` is also never advanced.
- **Why it matters MEDIUM not HIGH:** The spec section for THIS phase
  (`docs/04-migration-plan.md:454-462`) lists files-to-create and the
  five acceptance criteria — none of them mention history maintenance.
  And the dynamic-high/low consumer doesn't exist yet
  (no `IPriceRepo.compute_high_24h_from_history` or equivalent in the
  current tree). So this is a cross-phase gap that the contract didn't
  ask 8b to close, not a contract violation. But ALL Phase-8 services
  that touch `Stock.current` (8a-8f) share this gap — somebody has to
  own appending history + bumping `all_time_high` on every price-changing
  path before Phase 9/11 reads them, and the cleanest place is the same
  `_write_price` helper introduced here.
- **Fix:** Either add an `await self._price_repo.append_history(
  self._guild_id, stock.user_id, PricePoint(price=new_price, timestamp=now)
  )` (and `all_time_high = max(...)` via `replace`) inside `_write_price`
  in this PR, OR file an explicit follow-up on issue #2 to be resolved in
  Phase 8c (which has many more `current` mutations) so the gap is owned.

### LOW

**L1. Local `from datetime import UTC, datetime` inside `inactivity_decay_tick`.**
- **File:** `src/friendex/application/price_tick_service.py:175-177`
- **Symptom:** `datetime` import is done inside the method body (and
  re-evaluated on every call) where it could live in a single
  `if TYPE_CHECKING:`-adjacent runtime import block at the top. Same
  for `from friendex.domain.price_engine import compute_activity_return`
  at line 139. Both are documented as "keep TYPE_CHECKING honest" but
  the runtime cost (CPython caches imports in `sys.modules`, so it's
  ~free) and the readability hit (split imports across the file) both
  argue for moving them to module scope. The cycle they were avoiding
  doesn't exist (`domain.price_engine` doesn't import from
  `application.price_tick_service`).
- **Fix:** Hoist both to the existing `from friendex.domain.price_engine
  import apply_floor_stall, apply_inactivity_decay` line at module
  scope. No behaviour change.

**L2. `apply_floor_stall` skipped on the inactivity path; spec used it.**
- **File:** `src/friendex/application/price_tick_service.py:187`
  vs. `docs/spec/original-skeleton.md:836` (the original passes
  `current * (1 - INACTIVITY_DECAY)` through `apply_floor_stall(current,
  proposed)`, not a direct `max(..., min_price)`).
- **Symptom:** The implementation calls `apply_inactivity_decay(current,
  decay, min_price)` which uses a *hard* floor clamp (no attenuation).
  The original spec ran the decayed price through `apply_floor_stall`,
  which attenuates drops near the floor. At $100 with 4% decay both
  agree ($96.00). Near the floor they diverge: at $71 with 4% decay,
  `apply_inactivity_decay` returns $70.00 (4% drop, hits floor); the
  spec's `apply_floor_stall(71, 68.16, 70)` returns ~$70.72 (attenuated:
  realised drop ≈ 10% of the proposed $2.84 drop). For inactive users
  near the floor, the implementation drops faster than the original.
- **Why LOW not MEDIUM:** Phase 4 chose `apply_inactivity_decay` as a
  distinct pure function and pinned its semantics. The Phase 4 digest
  doesn't flag this as a divergence — the service is using the domain
  function as designed, and it's a deliberate Phase 4 design call (the
  domain layer offers two clamp behaviours; the inactivity path uses the
  simpler one). Worth surfacing only because the docstring
  (`price_tick_service.py:159-165`) implies parity with the original
  loop without noting the floor-stall vs. hard-clamp divergence.
- **Fix:** EITHER update the docstring to note the deliberate divergence
  from the original (preferred — cheaper than re-litigating Phase 4),
  OR add an `apply_floor_stall(current, proposed_after_decay, min_price)`
  variant if game-design parity with the original near-floor stall is
  desired (this is a game-design call, not a code call).

**L3. Lock taken even when target stock is None inside `_write_price`.**
- **File:** `src/friendex/application/price_tick_service.py:117-120`
- **Symptom:** `_write_price` enters the lock, re-reads `current`, and
  returns silently if `current is None`. This costs an extra lock
  acquire+release per stockless user under a tick that already pre-filters
  via `if stock is None: continue` at the call site. The pre-filter
  makes the in-lock `None` re-check defensive (concurrent stock
  deletion between pre-lock get and lock) — that's actually correct
  semantically, but if H1 above is fixed by collapsing into an in-lock
  RMW, this guard naturally folds in and the cost is amortised.
- **Fix:** Folded into H1's fix — no separate action.

## What is verified GOOD (do not regress)

- **All 5 ACs (B1-B5) are met with load-bearing tests.** Verified by
  tracing each assertion against `apply_floor_stall` /
  `apply_inactivity_decay` semantics: B5's near-floor activity test
  uses `activity_tick_k=-1000.0` AND a heavy bucket
  (`text_msgs=500, voice_minutes=300.0`, etc.) AND `starting =
  min_price + 1.0` so the unclamped delta WOULD breach the floor; the
  `>= min_price` assertion fails the moment `apply_floor_stall` is
  bypassed (`test_price_tick_service.py:398-435`). B5's at-floor
  inactivity test asserts exact equality with the floor ($70.00) — a
  missing floor in `apply_inactivity_decay` would yield $67.20 and
  the equality breaks (`test_price_tick_service.py:438-467`). B2
  forces a negative return via `activity_tick_k=-2.0` and asserts
  `after.current < starting` — only routing the negative delta through
  `apply_floor_stall` would satisfy this (`test_price_tick_service.py:
  157-194`). B3 splits into a "just-under-threshold" (no decay) and
  "well-past-threshold" (exact $96.00) pair that pins both sides of the
  threshold boundary (`test_price_tick_service.py:202-265`). B4 splits
  into "two boosts, only the in-voice one moves price" plus an
  "expired-window dropped from survivor list" pair (`test_price_tick_service.py:
  273-390`).
- **`reset_24h_high_low` is genuinely absent** — only mentioned in the
  module docstring as a Phase-3a correction breadcrumb. `grep -rn
  "reset_24h" src tests` returns one hit (the docstring), zero
  implementations.
- **Composite-key lock discipline at every mutation site.** The single
  write site `_write_price` uses
  `self._locks.locked(self._lock_key(stock.user_id))` and
  `_lock_key` returns `f"{self._guild_id}:{user_id}"` — matching the
  Phase 8a digest rule 2. The lock is per-user (one at a time inside
  the sweep loop), not all-at-once across the guild — no
  deadlock/over-serialisation surface.
- **Immutability honoured.** `dataclasses.replace(current,
  current=new_price)` is used to build the upserted aggregate; no
  in-place mutation of `Stock`/`UserAccount`/`PricePoint`. Inputs to
  `vc_boost_tick` are iterated, never mutated; survivors include
  `replace(boost, last_boost=now)` for the boosted entries.
- **`vc_extra_boost_multiplier = 1.03` IS spec-derived** —
  `docs/spec/original-skeleton.md:895` (`proposed = current * 1.03`).
  This default is genuinely calibrated against the original bot, unlike
  `activity_tick_k`.
- **`Iterable[VcExtraBoost]` parameter design is reasonable for Phase 9.**
  Storage ownership of the volatile extra-boost map staying with the
  caller (background-task layer) matches the original bot's in-memory
  dict pattern and avoids prematurely committing to a persistence shape
  before Phase 9 wires the loops. Not a complication for 8c-8f (those
  services don't touch VC state).
- **No new dependencies.** `git diff feat/phase-8a-activity...HEAD --
  pyproject.toml uv.lock` is empty.
- **Pure-orchestrator architecture is preserved.** All math is in
  `domain/price_engine` (`apply_floor_stall`, `apply_inactivity_decay`,
  `compute_activity_return`); the service contains exactly one piece of
  glue arithmetic — `current * (_ONE + ret_pct / _PERCENT)` — which
  mirrors the original spec's `current_price * (1 + ret_pct / 100.0)`
  (`docs/spec/original-skeleton.md:812`) and is the proposal step that
  feeds `apply_floor_stall`. No duplicated decay formula. No
  hand-rolled floor clamp. The 22-line `_write_price` helper is pure
  repo+lock glue.

## Next steps

1. **Fix H1.** Collapse the pre-lock `get` and the in-lock write into a
   single RMW pattern (see sketch above). Add an interleaving test that
   barrier-races a tick against a simulated concurrent upsert on the
   same `(guild, user)` key — mirror the Phase 8a two-guild isolation
   test's `asyncio.Barrier` pattern (`tests/application/test_activity_service.py:
   489-560`). The test should fail against the current
   pre-lock-read/post-lock-write code and pass after the RMW refactor.
2. **Decide M1.** Either pin `activity_tick_k` against a
   user-specified target hourly bucket, or tag the default as
   PLACEHOLDER and open a tracking sub-issue on #2 to resolve before
   Phase 9 wiring.
3. **Decide M2.** Add `append_history` + `all_time_high` bump to
   `_write_price` in this PR, OR open a tracking sub-issue covering
   all `current`-mutation paths (8a-8f) to be resolved before Phase
   11 (`/price`, `/trending` consumers of high/low/all-time).
4. **Address LOWs.** L1 + L2 are docstring/import hygiene; L3 folds
   into H1's fix.
5. Re-run the gate (`scripts/gate.sh
   baton-runner/br-2026-05-25-phase-8/gate-phase-8b-iter-2/`). On
   green + new interleaving test green, this unit goes to iter-2
   review.

## References

- Issue: #2 (phase status)
- Gate logs (this iter): `baton-runner/br-2026-05-25-phase-8/gate-phase-8b-iter-1/`
- Work baton under review: `pass-baton/phase-8b/001-2026-05-25-phase-8b-complete.md`
- Spec: `docs/04-migration-plan.md:448-473` (§Phase 8b)
- Original loops: `docs/spec/original-skeleton.md:801-816` (activity),
  `docs/spec/original-skeleton.md:818-839` (inactivity decay),
  `docs/spec/original-skeleton.md:870-903` (vc_extra_boost_step)
- Phase 4 domain functions:
  `baton-runner/br-2026-05-23-p4p5/digest-phase-4.md:9-13, 52-57`
- Phase 8a lock-key discipline:
  `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md` (rules 1-5)
- Code reviewed: `src/friendex/application/price_tick_service.py` (whole
  file, 248 lines); `src/friendex/adapters/config.py:91-99` (two new
  Settings)
- Tests reviewed: `tests/application/test_price_tick_service.py` (9
  tests, 497 lines)
