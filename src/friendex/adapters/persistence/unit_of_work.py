"""SQLAlchemy adapter for :class:`IUnitOfWork`.

:class:`SqlUnitOfWork` opens one shared :class:`AsyncSession` for the duration
of :meth:`SqlUnitOfWork.transaction` and wraps it in ``session.begin()`` so
the whole block forms one SQL transaction. The session is exposed as a
:class:`contextvars.ContextVar` so repositories called inside the block can
opt into the shared session via :func:`current_session` instead of opening
their own; if no transaction is active, :func:`current_session` returns
``None`` and the repositories fall back to their default short-lived sessions.

**Repository migration status.** :class:`SqlUserRepository`,
:class:`SqlFundRepository`, :class:`SqlPriceRepository`, and
:class:`SqlTradeCooldownRepository` honour the shared session on both
read and write paths. When a UoW is active every method calls
:func:`current_session`, enrols its statements into the shared session,
and lets the UoW own the commit; when no UoW is active each falls through
to its existing per-call session path so every legacy caller (background
tasks, single-write code paths, fakes-only tests) keeps working unchanged.

**Why reads must honour the shared session too.** SQLite's
:class:`~sqlalchemy.pool.StaticPool` (the default for the in-memory
test engine) shares one DBAPI connection across every session — so a
per-call ``async with self._sessionmaker() as session:`` opened *inside*
an active UoW issues its own ``BEGIN``/``ROLLBACK`` on the same
connection, silently rolling back the outer UoW's pending writes. The
production async engine pools differ in principle but the read-isolation
contract is the same: a UoW transaction MUST see its own in-flight
writes when it re-reads, and only the shared session can provide that
read-your-writes guarantee. The read paths therefore route through the
shared session whenever one is active.
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
