# Pass-Baton: Phase 9 — Background Tasks COMPLETE

**Date:** 2026-05-25
**Scope:** phase-9
**Branch:** feat/phase-9-tasks
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-9
**HEAD:** 68019b3 chore(baton-runner): init phase-9 run (un-committed work on top)

## Where things stand

Phase 9 acceptance criteria AC0–AC10 all met. All eight background-task
wrappers live under `src/friendex/adapters/tasks/`; the package contains a
`BackgroundTask` abstract base (swallow-and-log contract + lifecycle), one
module per task, and a public `__init__.py` re-exporting them all. The
`tests/adapters/tasks/` suite is 48 tests covering all ACs at **100%
coverage on the package** (gate is 80%). The repo-wide gate stays green:
**581 pytest passing** (up from the 533 Phase 8 baseline, +48 new tests),
ruff/format/mypy all clean — see `/tmp/p9-self-check/` log dir.

## Key design decision: cadence-as-declaration

Tasks declare cadence as a class attribute (`interval_minutes` /
`interval_hours`); the Phase 14 composition layer is expected to read those
and wrap each task's `_run` in a `discord.ext.tasks.loop(...)`, binding the
resulting `Loop` to `self._loop`. **This keeps the entire `adapters/tasks/`
package free of `discord` imports**, satisfying AC3's "LiquidationTask must
not import `discord`" in the strongest possible way (uniformly across all
tasks).

Phase 14 wiring snippet (for whoever picks this up):

```python
from discord.ext import tasks as discord_tasks
def attach(task):
    if task.interval_minutes:
        task._loop = discord_tasks.loop(minutes=task.interval_minutes)(task._run)
    else:
        task._loop = discord_tasks.loop(hours=task.interval_hours)(task._run)
    return task
```

## Per-guild fan-out design (decided: A)

Each task takes:
- `service_factory: Callable[[str], TService]` — builds a per-guild service.
- `iter_guild_ids: Callable[[], Awaitable[Iterable[str]]]` — yields current
  guild IDs (the bot's guild registry; Phase 14 will wire it to
  `discord.Client.guilds`).

Each `_run()` walks the guilds, builds a per-guild service, and runs the
service method through `_safe_run`. Tests verify N=2 fan-out works for all
sweep tasks (`g1`, `g2` in every per-guild test).

`MonthlyRolloverTask` is the only task that takes TWO factories
(`portfolio_service_factory` + `fund_service_factory`) since it orchestrates
two services per fire.

`DailyResetTask`/`WeeklyResetTask` also take `system_state_repo: ISystemStateRepo`
because the boundary check + state-advance bookkeeping lives in the task itself
(the application services don't know about scheduling).

`VcBoostTask` owns the per-guild `dict[str, list[VcExtraBoost]]` store (Phase 8b
§5 storage-by-parameter); expose `set_store_for_guild` / `get_store_for_guild`
for composition + tests.

`LiquidationTask` takes a `notifier: Callable[[LiquidationEvent], Awaitable[None]]`
per AC3 — generic callable so the task module never imports `discord`.

## Acceptance criteria results

| AC  | Status | Module                                | Tests |
|-----|--------|---------------------------------------|-------|
| AC0 | GREEN  | `base_task.py`                        | 9     |
| AC1 | GREEN  | `activity_tick_task.py`               | 3     |
| AC2 | GREEN  | `inactivity_decay_task.py`            | 3     |
| AC3 | GREEN  | `liquidation_task.py` (no `discord`)  | 5     |
| AC4 | GREEN  | `freeze_check_task.py`                | 3     |
| AC5 | GREEN  | `vc_boost_task.py` (storage threading)| 4     |
| AC6 | GREEN  | `daily_reset_task.py` (freezegun)     | 6     |
| AC7 | GREEN  | `weekly_reset_task.py` (ISO year+wk)  | 7     |
| AC8 | GREEN  | `monthly_rollover_task.py` (gate+APY) | 8     |
| AC9 | GREEN  | 8 test files + `__init__.py`          | 48    |
| AC10| GREEN  | All verification gates pass           | —     |

## TDD log — RED captures

Each AC's first test was RED-verified (`ModuleNotFoundError` at the import
line) before implementation:

| AC  | RED location                                                                       |
|-----|------------------------------------------------------------------------------------|
| AC0 | `tests/adapters/tasks/test_base_task.py:18`                                        |
| AC1 | `tests/adapters/tasks/test_activity_tick_task.py:12`                               |
| AC2 | `tests/adapters/tasks/test_inactivity_decay_task.py:13`                            |
| AC3 | `tests/adapters/tasks/test_liquidation_task.py:28`                                 |
| AC4 | `tests/adapters/tasks/test_freeze_check_task.py:23`                                |
| AC5 | `tests/adapters/tasks/test_vc_boost_task.py:28`                                    |
| AC6 | `tests/adapters/tasks/test_daily_reset_task.py:34`                                 |
| AC7 | `tests/adapters/tasks/test_weekly_reset_task.py:26`                                |
| AC8 | `tests/adapters/tasks/test_monthly_rollover_task.py:36`                            |

## Mutation verification (3 mutations, all RED on revert)

| Mutation                                            | Tests that broke                                            |
|-----------------------------------------------------|-------------------------------------------------------------|
| Revert `_safe_run` to re-raise (drop try/except)    | 3 in `test_base_task.py` (`test_safe_run_swallows_*`)       |
| Drop day/hour gate from `MonthlyRolloverTask._run`  | 3 in `test_monthly_rollover_task.py` (`no_op_when_*`)       |
| Drop survivor swap-back in `VcBoostTask._run`       | 3 in `test_vc_boost_task.py` (threading + per-guild + swallow) |

Each mutation was reverted after verifying RED.

## Verification gate results

`scripts/gate.sh /tmp/p9-self-check/` — **GATE: PASS**:

```
PASS pytest      (581 passing, was 533 baseline + 48 new)
PASS ruff-check  (src tests alembic)
PASS ruff-format (src tests alembic)
PASS mypy        (src/friendex)
```

Phase-9 sub-gate:

```
uv run ruff check src/friendex/adapters/tasks/ tests/adapters/tasks/
  → All checks passed!
uv run mypy src/friendex/adapters/tasks/
  → Success: no issues found in 10 source files
uv run pytest tests/adapters/tasks/ -v --cov=src/friendex/adapters/tasks --cov-fail-under=80
  → 48 passed; Required coverage 80% reached. Total coverage: 100.00%
```

## Files created (per spec)

- `src/friendex/adapters/tasks/base_task.py` (AC0)
- `src/friendex/adapters/tasks/activity_tick_task.py` (AC1)
- `src/friendex/adapters/tasks/inactivity_decay_task.py` (AC2)
- `src/friendex/adapters/tasks/liquidation_task.py` (AC3)
- `src/friendex/adapters/tasks/freeze_check_task.py` (AC4)
- `src/friendex/adapters/tasks/vc_boost_task.py` (AC5)
- `src/friendex/adapters/tasks/daily_reset_task.py` (AC6)
- `src/friendex/adapters/tasks/weekly_reset_task.py` (AC7)
- `src/friendex/adapters/tasks/monthly_rollover_task.py` (AC8)
- `tests/adapters/tasks/__init__.py`
- `tests/adapters/tasks/conftest.py` (fake_system_state_repo fixture)
- `tests/adapters/tasks/test_base_task.py` (9 tests — AC0 + lifecycle)
- `tests/adapters/tasks/test_activity_tick_task.py` (3 tests)
- `tests/adapters/tasks/test_inactivity_decay_task.py` (3 tests)
- `tests/adapters/tasks/test_liquidation_task.py` (5 tests)
- `tests/adapters/tasks/test_freeze_check_task.py` (3 tests)
- `tests/adapters/tasks/test_vc_boost_task.py` (4 tests)
- `tests/adapters/tasks/test_daily_reset_task.py` (6 tests)
- `tests/adapters/tasks/test_weekly_reset_task.py` (7 tests)
- `tests/adapters/tasks/test_monthly_rollover_task.py` (8 tests)
- `pass-baton/phase-9/001-2026-05-25-phase-9-work.md` (in-flight)
- `pass-baton/phase-9/002-2026-05-25-phase-9-complete.md` (this file)

## Files modified (per spec, only `__init__.py` allowed)

- `src/friendex/adapters/tasks/__init__.py` — added re-exports for clean
  import surface.

NO modifications to domain, application, persistence adapters, config, or
any test outside the new `tests/adapters/tasks/` tree.

## Open follow-ups (carry-forward for review unit)

- **Carry-forwards from Phase 8 still apply** (not touched by this unit):
  Phase 8e LOWs (zero-balance fund side-effect; `_get_or_create_account`
  uses `datetime.now`).
- **Phase 12 will wire** `iter_guild_ids` to `discord.Client.guilds`; the
  task contract treats it as an opaque async callable.
- **Phase 14 will bind `_loop`** by reading `interval_minutes` /
  `interval_hours` and wrapping `_run` in `discord.ext.tasks.loop(...)`.
  Tests do not exercise the actual loop — only the per-tick body via
  `task._run()`, which is what `discord.ext.tasks` invokes on each tick.
- **State-advance ordering** in `DailyResetTask`/`WeeklyResetTask`: the
  service call happens BEFORE state upsert, so a service failure leaves
  state unadvanced and the next tick retries. Documented in module
  docstrings + test `D5` / `W6`.

## Next steps

1. **For the review unit:** verify the cadence-as-declaration design is
   acceptable (per the Phase 9 AC3 stricter reading — extending the
   no-`discord`-import policy to ALL tasks rather than only liquidation).
   If unacceptable, switch to per-task `tasks.loop(...)` decorations
   inside each module (would require the test cadence checks to read
   `task._loop.minutes` and the module test for `LiquidationTask` to allow
   `from discord.ext import tasks`).
2. **Manager will commit** the eight task commits + base-class commit per
   the spec's "Commit boundary guidance: Eight commits, one per task file
   with its test, plus a base-class commit first." This baton-runner unit
   does NOT commit (manager owns git).
3. **For Phase 10:** Discord embed builders — no overlap with this unit.

## References

- Spec: `docs/04-migration-plan.md` §Phase 9 (L593-631)
- Predecessor pass-baton: `pass-baton/phase-9/001-2026-05-25-phase-9-work.md`
- Phase 8 digests (conventions honoured):
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8a.md` (guild_id ctor, lock keys)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8b.md` (vc_boost storage-by-parameter, activity_tick_k 0.3)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8c.md` (RMW lock discipline)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8d.md` (per-user-`locked()`-inside-loop, lockless reads)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8e.md` (accrue_apy retry-safe)
  - `baton-runner/br-2026-05-25-phase-8/digest-phase-8f.md` (LiquidationTask notifier injection)
- Issue: GitHub #2 (Phase status)
- Code surface produced (line counts approximate):
  - `src/friendex/adapters/tasks/base_task.py` (~115 lines)
  - `src/friendex/adapters/tasks/{activity_tick,inactivity_decay,freeze_check}_task.py` (~50 lines each)
  - `src/friendex/adapters/tasks/{liquidation,vc_boost,daily_reset,weekly_reset,monthly_rollover}_task.py` (~80-110 lines each)
  - 9 test files (~50-220 lines each) + `conftest.py`
