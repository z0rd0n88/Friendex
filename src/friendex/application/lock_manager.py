"""Per-user :mod:`asyncio` lock coordination for the application layer.

:class:`LockManager` is a process-local singleton (passed by dependency
injection to the application services) that serialises mutations affecting a
given user's economy. Each user id maps to its own :class:`asyncio.Lock`, so
unrelated users never contend.

**Deadlock safety.** The only public entry point, :meth:`LockManager.locked`,
acquires *multiple* user locks in a single ``async with`` block. To guarantee a
consistent global acquisition order across coroutines — and therefore prevent
the classic ``A→B`` / ``B→A`` deadlock — the requested ids are de-duplicated
and **sorted** before acquisition, and released in the reverse order in a
``finally`` block. Two callers asking for ``("a", "b")`` and ``("b", "a")``
thus acquire ``a`` then ``b`` in both cases and can never deadlock.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class LockManager:
    """Hands out and coordinates per-user :class:`asyncio.Lock` instances.

    Locks are created lazily on first use and cached for the manager's
    lifetime. Creation is guarded by an internal meta lock so concurrent
    coroutines requesting the same brand-new user id share one lock instead of
    racing to create competing ones.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock: asyncio.Lock = asyncio.Lock()

    async def _ensure_lock(self, uid: str) -> asyncio.Lock:
        """Return the lock for ``uid``, creating it under the meta lock.

        Uses :meth:`dict.setdefault` so that even if two coroutines reach this
        point concurrently for the same previously-unseen id, exactly one
        :class:`asyncio.Lock` is installed and returned to both.
        """
        async with self._meta_lock:
            return self._locks.setdefault(uid, asyncio.Lock())

    @asynccontextmanager
    async def locked(self, *user_ids: str) -> AsyncIterator[None]:
        """Acquire the locks for ``user_ids`` for the duration of the block.

        Ids are sorted and de-duplicated so acquisition order is deterministic
        (deadlock prevention); locks are released in reverse order in a
        ``finally`` so a failure inside the block never leaks a held lock.
        """
        ids = sorted(set(user_ids))
        locks = [await self._ensure_lock(uid) for uid in ids]
        for lock in locks:
            await lock.acquire()
        try:
            yield
        finally:
            for lock in reversed(locks):
                lock.release()
