# Pass-Baton: phase-8f kickoff — liquidation & discipline services

**Date:** 2026-05-25
**Scope:** phase-8f
**Branch:** feat/phase-8f-liq-disc
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 5c33a7b chore(phase-8e): review iter1 CLEAN + digest

## Where things stand

Phase 8f is the final unit of the Phase-8 application-services epic. Scope is
locked-in by the work-unit contract:

- NEW: `application/liquidation_service.py`, `application/discipline_service.py`,
  `application/liquidation_events.py`, plus matching tests.
- MODIFY: `application/trading_service.py` — extract a PRIVATE
  `_cover_internal(force: bool)` helper so `LiquidationService` can bypass
  the `PositionFrozen` guard without exposing `force` on the public
  `TradingService.cover()` method.

RED-first per acceptance criterion (F1–F8); GREEN; gate. Last test invocation
runs the WHOLE `tests/application/` suite to confirm the trading_service
refactor did not regress Phase 8c.

## Plan

1. RED-first per AC (F1–F8); record RED output below as each lands.
2. Add `LiquidationEvent` (frozen dataclass) in `liquidation_events.py`.
3. Refactor `TradingService.cover()` → public `cover()` does
   validate-and-lock, then delegates to private
   `_cover_internal(..., *, force: bool)` which holds the inside-lock body.
   The public method always passes `force=False`. Diff stays minimal — no
   logic change for the public path.
4. Implement `LiquidationService`:
   - Sweep `user_repo.list_all(guild_id)`; for each account, for each short,
     re-read the target's stock price and compare against
     `entry_price * Decimal(str(settings.liquidation_threshold))`.
   - At-or-above threshold → acquire `locked(holder, target)` ONCE, re-read
     the account inside the lock (it might have raced), call
     `_cover_internal(holder, target, shares, force=True)`, capture the
     `CoverResult`, build a `LiquidationEvent`.
5. Implement `DisciplineService.apply_discipline_penalty(user_id, reason)`:
   - `locked(target)` → read stock inside lock → drop price by
     `1 - settings.discipline_penalty` → floor at `settings.min_price` via
     `apply_floor_stall` (down move) → upsert + `append_history` +
     `all_time_high = max(...)` (no-op short-circuit on equal). Returns the
     event payload (`user_id`, `reason`, `old_price`, `new_price`,
     `timestamp`).

## Design choices (declared per work-unit hint)

- **Locking contract for `_cover_internal`:** option (a) — the helper does
  NOT take its own locks; the caller MUST hold them. Documented on the
  helper docstring. Reason: LockManager is non-reentrant (Phase 7 digest);
  LiquidationService pre-locks both holder + target in ONE `locked()` so it
  can re-read the account-after-race and only then invoke the helper. The
  public `cover()` does the equivalent pre-lock and then calls the helper —
  the two call-sites share the same critical-section discipline.
- **Discipline price RMW:** mirrors Phase 8b `_rmw_price` shape — read
  INSIDE the lock, compute via domain helper, no-op short-circuit on equal,
  upsert + `append_history` + ATH ratchet inside the same critical section.
  Floor enforcement goes through `apply_floor_stall` (down-move branch)
  rather than a hand-rolled `max`.
- **Per-guild scope:** `LiquidationService` + `DisciplineService` both take
  `guild_id` as a ctor kwarg (ADR-0001), use composite lock key
  `f"{guild_id}:{user_id}"`.

## Acceptance criteria (RED-first each)

### LiquidationService

- F1: short at 149% of entry NOT liquidated.
- F2: short at exactly 150% of entry IS liquidated.
- F3: a FROZEN short IS still liquidated (bypasses `PositionFrozen`).
- F4: `LiquidationEvent` payload — holder, target, shares, entry, exit,
  collateral returned, P&L — match the expected values for one scenario.

### DisciplineService

- F5: `timeout` drops the user's stock by 17%.
- F6: `ban` drops the user's stock by 17%.
- F7: `min_price` floor enforced (stock near floor falls to floor, not
  below).
- F8: opt-OUT user's stock is STILL affected — `opt_in` only controls
  being traded INTO, not disciplinary penalties.

## Next steps

1. Write RED skeleton for `tests/application/test_liquidation_service.py`
   (F1–F4) and `tests/application/test_discipline_service.py` (F5–F8).
   Confirm RED output and paste into baton.
2. Add `liquidation_events.py` + service stubs to make imports resolve.
3. Refactor `trading_service.py` (`_cover_internal`).
4. Implement services until GREEN.
5. Run full gate (ruff, ruff format, mypy, pytest with 85% coverage on
   `src/friendex/application/`).

## References

- Work-unit contract: this task's parent message.
- Spec: `docs/04-migration-plan.md` §Phase 8f (lines 562–589).
- Digests (read): `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md`,
  `digest-phase-8b.md`, `digest-phase-8a.md`,
  `baton-runner/br-2026-05-25-phase-7/digest-phase-7.md`,
  `baton-runner/br-2026-05-23-p4p5/digest-phase-4.md`.
- Code: `src/friendex/application/trading_service.py:576-680` (cover());
  `src/friendex/domain/errors.py:69` (PositionFrozen);
  `src/friendex/domain/price_engine.py:70` (apply_floor_stall).
- Tracking issue: #2.
