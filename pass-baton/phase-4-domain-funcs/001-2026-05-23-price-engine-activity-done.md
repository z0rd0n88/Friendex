# Pass-Baton: Phase 4 sub-unit 4a (price_engine + activity) complete

**Date:** 2026-05-23
**Scope:** phase-4-domain-funcs
**Branch:** feat/br-2026-05-23-p4p5/phase-4
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-23-p4p5
**HEAD:** 62fa872 chore(phase-4): add baton-runner gate + run state

## Where things stand

Sub-unit **4a** of Phase 4 is **done and green** — two domain pure-function
modules plus their tests and the domain conftest are implemented, fully tested,
and pass every scoped gate (ruff, ruff format, mypy, coverage). Files are
currently **untracked** (not committed — the manager owns all git/PR actions).
Sub-unit **4b** (`market_hours.py`, `fund_math.py` + their tests) is **not in
this work-unit's scope** and remains unbuilt.

Files created (all inside the worktree):
- `src/friendex/domain/price_engine.py`
- `src/friendex/domain/activity.py`
- `tests/domain/test_price_engine.py`
- `tests/domain/test_activity.py`
- `tests/domain/conftest.py`

## Type policy applied (Decimal-at-boundary — corrects the stale plan doc)

- Price/money params + returns are `Decimal`, quantised to `Decimal('0.01')`
  with `ROUND_HALF_EVEN`. Rate/factor tunables (`k`, `decay`) stay `float`.
- `compute_activity_return(bucket, k)` does `ΔP = k · ln(1 + activity)` where
  `activity = calculate_trending_score(bucket)`; the `ln` is computed in
  `float` (`math.log1p`) then converted back to a quantised `Decimal`.
- activity.py scores stay `float` (not money). No inputs are mutated;
  `reset_activity_bucket` returns a new `dataclasses.replace`d bucket with a
  fresh (non-aliased) channel list.

## Activity weighting chosen (the "gap" — derived from the spec)

Lifted **verbatim** from `docs/spec/original-skeleton.md`
§ENGAGEMENT/TRENDING (`calculate_trending_score`, lines ~382-423) so the
rebuilt economy matches the original:
- Soft-cap saturation `soft_cap(x, cap) = cap·(1 − exp(−x/cap))` on:
  text=100, media=50, voice_minutes=300, reactions=200, replies=100,
  role_ping_join_minutes=180.
- Weights: text 0.5, media 2.0, voice_minutes 0.1, unique_channels 1.5,
  reactions 0.2, replies 0.3, role_ping_joins 4.0, role_ping_join_minutes 0.3.
- `unique_channels` and `role_ping_joins` are intentionally **not** soft-capped
  (scarce, high-signal events) — matches the spec.
- **Deliberate omission:** the spec's optional age-decay (`score *= max(0.3,
  exp(-age/72h))`, keyed off an `activity["timestamp"]`) is **dropped**. The
  required signature is `calculate_trending_score(bucket) -> float` (no `now`),
  and `ActivityBucket` has `bucket_start` not a free-form `timestamp`; including
  decay would also break clean per-input monotonicity. Recorded as a deferred
  follow-up below.

`compute_activity_return` uses `k·ln(1+score)` per the **task spec / acceptance
criteria**, NOT the original's `log10`+baseline-centering+asymmetric-clamp
(spec lines ~452-471). The acceptance criteria explicitly overrides the doc here.

## RED evidence per criterion (TDD — test-first, captured before impl)

- **price_engine (criterion 1):** ran `pytest tests/domain/test_price_engine.py`
  before the module existed →
  `ModuleNotFoundError: No module named 'friendex.domain.price_engine'`
  (collection error, all 36 tests un-runnable). Then implemented → green.
- **activity (criterion 2):** ran `pytest tests/domain/test_activity.py` before
  the module existed →
  `ModuleNotFoundError: No module named 'friendex.domain.activity'`
  (collection error). price_engine also stayed red transitively (it imports
  `calculate_trending_score`). Implemented activity → both files green.
- After both impls: **73 passed**. Tests are parametrised for share/k scaling,
  inactivity arithmetic, tier boundaries, and per-field monotonicity; edges
  covered: zero/max activity, exact floor, buy-vs-sell direction, tier cut
  boundaries (rank 1/2/6/7/14/15 of 20), quantisation, no-mutation.

## Verification gate output (all green)

- `ruff check src/friendex/domain tests/domain` → **All checks passed!**
- `ruff format --check src/friendex/domain tests/domain` → **11 files already formatted**
- `mypy src/friendex/domain` → **Success: no issues found in 5 source files**
- Scoped coverage (module-import form — file-path form emits the documented
  "module never imported / no data" warning, a coverage quirk, see kickoff
  baton §"--cov path form"):
  `pytest ... --cov=friendex.domain.price_engine --cov=friendex.domain.activity`
  → **activity.py 100%, price_engine.py 100%** (bar is ≥95%).
- Migration-plan gate over the full domain suite
  (`pytest tests/domain/ --cov=src/friendex/domain --cov-fail-under=95`) →
  **100.00% total, 164 passed**.
- Full repo suite `pytest -q` → **191 passed** (no regressions).

## Next steps

1. Manager: commit these 5 files (one `feat(domain): price engine pure
   functions` + one `feat(domain): activity scoring`, or per repo's
   five-commits-per-file guidance), then proceed to sub-unit 4b.
2. Build **4b**: `src/friendex/domain/market_hours.py` and
   `src/friendex/domain/fund_math.py` + `tests/domain/test_market_hours.py`,
   `tests/domain/test_fund_math.py` (the conftest is already in place). See
   `docs/04-migration-plan.md` §Phase 4 (lines 286-287, 290-292).
3. 4b notes: `market_hours` is clock-dependent — pin time with the
   `frozen_now` fixture (already provided). `fund_math` must honor decision
   toggles #5/#6/#8 (kickoff baton §"Open questions").

## Open questions / risks (deferred — do NOT block on these)

- **Activity weights/caps are hardcoded module constants in `activity.py`**
  (named, documented). The original bot hardcoded them too; a future task may
  promote them to `Settings`. Flagged as a known tunable follow-up.
- **Trending-score age-decay omitted** (see weighting section). If a later
  phase wants time-decay of weekly scores, it needs a new signature taking
  `now` + a bucket timestamp; out of scope here.
- **`apply_floor_stall` vs `apply_trade_impact` clamping:** `apply_trade_impact`
  uses a simple `max(proposed, min_price)` clamp (per acceptance criteria);
  `apply_floor_stall` implements the original's full attenuation-near-floor
  logic. They are distinct functions with distinct contracts by design.

## References

- Issue: #2 (master tracking — Phase 4 box)
- Predecessor: `pass-baton/phase-4-domain-funcs/000-2026-05-22-phase-4-kickoff.md`
- Plan: `docs/04-migration-plan.md` §Phase 4 (lines ~276-305)
- Behavioral spec: `docs/spec/original-skeleton.md` §PRICE MANAGEMENT (284-356,
  473-490), §ENGAGEMENT/TRENDING (380-450), inactivity decay (819-839)
- Code: `src/friendex/domain/price_engine.py`, `src/friendex/domain/activity.py`
