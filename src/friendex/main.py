"""Friendex CLI entry point (Phase 14).

This module exposes :func:`main` — the synchronous CLI shim — and
:func:`amain` — the async core that loads settings, configures logging,
builds the engine + sessionmaker, constructs the
:class:`~friendex.adapters.container.Container`, builds the bot via
:func:`~friendex.adapters.discord_bot.bot.build_bot`, registers cogs and
listeners (and the error handler), then starts the bot.

**Engine via :func:`build_engine`, not raw ``create_async_engine``.** The
factory in :mod:`friendex.adapters.persistence.db` attaches a ``connect``
event listener that issues ``PRAGMA foreign_keys=ON`` on every new SQLite
DBAPI connection (ADR-0002). Bypassing it would silently disable every
``ON DELETE CASCADE`` declared in the schema — a class-C invariant.

**Bot lifecycle uses nested ``try/finally``.** ``bot.start`` raising (bad
token, gateway disconnect, malformed setup_hook) must still close the bot
so the aiohttp connector tears down and the process can exit cleanly; the
outer ``finally`` then disposes the engine pool so subsequent restarts
don't inherit a stuck pool.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from friendex.adapters.config import Settings, configure_logging
from friendex.adapters.container import Container
from friendex.adapters.discord_bot.bot import build_bot
from friendex.adapters.persistence.db import build_engine


async def amain() -> None:
    """Async core of the CLI entry point.

    Steps:

    1. Load :class:`Settings` from environment (``.env`` + process env).
    2. Configure structured logging.
    3. Build an async SQLAlchemy engine via :func:`build_engine` (so the
       SQLite ``PRAGMA foreign_keys=ON`` listener fires on every connect)
       and a sessionmaker over it.
    4. Construct the :class:`Container` over the sessionmaker.
    5. Build the bot via :func:`build_bot` and start it under
       ``try/finally: await bot.close()`` so the aiohttp connector cannot
       leak when ``start`` raises. ``setup_hook`` runs
       :meth:`Container.register_with`, :meth:`Container.build_runners`,
       then ``start`` on every runner, then a global tree sync (with an
       optional dev-guild sync when ``settings.dev_guild_id`` is set).
    6. Always dispose the engine in the outer ``finally``.
    """
    # ``Settings`` derives every field from environment / ``.env`` via
    # pydantic-settings, so the no-argument call is the canonical form;
    # mypy can't see the env-driven defaults, hence the localized ignore.
    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings)

    engine = build_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        container = Container(settings, sessionmaker)
        bot = build_bot(settings, container)
        try:
            await bot.start(settings.discord_token.get_secret_value())
        finally:
            # ``bot.close`` is idempotent on discord.py 2.x and tears down
            # the aiohttp connector + voice clients. Without this, a
            # ``start`` exception leaks the connector and leaves the
            # event loop holding open sockets until process exit.
            await bot.close()
    finally:
        await engine.dispose()


def main() -> None:
    """Synchronous CLI shim — runs :func:`amain` under :func:`asyncio.run`.

    This is the entry point exposed by ``pyproject.toml``'s
    ``[project.scripts]`` table as ``friendex = "friendex.main:main"`` and
    by :mod:`friendex.__main__` as ``python -m friendex``.
    """
    asyncio.run(amain())


if __name__ == "__main__":  # pragma: no cover
    main()
