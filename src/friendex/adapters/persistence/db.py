"""SQLAlchemy 2.0 async engine, session factory, and declarative base.

This module owns the persistence wiring shared by every repository: the
declarative :class:`Base`, a :func:`build_engine` factory over
``create_async_engine``, and a :func:`build_sessionmaker` factory over
``async_sessionmaker``. The factories take an explicit URL / engine so tests
can target an in-memory database (``sqlite+aiosqlite:///:memory:``) without
touching the configured ``Settings.database_url``.

There are **no import-time side effects** — no engine is created at module
load. Callers (the DI container in later phases, or tests) build the engine
once and thread it through. ``Base.metadata`` is the single registry Alembic's
``env.py`` points ``target_metadata`` at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import DBAPIConnection

    from friendex.adapters.config import Settings


class Base(DeclarativeBase):
    """Declarative base for all Friendex ORM models.

    Every ORM class subclasses this so that ``Base.metadata`` holds the full
    table set; ``Base.metadata.create_all`` (tests) and Alembic autogenerate
    (migrations) both read from this one registry.
    """


def build_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine for ``url``.

    :param url: A SQLAlchemy async URL, e.g.
        ``sqlite+aiosqlite:///data/friendex.db`` or
        ``sqlite+aiosqlite:///:memory:`` for tests.
    :param echo: When ``True``, SQLAlchemy logs every emitted statement.

    The engine carries a ``connect`` event listener that issues
    ``PRAGMA foreign_keys=ON`` on every new SQLite DBAPI connection (ADR-0002).
    SQLite defaults this PRAGMA to ``OFF`` and applies it per-connection, so
    without the listener the schema's ``FOREIGN KEY`` / ``ON DELETE CASCADE``
    declarations would be silently inert.
    """
    engine = create_async_engine(url, echo=echo)
    _enable_sqlite_foreign_keys(engine)
    return engine


def _enable_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Register a ``connect`` listener that turns on SQLite FK enforcement.

    Guarded to the SQLite dialect so the listener is a no-op on any other
    backend (PostgreSQL enforces foreign keys by default), keeping the wiring
    correct if the database is ever swapped.
    """
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(
        dbapi_connection: DBAPIConnection, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


def build_engine_from_settings(
    settings: Settings, *, echo: bool = False
) -> AsyncEngine:
    """Create an async engine from a :class:`Settings` instance."""
    return build_engine(settings.database_url, echo=echo)


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an :class:`async_sessionmaker` bound to ``engine``.

    ``expire_on_commit=False`` keeps loaded attributes accessible after a
    commit without an extra round trip — repositories map ORM rows to domain
    objects immediately after committing, so the values must stay live.
    """
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
