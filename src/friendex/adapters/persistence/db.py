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

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

if TYPE_CHECKING:
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
    """
    return create_async_engine(url, echo=echo)


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
