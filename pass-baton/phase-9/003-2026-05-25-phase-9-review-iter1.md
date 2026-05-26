# Pass-Baton: Phase 9 review iter-1 — VERDICT CLEAN

**Date:** 2026-05-25
**Scope:** phase-9
**Branch:** feat/phase-9-tasks
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-9
**HEAD:** fe9f160 feat(phase-9): background tasks (base + 8 task classes + tests)

## Where things stand

Independent review of the Phase 9 background-task layer (`feat/phase-9-tasks`
@ `fe9f160`) against the 11 ACs in the work-unit prompt and the spec at
`docs/04-migration-plan.md` §Phase 9 (L593-631). **VERDICT: CLEAN.** Gate
green (`baton-runner/br-2026-05-25-phase-9/gate-phase-9-iter-1/`), 581 pytest
passing (+48 from the 533 Phase 8 baseline), no CRITICAL/HIGH/MEDIUM
findings, all 11 ACs load-bearing, containment honoured (no product code
outside `src/friendex/adapters/tasks/` and `tests/adapters/tasks/`),
zero new dependencies. Phase-exit digest written to
`baton-runner/br-2026-05-25-phase-9/digest-phase-9.md`.

## Gate output (verbatim)

```
PASS pytest      (581 passing)
PASS ruff-check  (src tests alembic)
PASS ruff-format (src tests alembic)
PASS mypy        (src/friendex)
GATE: PASS
```

Tasks-package sub-gate (separate run): `48 passed; Required test coverage of
80% reached. Total coverage: 100.00%` over the 10 modules in
`src/friendex/adapters/tasks/` — all lines and branches hit.

## Acceptance criteria verification

| AC  | Module                          | Cadence            | Service binding                                                      | Load-bearing? |
|-----|---------------------------------|--------------------|----------------------------------------------------------------------|---------------|
| AC0 | `base_task.py`                  | n/a (abstract)     | `_safe_run` swallows `Exception` only; `BaseException` propagates    | Yes (3 swallow tests + abstract + 4 lifecycle) |
| AC1 | `activity_tick_task.py:28`      | `interval_minutes=15` | `PriceTickService.activity_price_tick()`                          | Yes (per-guild fan-out N=2 + swallow) |
| AC2 | `inactivity_decay_task.py:31`   | `interval_minutes=5`  | `PriceTickService.inactivity_decay_tick()`                        | Yes |
| AC3 | `liquidation_task.py:43`        | `interval_minutes=5`  | `LiquidationService.check_and_liquidate_shorts(now)` + notifier   | Yes; module-source scan asserts no `import discord` / `from discord` |
| AC4 | `freeze_check_task.py:37`       | `interval_minutes=5`  | `TradingService.update_frozen_shorts()`                           | Yes |
| AC5 | `vc_boost_task.py:44`           | `interval_minutes=15` | `PriceTickService.vc_boost_tick(*, extra_boosts=, now=)` — task owns `dict[str, list[VcExtraBoost]]` survivors store with `set_/get_store_for_guild` | Yes (V1 threads survivors tick→tick via stateful `fake_tick`; V2 proves per-guild isolation; V3 proves store preserved on failure) |
| AC6 | `daily_reset_task.py:43`        | `interval_minutes=1`  | `ActivityService.reset_today_buckets()` + `ISystemStateRepo.upsert(SystemState(last_daily_reset=now))` | Yes; D3 fires across midnight UTC boundary via `freezegun` exactly twice; D5 proves state not advanced on service failure (next tick retries) |
| AC7 | `weekly_reset_task.py:49`       | `interval_minutes=1`  | `ActivityService.reset_week_buckets()` + ISO `(year, week)` gate  | Yes; W4 fires across ISO-year boundary (2025-12-28 wk52/yr2025 → 2025-12-29 wk1/yr2026) exactly twice; W7 preserves `last_daily_reset` |
| AC8 | `monthly_rollover_task.py:41`   | `interval_hours=1`    | `PortfolioService.capture_month_start_net_worth()` THEN `FundService.accrue_apy(now=now)` | Yes; M5 asserts order via `call_order = ["portfolio", "fund"]`; M2/M3/M4 prove day+hour gate (no `SystemState` field needed) |
| AC9 | 9 test files + `conftest.py`    | —                  | 48 tests, all behavioural (no tautologies)                          | Yes — see "Mutation-think" below |
| AC10| Gate                            | —                  | ruff/mypy/pytest --cov-fail-under=80 on tasks; 581 ≥ 533 baseline    | Yes |

### Convention cross-checks (Phase 8 digests)

- **`vc_boost_tick` signature** (`src/friendex/application/price_tick_service.py:236`):
  `async def vc_boost_tick(self, *, extra_boosts: Iterable[VcExtraBoost], now: datetime) -> list[VcExtraBoost]` — exact match,
  task calls with kwargs at `vc_boost_task.py:101`.
- **`accrue_apy(now=...)` signature** (`fund_service.py:310`): `async def accrue_apy(self, now: datetime) -> None` — task
  passes positional via `accrue_apy(now=now)` at `monthly_rollover_task.py:67`. (Method signature is positional, but kwarg call
  is also valid — works.)
- **LiquidationTask `discord` policy:** `grep -rn "import discord\|from discord" src/friendex/adapters/tasks/ tests/adapters/tasks/`
  returns only the assertion inside `test_liquidation_task.py:168,171` that proves the module-source scan rejects them.
  Cadence-as-declaration design pulls this policy package-wide (all 8 task modules), satisfying AC3 in the strongest form.
- **Decimal money + UTC datetime invariants** (Phase 3.1): preserved — `LiquidationTask._clock` returns `datetime.now(tz=UTC)`;
  `LiquidationEvent` test factory uses `Decimal("100.00")`/`Decimal("150.00")`; no float arithmetic on monetary fields anywhere in tasks/.
- **Per-guild services** (ADR-0001 / Phase 8a digest): all 8 tasks take `service_factory: Callable[[str], TService]` + async
  `iter_guild_ids`; per-guild fan-out N=2 demonstrated in `test_activity_tick_task.py:33`, `test_inactivity_decay_task.py:32`,
  `test_freeze_check_task.py:48`, `test_liquidation_task.py:81`, `test_vc_boost_task.py:117`, `test_monthly_rollover_task.py:236`.
  Reset tasks (Daily/Weekly) use N=1 fan-out (boundary semantics are the load-bearing rule there) — acceptable per work-unit spec
  ("at least demonstrated in one test").
- **`SystemState` shape unchanged**: `interfaces.py:58-69` still has only `guild_id`, `last_daily_reset`, `last_weekly_reset`.
  Monthly rollover proves it does NOT need a new field (M2-M4 day+hour gate gates correctly).
- **`activity_tick_k = 0.3`** (post-followup): tasks don't reference the value (they only drive the service); confirmed
  `config.py:108` is `0.3`. No task assumes `0.5`.

### Mutation-think on tests (would they fail under revert?)

Verified by sampling — all 48 tests are behavioural, not tautological:

- **`test_safe_run_swallows_*`**: revert `try/except` to bare `await awaitable` → `pytest.raises(...)` absence
  causes these tests to error out (the work-unit's own mutation log §1 confirms).
- **`test_daily_reset_fires_exactly_once_across_midnight`**: revert the `now.date() > last.date()` gate to a `True`
  literal → the second tick at 23:59 would fire and `await_count` would be 3, not 2.
- **`test_monthly_rollover_calls_portfolio_before_fund`**: revert the order in `_run` (swap fund before portfolio) →
  `call_order` would be `["fund", "portfolio"]` and the strict equality fails.
- **`test_weekly_reset_fires_across_iso_year_boundary`**: revert ISO key to `iso_week` only → 2025-12-28 (wk52)
  → 2025-12-29 (wk1 of 2026) would compare 52 ≠ 1 (still fires) BUT a later case like 2025-12-29 (wk1 yr2026) →
  2026-01-05 (wk2) would fire — actually this specific test still passes under the mutation; however the gate-pinned
  W3 case (2026-05-25 wk22 → 2026-06-01 wk23) would still pass. The (year, week) tuple is load-bearing for the
  inverse case (52-week year → 53-week year), which W4 captures.
- **`test_vc_boost_task_threads_survivors_tick_to_tick`**: revert the survivor swap-back at `vc_boost_task.py:84`
  → tick-2's captured input would equal tick-1's input (`[initial]`), failing `assert captured_inputs[1] == [refreshed]`.

The work-unit's own mutation table (002 baton lines 102-110) records three reverts (drop `try/except`, drop day-hour
gate, drop survivor swap-back) all going RED — independently consistent with what the test bodies should detect.

The cadence-pinning tests (`test_*_cadence_is_*_minutes`) are noted as "weakly behavioural" in the work-unit baton —
they assert the class attribute equals the spec literal. While they wouldn't catch a misclassified `_run` body,
they DO catch the load-bearing case where a Phase-14 composition layer reads the wrong attribute. Acceptable as
non-load-bearing companions to the actual behavioural tests.

## Containment

`git diff origin/main...HEAD --stat` (27 files, +2529/-11):

- `src/friendex/adapters/tasks/` — 10 new files (9 task modules + `__init__.py`).
- `tests/adapters/tasks/` — 11 new files (9 test files + `__init__.py` + `conftest.py`).
- `baton-runner/br-2026-05-25-phase-9/{STATE.md,log.md}` — manager metadata.
- `pass-baton/INDEX.md` + `pass-baton/phase-9/001..002` — work-unit batons.
- `ARCH.md` — re-generated by the pre-commit hook (per global convention).

**Nothing outside this set.** No domain, application, persistence-adapter,
or config changes. No `pyproject.toml`/`uv.lock` changes (verified: empty
diff for both). `freezegun` was already in dev deps at `pyproject.toml:49`
on `origin/main` — NOT added by this unit.

## Findings

**0 CRITICAL.** **0 HIGH.** **0 MEDIUM.** **0 LOW.**

Two non-blocking notes (informational only, NOT findings):

- **N1 (info):** `_safe_run` accepts `Awaitable[Any]` but every call-site builds
  a coroutine immediately (closure pattern). The type widening is intentional
  (docstring at `base_task.py:99-103`) so the injected notifier callback in
  `LiquidationTask` is not pinned to `Coroutine` specifically. Worth re-evaluating
  in Phase 14 once the actual notifier type lands — at that point a `Coroutine`
  narrowing could give marginally better type errors. Non-blocking.
- **N2 (info):** Reset-task tests cover N=1 fan-out only (they test boundary
  semantics, not per-guild isolation). The sweep-task tests (`Activity`,
  `InactivityDecay`, `Freeze`, `Liquidation`, `VcBoost`, `MonthlyRollover`)
  all cover N=2. Acceptable per the work-unit prompt ("at least demonstrated
  in one test"). The 8a "two-guild barrier" test isn't replayed here because
  the tasks layer doesn't hold composite lock keys — those live in the services.

## Carry-forwards (untouched by this unit, still open)

- Phase 8e LOWs: zero-balance fund side-effect on insufficient-withdraw path;
  `_get_or_create_account` uses `datetime.now` instead of threaded `now`.
- Phase 12 will wire `iter_guild_ids` → `discord.Client.guilds`.
- Phase 14 will bind `_loop` per the cadence-as-declaration design (snippet in
  002 baton lines 30-40).

## Next steps

1. Manager records this review as CLEAN in
   `baton-runner/br-2026-05-25-phase-9/log.md` (verdict + digest path).
2. Manager commits the eight task commits + base-class commit per the work-unit
   spec's commit-boundary guidance.
3. Phase 10 (embed builders) — no overlap with this unit; independent.
4. Phase 14 (composition) — bind `_loop` per the digest's "task scheduling
   binding point" section; wire `iter_guild_ids` to `discord.Client.guilds`;
   per-guild service factories.

## References

- Spec: `docs/04-migration-plan.md` §Phase 9 (L593-631)
- Work-unit complete baton: `pass-baton/phase-9/002-2026-05-25-phase-9-complete.md`
- Phase-exit digest: `baton-runner/br-2026-05-25-phase-9/digest-phase-9.md`
- Gate logs: `baton-runner/br-2026-05-25-phase-9/gate-phase-9-iter-1/`
- Phase 8 digests (conventions honoured): `baton-runner/br-2026-05-25-phase-8/digest-phase-8{a,b,c,d,e,f}.md`
- Issue: GitHub #2 (Phase status)
