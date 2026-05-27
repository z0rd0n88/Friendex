# Pass-Baton: Phase 8b M1 closed — `activity_tick_k` calibrated 0.5 → 0.3

**Date:** 2026-05-25
**Scope:** phase-8-followup
**Branch:** feat/phase-8-followup-cooldown-and-k
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-8
**HEAD:** 68c1a8f refactor(application): widen ITradeCooldownRepo.get with kwarg-only now=
(this baton precedes the chore B commit)

## Where things stand

Chore B of the two-chore Phase 8 follow-up unit (the 8b review's M1 — K
placeholder) is implementation-complete and gate-green. The
`Settings.activity_tick_k` default is now **0.3** (was 0.5); the docstring
no longer carries the "TBD/placeholder" wording and explicitly documents
the initial calibration date + rationale + re-tune-on-Phase-9 instruction.
The H1 RMW test comment that referenced "k=0.5" is updated to phrase the
atomicity proof in terms of "any positive K" (default K=0.3).

No test logic changed; every assertion in `test_price_tick_service.py` is
relative (`>`, `>=`, comparisons to `starting`/`marker`/`min_price`) and
holds for any positive K. The RED/GREEN signal here is the gate suite as
a whole: pre-change baseline 533 passing, post-change 533 passing, no
regression.

Together with chore A (committed at `68c1a8f`), both Phase 8 follow-up
items (8c M2 + 8b M1) are now closed. The branch is ready to push +
PR per the work-unit contract; the manager owns push.

## Verification (chore B in isolation, post-chore-A HEAD)

```
uv run ruff check src tests        # All checks passed!
uv run ruff format --check src tests # 84 files already formatted
uv run mypy src/friendex            # Success: no issues found in 43 source files
uv run pytest -q                    # 533 passed in 6.71s
uv run pytest tests/application/test_price_tick_service.py -v
                                    # 15 / 15 PASSED — no behavioural drift
```

## Next steps

1. **Commit chore B.** Single commit, exact template message:
   `chore(config): calibrate activity_tick_k 0.5 -> 0.3 (8b M1 follow-up)`
   with the body and `Refs #2 (8b M1 follow-up)` footer from the work-unit
   spec.
2. **Hand back to the baton-runner manager.** Both chores land as a single
   PR; the manager opens it and runs the review pass.

## Open questions / risks

- The chosen K=0.3 is a conservative initial calibration, not a measured
  optimum. The docstring is explicit that **Phase 9** (background loops
  wiring the live activity tick) is the right place to re-tune from
  observed bucket distributions. If a representative bucket distribution
  appears earlier (e.g. from staging), the value can move without breaking
  any test — every assertion is K-agnostic now.

## References

- Issue: #2 (Phase 8 tracker, 8b M1)
- Review baton documenting K placeholder:
  [`baton-pass/phase-8b/004-2026-05-25-phase-8b-review-iter2-clean.md`](../phase-8b/004-2026-05-25-phase-8b-review-iter2-clean.md)
- Chore A baton:
  [`./000-2026-05-25-cooldown-protocol-widened.md`](./000-2026-05-25-cooldown-protocol-widened.md)
- Files touched in this chore:
  - `src/friendex/adapters/config.py` — `activity_tick_k` default 0.5 → 0.3;
    docstring softened from "TBD/placeholder" to "Initial calibration
    (2026-05-25)" with rationale + Phase-9-retune note.
  - `tests/application/test_price_tick_service.py` — stale comment
    "positive return at k=0.5" replaced with "any positive K (default
    K=0.3)". Assertion logic unchanged.
