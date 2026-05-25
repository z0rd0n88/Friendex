# Review Baton: Phase 7 — Concurrency Primitives (LockManager)

**Date:** 2026-05-25
**Scope:** br-2026-05-25-phase-7 (review iter 1)
**Branch:** feat/phase-7-locks
**Worktree:** /home/alex/Friendex/.claude/worktrees/br-2026-05-25-phase-7
**HEAD:** 8cd6bdc chore(phase-7): refresh ARCH.md for lock_manager
**Run base:** 93b098a (origin/main)
**Reviewer:** independent of implementer

## VERDICT: CLEAN

Gate is green, all four acceptance criteria are independently mutation-verified
(not tautological), coverage is 100% (>=95% required), no new dependencies, and
there are no CRITICAL or HIGH findings.

## Gate result (scripts/gate.sh ... gate-phase-7-iter-1/)

```
PASS pytest        (uv run pytest — full suite)
PASS ruff-check    (src tests alembic)
PASS ruff-format   (src tests alembic)
PASS mypy          (src/friendex)
GATE: PASS  (exit 0)
```

Per-module coverage (dotted form, as the work baton notes the spec's slashed
`--cov` path mis-spells the module): `src/friendex/application/lock_manager.py`
21 stmts, 0 miss, 4 branch, 0 BrPart, **100%** — "Required test coverage of 95%
reached."

## Acceptance criteria — independently mutation-verified

I did NOT trust the work baton's RED claims; I re-ran each mutation myself
(against a private copy / git-restored worktree, product code untouched).

- **(a) same-user contexts serialise** — `test_same_user_contexts_serialise`.
  Mutation "yield only, no acquire/release" → test FAILS. ✓ Real.
- **(b) locked(a,b) vs locked(b,a) no deadlock** —
  `test_multi_lock_opposite_request_order_does_not_deadlock`. Mutation
  "dedupe-without-sort (`list(dict.fromkeys(...))`)" → test FAILS with
  `TimeoutError` (the A→B/B→A cross-hold). ✓ Real — the sort in `locked()` is
  load-bearing and the test catches its removal. The test pre-holds both
  underlying locks and parks both coroutines, forcing the dangerous interleave
  deterministically rather than hoping for a scheduler race.
- **(c) reentrant attempt on held user blocks** —
  `test_reentrant_acquire_on_held_user_blocks`. Mutation "yield only" → test
  FAILS (`AssertionError: reentrant acquire succeeded while lock was held`).
  Proven via a positive timeout (must NOT acquire within 0.2s) + a release-then-
  acquire-promptly assertion. ✓ Real.
- **(d) different users do not block each other** —
  `test_different_users_do_not_block_each_other`. Mutation "all ids map to one
  shared `"ALL"` lock" → test FAILS with `TimeoutError`; original PASSES.
  Uses `asyncio.Barrier(2)` so both must be inside simultaneously — a shared
  lock can never satisfy it. ✓ Real; meta-lock per-uid creation is load-bearing.

Meta-lock safety: `_ensure_lock` uses `dict.setdefault` under `self._meta_lock`,
so concurrent first-touch of a new uid yields a single shared lock — correct.

## Findings

### MEDIUM

- **[MEDIUM] Partially-acquired locks leak if the acquire loop is cancelled —
  `src/friendex/application/lock_manager.py:60-66`.**
  Impact: in `locked(*ids)` the acquire loop (`for lock in locks: await
  lock.acquire()`) runs OUTSIDE the `try/finally`. If the coroutine is cancelled
  (or, hypothetically, an exception is raised) while awaiting acquisition of the
  N-th lock, the first N-1 already-held locks are never released → those users'
  economies are wedged for the manager's lifetime. Multi-lock callers (e.g. a
  trade touching buyer + target) acquire two locks, so the window is real once
  Phase 8a+ services start cancelling tasks (timeouts, shutdown).
  Fix: track acquired locks and release them on failure, e.g.
  ```python
  acquired: list[asyncio.Lock] = []
  try:
      for lock in locks:
          await lock.acquire()
          acquired.append(lock)
      yield
  finally:
      for lock in reversed(acquired):
          lock.release()
  ```
  Note: NOT blocking — the migration-plan spec describes exactly the
  acquire-then-`try/yield/finally` shape, the acceptance criteria do not require
  cancellation-safety, and no current caller cancels mid-acquire (no services
  exist yet). Recommend addressing when Phase 8a wires the first real caller, or
  fast-following. Severity capped at MEDIUM for that reason.

### LOW

- **[LOW] No test exercises the multi-lock dedupe path with a duplicate id —
  `tests/application/test_lock_manager.py`.** `locked()` does `sorted(set(...))`;
  the `set()` dedupe is covered incidentally (single-id tests) but there is no
  test that `locked("u1", "u1")` doesn't self-deadlock. Coverage is already
  100% so this is purely a behavioural-intent gap. Fix: add a one-liner asserting
  `async with manager.locked("u1", "u1")` enters without hanging.

- **[LOW] Tests reach into the private `_ensure_lock` —
  `tests/application/test_lock_manager.py:78-81`.** The deadlock test calls
  `manager._ensure_lock("a")` to pre-hold locks. This couples the test to a
  private helper; if `_ensure_lock` is renamed/inlined the test breaks for a
  non-behavioural reason. Acceptable (it's the cleanest way to force the
  interleave deterministically) but worth a comment flagging the intentional
  private access. No change required.

## Dependencies

**No new dependencies.** `git diff 93b098a...HEAD -- pyproject.toml uv.lock` is
empty. Implementation uses only stdlib `asyncio` + `contextlib.asynccontextmanager`.

## Files changed (non-doc)

- `src/friendex/application/lock_manager.py` (new, 66 lines)
- `tests/application/test_lock_manager.py` (new, 178 lines, 4 async tests)
- `ARCH.md` — auto-generated tree refresh only (hook output; no curated edits)

## Security review

ecc-security-review checklist applied: N/A across the board. Pure in-process
async concurrency primitive — no secrets, user input, SQL, network, auth,
deserialization, or sensitive logging. The lock-leak-on-cancel item is a
robustness concern, not a security one.

## References

- Plan: `docs/04-migration-plan.md` "## Phase 7 — Concurrency Primitives"
- Spec ref (API-shape superseded by user override; no public `acquire()`):
  `docs/02-target-architecture.md` "### Per-User Locks"
- Work baton: `baton-runner/br-2026-05-25-phase-7/baton-phase-7-work.md`
- Gate logs: `baton-runner/br-2026-05-25-phase-7/gate-phase-7-iter-1/`
- Issue: #2 (phase status)
