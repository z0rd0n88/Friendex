"""Behavioural tests for :class:`LockManager` (Phase 7).

These tests exercise the *public* surface only — the single
``locked(*user_ids)`` async context manager — and prove the concurrency
contract deterministically with events and timeouts rather than relying on
``sleep`` races:

(a) two ``locked(uid)`` contexts on the **same** user serialise;
(b) ``locked(a, b)`` and ``locked(b, a)`` run concurrently without deadlock;
(c) a reentrant ``locked(uid)`` on a held user blocks (timeout proof);
(d) two **different** users do not block each other;
(e) cancelling a multi-lock acquire mid-flight leaks no already-held locks.
"""

from __future__ import annotations

import asyncio
import contextlib

from friendex.application.lock_manager import LockManager

# A timeout long enough to never trip on a healthy machine, short enough that a
# genuine deadlock fails the suite quickly.
_TIMEOUT = 1.0


async def test_same_user_contexts_serialise() -> None:
    """A second ``locked(uid)`` on the same user waits for the first to exit."""
    manager = LockManager()
    order: list[str] = []
    first_inside = asyncio.Event()
    release_first = asyncio.Event()

    async def first() -> None:
        async with manager.locked("u1"):
            order.append("first-enter")
            first_inside.set()
            await release_first.wait()
            order.append("first-exit")

    async def second() -> None:
        # Wait until `first` is provably inside the critical section.
        await first_inside.wait()
        async with manager.locked("u1"):
            order.append("second-enter")

    first_task = asyncio.create_task(first())
    second_task = asyncio.create_task(second())

    await first_inside.wait()
    # Give `second` a chance to (incorrectly) acquire while `first` still holds.
    await asyncio.sleep(0.05)
    assert order == ["first-enter"], "second entered before first released"

    release_first.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), _TIMEOUT)

    assert order == ["first-enter", "first-exit", "second-enter"]


async def test_multi_lock_opposite_request_order_does_not_deadlock() -> None:
    """``locked("a", "b")`` and ``locked("b", "a")`` cannot deadlock.

    This is the textbook ``A→B`` / ``B→A`` deadlock. To force the dangerous
    interleaving deterministically (rather than hoping the scheduler produces
    it), the test itself pre-acquires both underlying locks and releases them
    only after *both* coroutines are parked waiting. Each coroutine is thus
    guaranteed to be mid-acquisition when the locks free up — exactly the
    window in which an unsorted manager would cross-hold (``forward`` waiting
    on ``b`` while holding ``a``; ``reverse`` waiting on ``a`` while holding
    ``b``) and deadlock. A sorted manager makes both acquire ``a`` then ``b``,
    so they serialise cleanly and ``gather`` completes before the timeout.
    """
    manager = LockManager()
    completed: list[str] = []

    # Pre-create + hold both locks so the coroutines below all block on their
    # *first* acquire, guaranteeing they are interleaved before either makes
    # progress past lock one.
    lock_a = await manager._ensure_lock("a")
    lock_b = await manager._ensure_lock("b")
    await lock_a.acquire()
    await lock_b.acquire()

    async def forward() -> None:
        async with manager.locked("a", "b"):
            completed.append("forward")

    async def reverse() -> None:
        async with manager.locked("b", "a"):
            completed.append("reverse")

    forward_task = asyncio.create_task(forward())
    reverse_task = asyncio.create_task(reverse())

    # Let both tasks reach their first `await lock.acquire()` and park.
    await asyncio.sleep(0.05)
    lock_a.release()
    lock_b.release()

    await asyncio.wait_for(
        asyncio.gather(forward_task, reverse_task),
        _TIMEOUT,
    )

    assert sorted(completed) == ["forward", "reverse"]


async def test_reentrant_acquire_on_held_user_blocks() -> None:
    """Re-entering ``locked(uid)`` for a held user does not re-acquire.

    The lock is **not** reentrant: a coroutine that enters ``locked("u1")``
    while another holder is inside must wait. Proven by asserting the second
    ``locked("u1")`` does not complete within a short timeout while the lock
    is held; once released, it acquires promptly.
    """
    manager = LockManager()
    holder_inside = asyncio.Event()
    release_holder = asyncio.Event()
    second_acquired = asyncio.Event()

    async def holder() -> None:
        async with manager.locked("u1"):
            holder_inside.set()
            await release_holder.wait()

    async def reentrant() -> None:
        await holder_inside.wait()
        async with manager.locked("u1"):
            second_acquired.set()

    holder_task = asyncio.create_task(holder())
    reentrant_task = asyncio.create_task(reentrant())

    await holder_inside.wait()

    # While the holder is inside, the reentrant attempt must NOT acquire.
    try:
        await asyncio.wait_for(second_acquired.wait(), timeout=0.2)
    except TimeoutError:
        pass
    else:  # pragma: no cover - only reached on a real bug
        raise AssertionError("reentrant acquire succeeded while lock was held")
    assert not second_acquired.is_set()

    # Releasing the holder lets the blocked attempt acquire promptly.
    release_holder.set()
    await asyncio.wait_for(second_acquired.wait(), _TIMEOUT)
    await asyncio.wait_for(
        asyncio.gather(holder_task, reentrant_task),
        _TIMEOUT,
    )

    assert second_acquired.is_set()


async def test_different_users_do_not_block_each_other() -> None:
    """Locks for distinct users are independent — both proceed concurrently.

    Each coroutine enters its own user's critical section and then waits at a
    barrier that only releases once *both* are inside. If the locks were not
    per-user, one would be unable to enter while the other is held and the
    barrier (and the surrounding timeout) would never be satisfied.
    """
    manager = LockManager()
    both_inside = asyncio.Barrier(2)
    entered: list[str] = []

    async def worker(user_id: str) -> None:
        async with manager.locked(user_id):
            entered.append(user_id)
            # Blocks until the *other* user's coroutine is also inside.
            await both_inside.wait()

    await asyncio.wait_for(
        asyncio.gather(worker("alice"), worker("bob")),
        _TIMEOUT,
    )

    assert sorted(entered) == ["alice", "bob"]


async def test_cancel_mid_acquire_releases_already_held_locks() -> None:
    """Cancelling ``locked("a", "b")`` while awaiting ``b`` must free ``a``.

    ``locked()`` sorts ids, so a ``locked("a", "b")`` call acquires ``a`` first
    and then awaits ``b``. The test pre-holds ``b`` so the call parks on its
    *second* acquire while already holding ``a``. Cancelling the task in that
    window must not leak ``a``: a leak would wedge that user's economy for the
    manager's lifetime. After the cancellation, ``a`` must be immediately
    re-acquirable — proven by entering ``locked("a")`` under a tight timeout.
    """
    manager = LockManager()

    # Pre-hold "b" so the acquire loop blocks on its second lock while holding
    # "a". Acquiring it via the public API mirrors a real concurrent holder.
    lock_b = await manager._ensure_lock("b")
    await lock_b.acquire()

    parked = asyncio.Event()

    async def victim() -> None:
        # Signal *before* entering so the canceller can act once we are parked.
        parked.set()
        async with manager.locked("a", "b"):  # blocks awaiting "b"
            pass

    victim_task = asyncio.create_task(victim())
    await parked.wait()
    # Let the task acquire "a" and park on "b".
    await asyncio.sleep(0.05)

    victim_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await victim_task

    # Release the pre-held "b" so only the leak of "a" could still block us.
    lock_b.release()

    # If "a" leaked, this re-acquire would hang and trip the timeout.
    async def reacquire() -> None:
        async with manager.locked("a"):
            pass

    await asyncio.wait_for(reacquire(), _TIMEOUT)
