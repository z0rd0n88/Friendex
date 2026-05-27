# Pass-Baton: phase-8f review iter1 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-8f
**Branch:** feat/phase-8f-liq-disc
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** c84ca79 feat(phase-8f): liquidation + discipline services

## Verdict

**CLEAN.** Gate green, ACs F1–F8 met with load-bearing assertions, lock
discipline correct, public API surface preserved, no regression in 8c.
Exit digest written at
`baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md`. Final unit of the
Phase-8 epic — manager should commit per spec's four-commit boundary, open
the stacked PR, and close the Phase-8 baton-runner.

## Where things stand

Phase 8f delivers `LiquidationService` + `DisciplineService` +
`LiquidationEvent` + private `TradingService._cover_internal(force=…)`. The
work-unit chose the spec's option (a): `_cover_internal` does NOT take
locks; both callers (public `cover()` and `LiquidationService._maybe_liquidate`)
hold the per-(actor, target) lock in a single `locked()` block, so the
non-reentrant `LockManager` (Phase 7) is never re-entered. Verified in
`src/friendex/application/trading_service.py:606-614` (public path) and
`src/friendex/application/liquidation_service.py:139-161` (liquidation path)
— no nested `locked()` on the same manager.

## Gate (reproduced this review)

```
bash scripts/gate.sh baton-runner/br-2026-05-25-phase-8/gate-phase-8f-iter-1/
→ pytest PASS · ruff-check PASS · ruff-format PASS · mypy PASS · GATE: PASS

uv run pytest tests/application/ --cov=src/friendex/application --cov-fail-under=85
→ 180 passed in 1.25s; total coverage 92.68% (≥ 85% gate met)
  liquidation_events.py 100% · liquidation_service.py 91% · discipline_service.py 92%
  trading_service.py 94% (≥ 8c baseline 92.53% → no regression)

uv run pytest tests/application/test_trading_service.py
→ 41 passed (8c suite green, confirms _cover_internal refactor preserved behaviour)
```

## AC verification (all load-bearing)

- **F1** ✓ — `test_short_below_threshold_not_liquidated` puts price at 149
  (strictly < 150) and asserts `events == []` AND the short row is
  untouched. Boundary correctly excluded.
- **F2** ✓ — `test_short_at_threshold_is_liquidated` puts price at exactly
  150 (`entry * 1.5`); the service uses `stock.current >= trigger_price`
  (liquidation_service.py:153) so equality liquidates. Matches CLAUDE.md
  "1.5×" wording and spec "shorts at 150% of entry".
- **F3** ✓ — `test_frozen_short_still_liquidated` uses `frozen=True` and
  exercises the full `check_and_liquidate_shorts` entry point (not
  `_cover_internal` directly) — proves the bypass contract end-to-end.
- **F4** ✓ — `test_liquidation_event_payload_correct` asserts every field
  with concrete numbers: holder, target, shares=10, entry=$100.00,
  exit=$150.00, collateral=$1000.00, pnl=$-500.00, timestamp=NOW.
- **F5** ✓ — `test_timeout_drops_stock_by_discipline_penalty`: $100 → $83.00
  (exact `100 * 0.83`); also asserts a single `PricePoint` was appended to
  history (load-bearing for 8b RMW pattern compliance).
- **F6** ✓ — `test_ban_drops_stock_by_discipline_penalty`: $200 → $166.00
  exact.
- **F7** ✓ — `test_min_price_floor_enforced_when_near_floor`: $75 (above
  floor) → would compute $62.25 → clamped to exactly $70.00 (asserts
  equality with `min_price`). Pre-asserts `settings.min_price == 70.0` so
  the test is self-describing.
- **F8** ✓ — `test_optout_user_stock_still_disciplined` creates
  `opt_in=False` user, applies ban, asserts price still drops to $83.00.
  `apply_discipline_penalty` correctly does NOT check `opt_in`.

## Architecture checks (all passed)

- **Lock composition** ✓ — design (a) confirmed in code: public `cover()`
  acquires `locked(coverer, target)` then calls `_cover_internal(force=False)`;
  `LiquidationService._maybe_liquidate` acquires `locked(holder, target)`
  then calls `_cover_internal(force=True)`. Neither path nests a second
  `locked()` on the same manager, so the non-reentrant lock will not
  deadlock at runtime. `_cover_internal`'s docstring explicitly states the
  contract ("caller MUST hold the locks; this helper does NOT acquire
  `locked()`").
- **Private API preserved** ✓ — `_cover_internal` has a leading underscore;
  public `cover()` signature is `(coverer_id, target_id, shares)` — no
  `force` parameter exposed. Confirmed by grep of all `force=` usages: only
  on `_cover_internal` call sites + docstrings.
- **8b RMW pattern** ✓ — `DisciplineService.apply_discipline_penalty`
  matches `PriceTickService._rmw_price` shape: inside single per-user
  `locked()`, read stock → compute new price (with floor) → upsert with
  `all_time_high` ratchet (`max(stock.all_time_high, new_price)`) →
  `append_history`. No-op short-circuit when stock already at floor skips
  upsert AND history (keeps the log quiet — same as 8b).
- **Composite lock keys** ✓ — both new services use
  `f"{guild_id}:{user_id}"` (ADR-0001 isolation) via `_lock_key` helpers
  mirroring 8c.
- **Per-short sweep locking** ✓ — `check_and_liquidate_shorts` enumerates
  shorts pre-lock and acquires `locked(holder, target)` per candidate;
  unrelated accounts never serialise on a single liquidation tick.
- **Immutability** ✓ — `LiquidationEvent`, `DisciplineEvent`,
  `DisciplineReason` are `frozen=True` / `Literal`. All state mutations use
  `dataclasses.replace`.

## No new deps · Settings additions

`git diff feat/phase-8e-fund-daily...HEAD -- pyproject.toml uv.lock` →
empty. No new runtime or dev deps. Settings used are already on `Settings`
from earlier phases — confirmed: `liquidation_threshold = 1.5`,
`discipline_penalty = 0.17`, `min_price = 70.0` (`src/friendex/adapters/config.py:82,86,89`).

## Findings by severity

- **CRITICAL** — none.
- **HIGH** — none.
- **MEDIUM** — none.
- **LOW** — none. (The three uncovered branches in
  `liquidation_service.py` lines 144/147/150 are defensive raced-away
  returns the kickoff baton explicitly flagged; not blocking, not in
  scope to add fakes for.)

## Carry-forwards (verified still deferred, not 8f's job)

- **8b M1** — `activity_tick_k = 0.5` placeholder still present at
  `src/friendex/application/price_tick_service.py:180`; resolves in Phase 9
  wiring.
- **8c M2** — `ITradeCooldownRepo.get` protocol drift (missing `now=`
  kwarg); no edits touched the cooldown protocol this unit.
- **8a 2 LOWs** — still deferred to Phase 12.

## Next steps

1. Baton-runner manager: commit per spec's four-commit boundary —
   (1) `feat(application): expose internal cover for liquidation`,
   (2) `feat(application): liquidation service`,
   (3) `feat(application): discipline service`,
   (4) `test(application): liquidation + discipline`.
2. Open PR stacked on `feat/phase-8e-fund-daily`, `Refs #2`.
3. Close the Phase-8 baton-runner work (this is the final unit).
4. Phase 9 (`adapters/tasks/`) will wire
   `LiquidationService.check_and_liquidate_shorts` into a 5-min task and
   the Phase 12 `on_member_update` listener into
   `DisciplineService.apply_discipline_penalty`. The digest summarises the
   contract for those consumers.

## References

- Spec: `docs/04-migration-plan.md` §"Phase 8f — Liquidation & Discipline
  Services" (lines 562–589).
- Gate artefacts: `baton-runner/br-2026-05-25-phase-8/gate-phase-8f-iter-1/`.
- Exit digest: `baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md`.
- Predecessor baton: `baton-pass/phase-8f/001-2026-05-25-phase-8f-complete.md`.
- Kickoff baton: `baton-pass/phase-8f/000-2026-05-25-phase-8f-kickoff.md`.
- Code under review:
  - `src/friendex/application/liquidation_events.py`
  - `src/friendex/application/liquidation_service.py`
  - `src/friendex/application/discipline_service.py`
  - `src/friendex/application/trading_service.py:576-718` (refactored
    `cover()` + new private `_cover_internal`)
  - `tests/application/test_liquidation_service.py`
  - `tests/application/test_discipline_service.py`
- Tracking issue: #2.
