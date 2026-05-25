# Phase 7 Digest — Concurrency Primitives (LockManager)

**Status:** CLEAN. Gate green, 100% coverage, criteria mutation-verified.

## Public surface added

`src/friendex/application/lock_manager.py`:

```python
class LockManager:
    def __init__(self) -> None: ...
    @asynccontextmanager
    async def locked(self, *user_ids: str) -> AsyncIterator[None]: ...
```

- `locked(*user_ids)` is the ONLY public entry point. There is NO public
  `acquire()` (deliberately superseded the docs/02 snippet; user-confirmed).
- Lock creation is private: `_ensure_lock(uid)` via `dict.setdefault` under an
  internal `_meta_lock` — do not call it from product code.

## Conventions Phase 8a (and all later service phases) MUST honor

1. **Take locks via `async with lock_manager.locked(...)`** — never reach into
   `_locks` / `_ensure_lock`. Wrap each mutating use case (trade, fund, daily,
   liquidation) so it serialises on the affected user id(s).
2. **Pass `LockManager` by dependency injection.** It is a process-local
   singleton constructed once (at composition / Phase 14 wiring) and handed to
   services. Do NOT instantiate per call — a fresh `LockManager()` shares no
   locks and gives zero serialisation.
3. **Acquire ALL needed user ids in ONE `locked(buyer, target, ...)` call.**
   Never nest `locked()` blocks for the same manager — `locked()` sorts ids so a
   single call is deadlock-safe, but two separate nested calls can cross-order
   and deadlock. One call per critical section.
4. **The lock is NOT reentrant.** A coroutine already inside `locked("u1")` must
   not re-enter `locked("u1")` — it will block forever. Compose so each logical
   operation takes its locks once at the top.
5. **Locks are per-user, not per-guild.** ids are opaque strings. If a future
   phase needs guild-scoped serialisation, key with a composite string
   (e.g. `f"{guild_id}:{user_id}"`); the manager treats it as just an id.

## Known follow-up (non-blocking, from review)

- `locked()`'s acquire loop sits outside the `try/finally`; a cancellation while
  awaiting the N-th lock leaks the first N-1 held locks. Harmless today (no
  caller cancels mid-acquire). Phase 8a should fix when it wires the first real
  caller: track `acquired` locks and release them in `finally`. See
  `baton-phase-7-review-iter-1.md` MEDIUM finding for the patch.

## No new dependencies

stdlib `asyncio` + `contextlib.asynccontextmanager` only. pyproject/uv.lock
unchanged.
