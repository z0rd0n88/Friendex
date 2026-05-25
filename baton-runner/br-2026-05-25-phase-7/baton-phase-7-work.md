# Baton: Phase 7 — Concurrency Primitives (LockManager) — COMPLETE

**Date:** 2026-05-25
**Branch:** feat/phase-7-locks
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-7
**HEAD:** 93b098a feat(phase-6): persistence — repositories + JSON→SQLite migrator (#37)

## Scope (delivered)

`LockManager` per docs/04-migration-plan.md "Phase 7" with the authoritative
API-shape decision: ONLY public async CM `locked(*user_ids)`; NO public
`acquire()` — inlined as private `_ensure_lock(uid)` using `setdefault` under
the meta lock. `locked()` sorts+dedupes ids (`sorted(set(user_ids))`), acquires
in order, releases reversed in `finally`.

Files created (only two — no other modules touched):
- src/friendex/application/lock_manager.py  (21 stmts, 100% covered)
- tests/application/test_lock_manager.py     (4 async tests)
- (tests/application/__init__.py already existed from Phase 6 — untouched)

## TDD evidence (RED→GREEN per acceptance criterion)

Each test was confirmed to genuinely detect its failure mode by temporarily
breaking the implementation, capturing the RED, then restoring to GREEN.

- [x] (a) same-user contexts serialise — `test_same_user_contexts_serialise`
  - RED: `ModuleNotFoundError: No module named 'friendex.application.lock_manager'`
  - GREEN after implementing LockManager.

- [x] (b) locked(a,b) vs locked(b,a) concurrent, no deadlock —
      `test_multi_lock_opposite_request_order_does_not_deadlock`
  - Test pre-holds both underlying locks, parks both coroutines mid-acquire,
    then releases — forcing the A→B/B→A interleaving deterministically.
  - RED (impl with `ids = list(dict.fromkeys(user_ids))`, dedupe but NO sort):
    `TimeoutError` (in 1.09s — the cross-hold deadlock).
  - GREEN after restoring `ids = sorted(set(user_ids))`.

- [x] (c) reentrant attempt on held user blocks —
      `test_reentrant_acquire_on_held_user_blocks`
  - Asserts the second `locked("u1")` does NOT acquire within 0.2s while held,
    then acquires promptly once released.
  - RED (impl with acquire/release skipped, `yield` only):
    `AssertionError: reentrant acquire succeeded while lock was held`.
  - GREEN after restoring acquire/release.

- [x] (d) two different users do not block each other —
      `test_different_users_do_not_block_each_other`
  - Both users enter their own critical section and meet at `asyncio.Barrier(2)`;
    only per-user locks let both be inside simultaneously.
  - RED (impl with all ids mapped to one shared `"ALL"` lock): `TimeoutError`
    (barrier never satisfied — second user can't enter).
  - GREEN after restoring per-uid `_ensure_lock(uid)`.

## Verification results (run locally)

- `uv run ruff check src/friendex/application/lock_manager.py tests/application/test_lock_manager.py` → All checks passed!
- `uv run ruff format --check .` → 59 files already formatted
- `uv run mypy src/friendex/application/lock_manager.py` → Success: no issues found in 1 source file
- `uv run pytest tests/application/test_lock_manager.py -v --cov=friendex.application.lock_manager --cov-fail-under=95`
  → 4 passed; lock_manager.py 21 stmts, 0 miss, 4 branch, 0 BrPart, **100%**; "Required test coverage of 95% reached."
- `bash scripts/gate.sh` → GATE: PASS (pytest 373 passed, ruff-check, ruff-format, mypy all PASS)

## ⚠️ Coverage-arg note for the reviewer (not a code defect)

The verification target as literally written uses
`--cov=src/friendex/application/lock_manager` (a slashed path). coverage.py
treats that as a *module name* it tries to import, fails, and reports
"Module ... was never imported" → 0% → gate fails. The package is imported as
`friendex.application.lock_manager`; using the **dotted module form**
(`--cov=friendex.application.lock_manager`) reports **100%** (shown above).
The committed `scripts/gate.sh` runs whole-suite `uv run pytest` with no
per-module cov gate, so it PASSES regardless. No source/test change is needed —
this is purely the cov argument spelling.

## Decisions / no new deps

- Async test convention followed: bare `async def test_*` (pyproject
  `asyncio_mode = "auto"`, pytest-asyncio) — matches test_interfaces.py. No new
  runner, no decorators.
- `AsyncIterator` import is under `if TYPE_CHECKING:` (ruff TC003).
- No new dependencies (stdlib `asyncio` + `contextlib.asynccontextmanager`).

## References

- Plan: docs/04-migration-plan.md "## Phase 7 — Concurrency Primitives"
- Ref snippet (overridden by API-shape decision): docs/02-target-architecture.md "### Per-User Locks"
- Issue: #2 (phase status)
