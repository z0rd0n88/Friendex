# Pass-Baton: Phase 8b — PriceTickService implementation COMPLETE

**Date:** 2026-05-25
**Scope:** phase-8b
**Branch:** feat/phase-8b-price-tick
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 8ac60ba chore(phase-8a): review iter2 CLEAN + digest
(no Phase-8b commits yet — manager owns the commit + PR step)

## Where things stand

`src/friendex/application/price_tick_service.py` is implemented as a pure
orchestrator over `domain/price_engine` + repositories. All five acceptance
criteria (B1-B5) plus four additional behavioural tests pass; ruff,
ruff-format, and mypy all green; no regressions in the broader application
or full test suite (439 passing). Two `Settings` defaults were added —
`activity_tick_k=0.5` and `vc_extra_boost_multiplier=1.03` — declared
in the digest as a load-bearing tunable promotion (not magic numbers in the
service).

Manager-facing: this unit produced **no commits**; the baton-runner owns
the git/push/gh steps. The working tree contains the new service, the new
test, the two `Settings` additions, this baton, the digest, and the
`INDEX.md` row.

## Next steps

1. Manager: stage + commit per `STATE.md` boundary guidance.
   Suggested two commits:
   - `feat(application): price tick service`
     (`src/friendex/application/price_tick_service.py` +
      `src/friendex/adapters/config.py` — the two new Settings fields)
   - `test(application): price tick coverage`
     (`tests/application/test_price_tick_service.py`)
   Plus the baton + digest under their own
   `docs(pass-baton)` / `chore(phase-8b)` commits if the run prefers.
2. Open `feat/phase-8b-price-tick` PR stacked on `feat/phase-8a-activity`
   per `STATE.md`. `Refs #2`.
3. Independent review unit reads the new digest at
   `baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md` plus this baton
   and runs the same gate locally.
4. Move on to Phase 8c (trading) — the most complex unit; budget reservation
   noted in `STATE.md`.

## Acceptance criteria — verified

| # | Criterion | Test |
|---|---|---|
| B1 | activity_price_tick raises an active user's price | `test_activity_tick_raises_price_for_active_user` |
| B2 | activity_price_tick lowers an under-engaged user's price | `test_activity_tick_lowers_price_for_under_engaged_user` |
| B3 | inactivity_decay_tick respects the threshold (before/after) | `test_inactivity_decay_skipped_before_threshold` + `test_inactivity_decay_applied_past_threshold` |
| B4 | vc_boost_tick boosts ONLY users still in voice (expired window dropped) | `test_vc_boost_tick_boosts_only_in_voice_users` + `test_vc_boost_tick_drops_expired_window` |
| B5 | min_price floor enforced through every path | `test_activity_tick_floor_enforced_for_near_floor_stock` + `test_inactivity_decay_floor_enforced_for_at_floor_stock` |

## Declared deviations from the strict contract

The contract said "Files to MODIFY: none". Two surgical edits were
required and are pre-declared per the contract's "you MAY add" carve-out
for Settings tunables:

- `src/friendex/adapters/config.py` — added `activity_tick_k: float = 0.5`
  and `vc_extra_boost_multiplier: float = 1.03`. Both replace the
  service-internal magic numbers the original spec hard-codes. Without
  these, the service would either reuse `price_impact_k` (semantically
  wrong — trade impact ≠ activity gain) or embed literals
  (`Decimal("1.03")`, `0.5`) inline, violating the
  "no module-level constants for tunables" rule in CLAUDE.md and the
  Phase-4 digest. Existing tests in `tests/adapters/test_config.py`
  remain green (the defaults-spotcheck test asserts specific fields,
  not field count; extras are tolerated by `extra="ignore"`).

- `pass-baton/INDEX.md` — appended the phase-8b row. Mandated by the
  `pass-baton` skill workflow.

- `baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md` — Phase 8b
  exit digest, required by the run-shape conventions in `STATE.md`.

## Open questions / risks

- None blocking. The `VcExtraBoost` storage decision (parameter vs. dedicated
  store) is intentionally deferred to Phase 8d/9 — see digest §Deferred.
- `STATE.md` should be re-bumped by the runner (current `units_used: 6`,
  this unit adds 1).

## Verification (this run)

```
uv run ruff check src tests             → All checks passed!
uv run ruff format --check src tests    → 66 files already formatted
uv run mypy src/friendex                → Success: no issues found in 32 source files
uv run pytest tests/application/test_price_tick_service.py -v → 9 passed in 0.02s
uv run pytest tests/application/        → 88 passed in 0.47s
uv run pytest                           → 439 passed in 6.40s
```

RED-first capture: initial test collection failed at import
(`ModuleNotFoundError: friendex.application.price_tick_service`),
satisfying RED for B1-B5 simultaneously before the service module existed.
All nine tests went GREEN with the orchestrator implementation. No
fixture/test was loosened to make a passing run.

## References

- Spec: `docs/04-migration-plan.md` §Phase 8b (lines 448–473)
- Digest: `baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md`
- Continuity digests honoured: `digest-phase-8a.md`, `digest-phase-8-fakes.md`,
  `digest-phase-4.md`, `digest-phase-7.md`
- New code: `src/friendex/application/price_tick_service.py`
- New test: `tests/application/test_price_tick_service.py`
- Settings additions: `src/friendex/adapters/config.py` (two fields)
- Issue: #2 (master tracking)
- Prior baton: `phase-8b/000-2026-05-25-phase-8b-start.md`
