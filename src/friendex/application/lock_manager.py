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

**Composite lock keys.** Every per-guild service formerly carried its own
``_lock_key`` method that returned ``f"{self._guild_id}:{user_id}"`` to keep
two guilds' economies from serialising on the same user (ADR-0001
per-guild isolation). The canonical formatter now lives here as
:func:`guild_lock_key`; services call it directly so a future key-shape
tweak lands once and propagates to every call site (#82 H16).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def guild_lock_key(guild_id: str, user_id: str) -> str:
    """Return the composite ``"<guild_id>:<user_id>"`` lock key (ADR-0001).

    The application :class:`LockManager` is shared across every per-guild
    service scope, so the lock keys must encode the guild explicitly — a
    plain ``user_id`` would serialise the same person across two unrelated
    guilds. This helper is the single source of truth for the key shape;
    previously nine services declared a private ``_lock_key`` method that
    re-implemented this f-string by hand (#82 H16), making any future
    change (e.g. a separator swap, a hash-based scheme) impossible to land
    without touching every service in lockstep.
    """
    return f"{guild_id}:{user_id}"


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

        **Cancellation safety, accurately (#84 L).** The acquire loop has
        two distinct phases with different cancellation profiles:

        1. **Pre-``try`` lock creation.** ``[await self._ensure_lock(uid)
           for uid in ids]`` runs *before* the ``try`` block. A
           cancellation here can land on any individual ``_ensure_lock``
           await. Lock creation itself only takes the meta lock briefly and
           does NOT acquire any per-user lock, so a cancellation during
           this phase never leaves a per-user lock held — the worst case
           is that some empty :class:`asyncio.Lock` instances were lazily
           created and are now installed in ``self._locks``. Those will
           simply be reused by the next caller; nothing leaks.

        2. **Inside-``try`` acquisition.** Each per-user lock is acquired
           one at a time and recorded in ``acquired`` **only after**
           ``await lock.acquire()`` returns successfully. A cancellation
           (or exception) while awaiting the N-th ``lock.acquire()``
           therefore releases the first N-1 already-held locks in the
           ``finally`` — the N-th lock cannot be in ``acquired`` because
           the recording append did not run. This is the load-bearing
           guarantee tested by
           ``test_cancel_mid_acquire_releases_already_held_locks``.

        The ``yield``-and-cleanup phase is symmetric: any exception inside
        the user's ``async with`` block, or a cancellation that lands
        during the held window, releases every held lock via the
        ``finally``. The ``finally`` itself never raises; in particular a
        spurious ``asyncio.CancelledError`` thrown into a release path is
        ignored by ``Lock.release`` (synchronous), so the chain runs to
        completion.
        """
        ids = sorted(set(user_ids))
        locks = [await self._ensure_lock(uid) for uid in ids]
        acquired: list[asyncio.Lock] = []
        try:
            for lock in locks:
                await lock.acquire()
                acquired.append(lock)
            yield
        finally:
            for lock in reversed(acquired):
                lock.release()
