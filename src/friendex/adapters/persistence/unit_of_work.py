"""SQLAlchemy adapter for :class:`IUnitOfWork`.

:class:`SqlUnitOfWork` opens one shared :class:`AsyncSession` for the duration
of :meth:`SqlUnitOfWork.transaction` and wraps it in ``session.begin()`` so
the whole block forms one SQL transaction. The session is exposed as a
:class:`contextvars.ContextVar` so repositories called inside the block can
opt into the shared session via :func:`current_session` instead of opening
their own; if no transaction is active, :func:`current_session` returns
``None`` and the repositories fall back to their default short-lived sessions.

This is the **minimal** persistence-side addition the unit-of-work seam needs.
The existing :class:`SqlUserRepository` / :class:`SqlFundRepository` etc.
remain unchanged for the lookup-the-session helper to be added later — the
seam is in place at the application layer immediately, and individual
repositories can be migrated to honour it incrementally without churning
their tests.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_current_session: ContextVar[AsyncSession | None] = ContextVar(
    "friendex_current_session", default=None
)


def current_session() -> AsyncSession | None:
    """Return the in-flight :class:`AsyncSession`, or ``None`` if no UoW is active.

    Repositories can call this to opt into a shared transaction when the
    application layer has opened one via :class:`SqlUnitOfWork`. The fallback
    return value of ``None`` lets a repository keep its existing
    ``async with self._sessionmaker() as session:`` path when no transaction
    is open, preserving the per-call session semantics every test relies on.
    """
    return _current_session.get()


class SqlUnitOfWork:
    """SQLAlchemy :class:`IUnitOfWork` over an :class:`async_sessionmaker`.

    Each call to :meth:`SqlUnitOfWork.transaction` opens one
    :class:`AsyncSession` and wraps the block in ``session.begin()``. The
    session is installed in a :class:`contextvars.ContextVar` so any
    repository call inside the block can pick it up via
    :func:`current_session`; on exit, the previous value (typically ``None``)
    is restored regardless of success or failure.
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Open one shared session + transaction for the block.

        On a clean exit the transaction commits; on any exception the
        transaction rolls back before the exception propagates.
        """
        async with self._sessionmaker() as session, session.begin():
            token = _current_session.set(session)
            try:
                yield session
            finally:
                _current_session.reset(token)
