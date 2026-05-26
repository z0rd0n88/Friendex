# Pass-Baton: phase-8f complete — liquidation + discipline gate green

**Date:** 2026-05-25
**Scope:** phase-8f
**Branch:** feat/phase-8f-liq-disc
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 5c33a7b chore(phase-8e): review iter1 CLEAN + digest *(work uncommitted; manager owns commits)*

## Where things stand

All Phase 8f scope is implemented and the full gate is green. RED-first
TDD per AC F1–F8 (RED via ModuleNotFoundError on first pytest run pre-impl
— see kickoff baton 000). Exit digest written at
`baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md`. The work is
parked on `feat/phase-8f-liq-disc` uncommitted; commits are the
baton-runner manager's job (per work-unit containment rule). This is the
final unit of the Phase-8 epic.

## Files (final)

NEW:
- `src/friendex/application/liquidation_events.py` — `LiquidationEvent`
  frozen dataclass (holder, target, shares, entry/exit prices, collateral
  returned, P&L, timestamp).
- `src/friendex/application/liquidation_service.py` — `LiquidationService`:
  per-guild sweep over accounts with shorts; per-(holder,target) `locked()`
  with inside-lock re-read; delegates the cover to
  `TradingService._cover_internal(force=True)`.
- `src/friendex/application/discipline_service.py` — `DisciplineService`
  + `DisciplineEvent` (frozen). Flat 17% drop with floor at `min_price`;
  no-op short-circuit when stock already at floor; per-user `locked()`
  RMW mirroring the Phase 8b `_rmw_price` shape.
- `tests/application/test_liquidation_service.py` — F1–F4 + skip-empty.
- `tests/application/test_discipline_service.py` — F5–F8.

MODIFIED:
- `src/friendex/application/trading_service.py` — extracted private
  `_cover_internal(coverer_id, target_id, shares, *, force: bool)` from
  the public `cover()`. Public method now does validate + cooldown-check
  + `locked()` + delegate + cooldown-set; signature unchanged. The
  `force=` flag is private, never on the public API.

## Design choice declared (per work-unit hint, option (a))

`_cover_internal` does **not** acquire `locked()` — callers hold the
locks. The public `cover()` and `LiquidationService._maybe_liquidate`
both wrap the helper in `locked(coverer, target)` / `locked(holder,
target)` respectively. Rationale: `LockManager` is non-reentrant (Phase
7), so making the helper re-lock would deadlock the liquidation path
that already holds an outer lock around the holder+target pair.

## Verification (final gate, scope = `tests/application/` then full repo)

```
uv run ruff check src tests                          → All checks passed!
uv run ruff format --check src tests                 → 84 files already formatted
uv run mypy src/friendex                             → Success: no issues in 43 source files
uv run pytest tests/application/ -v
  --cov=src/friendex/application --cov-fail-under=85 → 180 passed; coverage 92.68%
uv run pytest (full repo)                            → 531 passed (+9 from 8e's 522)
```

Module-level coverage (post-8f):
- `liquidation_events.py` 100%; `liquidation_service.py` 91%;
  `discipline_service.py` 92%; `trading_service.py` 94% (>= 8c's 92.53%
  baseline — no regression).

## Next steps

1. Baton-runner manager: review iteration (typical: independent reviewer
   reads the diff against `feat/phase-8e-fund-daily`).
2. On CLEAN review, commit per the migration plan's commit-boundary
   guidance (four commits): (1) feat(application): expose internal cover
   for liquidation; (2) feat(application): liquidation service; (3)
   feat(application): discipline service; (4) test(application):
   liquidation + discipline.
3. Open the PR stacked on `feat/phase-8e-fund-daily` (per `STATE.md`
   stacked-draft-PRs plan), `Refs #2`.
4. After Phase-8 epic merge: Phase 9 (`adapters/tasks/`) — wires
   `LiquidationService.check_and_liquidate_shorts` into a 5-min
   `LiquidationTask` and the Phase 12 `on_member_update` listener into
   `DisciplineService.apply_discipline_penalty`.

## Open questions / risks

- None new. Carry-forward from 8e unchanged: 8c M2
  (`ITradeCooldownRepo.get` missing `now=` kwarg), 8b M1
  (`activity_tick_k = 0.5` placeholder pre-Phase-9 wiring).
- Coverage on the three new modules is good; the small uncovered branches
  in `liquidation_service.py` (lines 144/147/150) are the inside-lock
  defensive returns for raced-away holder / short / stock — load-bearing
  but not asserted-on by F1–F4 because the test fixtures don't simulate
  the race.

## References

- Spec: `docs/04-migration-plan.md` §Phase 8f (lines 562–589).
- Exit digest: `baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md`.
- Kickoff: `pass-baton/phase-8f/000-2026-05-25-phase-8f-kickoff.md`.
- Tracking issue: #2.
- Predecessor baton: `pass-baton/phase-8e/002-2026-05-25-review-iter1-clean.md`.
- Continuity (read for this unit): `digest-phase-8c.md`,
  `digest-phase-8b.md`, `digest-phase-8a.md`,
  `baton-runner/br-2026-05-25-phase-7/digest-phase-7.md`,
  `baton-runner/br-2026-05-23-p4p5/digest-phase-4.md`.
- New code: `src/friendex/application/liquidation_service.py`,
  `src/friendex/application/discipline_service.py`,
  `src/friendex/application/liquidation_events.py`,
  `src/friendex/application/trading_service.py:576-722`
  (refactored `cover()` + new `_cover_internal`).
