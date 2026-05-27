"""Friendex CLI entry point (Phase 14).

This module exposes :func:`main` — the synchronous CLI shim — and
:func:`amain` — the async core that loads settings, configures logging,
builds the engine + sessionmaker, constructs the
:class:`~friendex.adapters.container.Container`, builds the bot via
:func:`~friendex.adapters.discord_bot.bot.build_bot`, registers cogs and
listeners (and the error handler), then starts the bot.

The engine disposal sits in a ``try ... finally`` — even when the bot
exits with an exception the engine is closed gracefully so subsequent
restarts don't inherit a stuck pool.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from friendex.adapters.config import Settings, configure_logging
from friendex.adapters.container import Container
from friendex.adapters.discord_bot.bot import build_bot


async def amain() -> None:
    """Async core of the CLI entry point.

    Steps:

    1. Load :class:`Settings` from environment (``.env`` + process env).
    2. Configure structured logging.
    3. Build an async SQLAlchemy engine + sessionmaker.
    4. Construct the :class:`Container` over the sessionmaker.
    5. Build the bot via :func:`build_bot` and start it. ``setup_hook`` runs
       :meth:`Container.register_with`, :meth:`Container.bind_runtime`, then
       :meth:`task.start` for every task, then a global tree sync (with an
       optional dev-guild sync when ``settings.dev_guild_id`` is set).
    6. Always dispose the engine in ``finally``.
    """
    # ``Settings`` derives every field from environment / ``.env`` via
    # pydantic-settings, so the no-argument call is the canonical form;
    # mypy can't see the env-driven defaults, hence the localized ignore.
    settings = Settings()  # type: ignore[call-arg]
    configure_logging(settings)

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        container = Container(settings, sessionmaker)
        bot = build_bot(settings, container)
        await bot.start(settings.discord_token)
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
