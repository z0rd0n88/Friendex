"""Tests for the :class:`SqlUnitOfWork` SQLAlchemy adapter.

Pins the seam:

* On a clean exit the wrapped block commits.
* On an exception inside the block the transaction rolls back.
* The in-flight :class:`AsyncSession` is exposed via :func:`current_session`
  so repositories called inside the block can opt into the shared
  transaction; the previous (typically ``None``) value is restored after
  the block exits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import text

from friendex.adapters.persistence.db import build_engine, build_sessionmaker
from friendex.adapters.persistence.unit_of_work import (
    SqlUnitOfWork,
    current_session,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite engine for the UoW tests."""
    eng = build_engine("sqlite+aiosqlite:///:memory:")
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """An :class:`async_sessionmaker` bound to the in-memory engine."""
    return build_sessionmaker(engine)


async def test_transaction_exposes_session_via_current_session(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The shared :class:`AsyncSession` is installed in the ContextVar."""
    uow = SqlUnitOfWork(sessionmaker)
    assert current_session() is None
    async with uow.transaction() as session:
        assert current_session() is session
    assert current_session() is None


async def test_transaction_commits_on_clean_exit(
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """A clean exit commits every write the block made."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY)"))

    uow = SqlUnitOfWork(sessionmaker)
    async with uow.transaction() as session:
        await session.execute(text("INSERT INTO marker (id) VALUES (1)"))

    async with sessionmaker() as session:
        result = await session.execute(text("SELECT id FROM marker"))
        assert result.scalars().all() == [1]


async def test_transaction_rolls_back_on_exception(
    engine: AsyncEngine,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """An exception inside the block rolls back every write."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE marker (id INTEGER PRIMARY KEY)"))

    uow = SqlUnitOfWork(sessionmaker)
    with pytest.raises(RuntimeError, match="explode"):
        async with uow.transaction() as session:
            await session.execute(text("INSERT INTO marker (id) VALUES (2)"))
            raise RuntimeError("explode")

    async with sessionmaker() as session:
        result = await session.execute(text("SELECT id FROM marker"))
        assert result.scalars().all() == []


async def test_current_session_token_restored_on_exception(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """The ContextVar is reset even when the block raises."""
    uow = SqlUnitOfWork(sessionmaker)
    with pytest.raises(RuntimeError):
        async with uow.transaction():
            assert current_session() is not None
            raise RuntimeError("boom")
    assert current_session() is None
