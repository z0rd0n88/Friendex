"""Alembic migration environment for Friendex (async SQLAlchemy 2.0).

This env is async-aware and self-configures from the ``DATABASE_URL``
environment variable rather than the static ``sqlalchemy.url`` in
``alembic.ini`` — keeping the live database URL out of version control and
letting tests point migrations at a throwaway temp database.

``target_metadata`` is wired to :data:`friendex.adapters.persistence.db.Base`'s
metadata. Importing :mod:`friendex.adapters.persistence.orm` is essential: the
ORM classes register themselves on ``Base.metadata`` at definition time, so the
import is what makes every table visible to autogenerate.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# ``friendex.adapters.persistence.orm`` is imported for its side effect: each ORM
# class registers itself on ``Base.metadata`` at definition time, so the import
# is what makes every table visible to ``target_metadata``. ``noqa: F401`` marks
# the otherwise-"unused" import as intentional.
import friendex.adapters.persistence.orm  # noqa: F401
from friendex.adapters.persistence.db import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

# The Alembic Config object, providing access to the ``alembic.ini`` values.
config = context.config

# Override the static URL with DATABASE_URL when present so migrations target
# the environment-specified database (production, dev, or a test's temp file).
_database_url = os.environ.get("DATABASE_URL")
if _database_url:
    config.set_main_option("sqlalchemy.url", _database_url)

# Configure Python logging from the ini file, if one is in use.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata target for ``--autogenerate`` support.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL without a live DBAPI).

    Configures the context with just the URL; useful for generating SQL scripts
    against environments where a live database connection is unavailable.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite has no full ``ALTER TABLE``; batch mode (move-and-copy) lets
        # migrations rebuild tables to add/drop FK actions (ADR-0002 cascade).
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations against an established (sync-facing) connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite has no full ``ALTER TABLE``; batch mode (move-and-copy) lets
        # migrations rebuild tables to add/drop FK actions (ADR-0002 cascade).
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations within its connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode by driving the async engine."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
