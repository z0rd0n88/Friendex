"""Atomicity seam for multi-write application-service flows.

The trading and fund services own use cases that span several repository
writes (``short`` writes the user, the fund, and the price; ``invest`` and
``withdraw`` write both the fund and the user account). A failure between
those writes corrupts money — cash is debited but the matching position
never persists, or the locked collateral is released without the position
being closed. The original Phase 8 implementation relied on the writes
happening in a "safe order" and on Python not raising mid-sequence, which
is brittle.

:class:`IUnitOfWork` is the explicit **atomicity envelope** the services
hold their multi-write critical sections inside. The Protocol exposes one
async context manager — :meth:`IUnitOfWork.transaction` — and the contract
is: every write made inside that block forms a single logical unit; on a
clean exit, every write is durable; on any exception inside the block,
every write is reverted before the exception propagates.

The Protocol is **structural**: services depend on the shape, never on a
concrete class. Two concrete implementations live elsewhere:

* The SQLAlchemy adapter (``adapters/persistence/unit_of_work.py``) opens
  one shared :class:`AsyncSession` for the scope and wraps it in
  ``session.begin()``.
* The in-memory test fakes (``tests/application/fakes/fake_repos.py``)
  snapshot every aggregate on enter and restore on exception — a
  savepoint pattern that lets the application tests verify the
  rollback-on-failure contract end-to-end without touching SQLite.

:class:`NullUnitOfWork` is the no-op fallback used by call sites that
have not yet been wired through dependency injection (the original
container construction predates this seam). It is intentionally a
yield-and-do-nothing — any service that takes a :class:`IUnitOfWork` MUST
preserve correctness even when the unit of work is null, because the
write ordering itself remains the first line of defence.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class IUnitOfWork(Protocol):
    """Application-layer atomicity envelope.

    Services hold their multi-write critical sections inside
    ``async with uow.transaction():``. The contract is binary: on clean
    exit every write is durable; on any exception every write is
    reverted before the exception propagates.
    """

    def transaction(self) -> AbstractAsyncContextManager[Any]:
        """Return the atomicity envelope for one logical transaction.

        Implementations return an async context manager. The yielded
        value (e.g. the in-flight :class:`AsyncSession` for the SQL
        adapter) is typed ``Any`` so callers that only need the
        atomicity envelope can ignore it, while implementations remain
        free to expose richer scope state.
        """
        ...


class NullUnitOfWork:
    """No-op :class:`IUnitOfWork` — yields without any atomicity guarantee.

    The default fallback for call sites not yet wired through the
    transactional seam. Code that uses :class:`IUnitOfWork` MUST be
    correct even with this implementation, because the write ordering
    inside the critical section is the first line of defence.
    """

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """Yield immediately; no real atomicity is provided."""
        yield
