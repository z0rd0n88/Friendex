# Pass-Baton: Phase 7 (LockManager) merged — carry-forward into Phase 8

**Date:** 2026-05-25
**Scope:** phase-7-locks
**Branch:** main (Phase 7 work merged; this baton written on `docs/phase-7-handoff`)
**Worktree:** /home/alex/Friendex
**HEAD:** 97b505e feat(phase-7): concurrency primitives — LockManager (#38)

## Where things stand

Phase 7 (Concurrency primitives) is **done and merged** — PR #38 squash-merged to
`main` as `97b505e`, Phase 7 box ticked in issue #2. `LockManager` lives at
`src/friendex/application/lock_manager.py` with the single public async context
manager `locked(*user_ids)` (no public `acquire()` — deliberately superseded the
docs/02 snippet, user-confirmed). Built via baton-runner run
`br-2026-05-25-phase-7`: TDD work unit → independent review unit, VERDICT CLEAN,
gate green, **100% coverage** on `lock_manager.py`, all four criteria
mutation-verified, no new dependencies. **Phase 8a is the next phase.**

## Next steps

1. Start Phase 8a (`activity_service` + `voice_ping_service`) — `/baton-runner`
   reads `docs/04-migration-plan.md` §Phase 8a. Feed it the Phase 7 digest at
   `baton-runner/br-2026-05-25-phase-7/digest-phase-7.md` so it inherits the
   `LockManager` public surface + the conventions below (a fresh baton-runner run
   gets a new run-id dir and will NOT auto-discover the phase-7 digest).
2. **Fix the deferred MEDIUM while wiring the first lock caller** (see below) —
   it belongs in Phase 8a per the review, not a separate PR.

## Open questions / risks

- **MEDIUM (deferred from Phase 7 review → fix in Phase 8a):** `locked()`'s
  acquire loop sits *outside* the `try/finally` in
  `src/friendex/application/lock_manager.py`. If a coroutine is cancelled while
  awaiting the N-th lock, the first N-1 already-held locks leak (never released).
  Harmless today (no caller cancels mid-acquire), but Phase 8a wires the first
  real callers. Fix: track `acquired` locks as you take them and release them in
  `finally`. Exact patch is in
  `baton-runner/br-2026-05-25-phase-7/baton-phase-7-review-iter-1.md` (MEDIUM
  finding). Add a test that cancels a task mid-acquire and asserts no lock leaks.
- **LOW (optional):** no test for duplicate ids in one `locked(a, a)` call
  (dedupe via `sorted(set(...))` is untested); existing tests reach into the
  private `_ensure_lock` — prefer black-box assertions through `locked()`.

## Conventions Phase 8a+ services MUST honor (from the digest)

- Take locks ONLY via `async with lock_manager.locked(...)`; never touch
  `_locks` / `_ensure_lock`.
- `LockManager` is a **process-local singleton** passed by DI (constructed once
  at Phase 14 wiring) — never `LockManager()` per call (fresh instances share no
  locks → zero serialisation).
- Acquire **all** needed ids in ONE `locked(buyer, target, ...)` call — one call
  per critical section. Nesting `locked()` blocks can cross-order and deadlock.
- The lock is **NOT reentrant** — a coroutine inside `locked("u1")` must not
  re-enter `locked("u1")`.
- Locks are per-id (opaque strings). For guild-scoped serialisation later, key
  with a composite like `f"{guild_id}:{user_id}"`.

## References

- PRs: #38 (merged)
- Issues: #2 (Phase 7 ticked; Phase 8a–8f next)
- Docs: `docs/04-migration-plan.md` §Phase 7 / §Phase 8a; `docs/02-target-architecture.md` §Per-User Locks
- Digest (full surface + decisions): `baton-runner/br-2026-05-25-phase-7/digest-phase-7.md`
- Review findings: `baton-runner/br-2026-05-25-phase-7/baton-phase-7-review-iter-1.md`
- Code: `src/friendex/application/lock_manager.py`; tests `tests/application/test_lock_manager.py`
