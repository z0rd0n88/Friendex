"""Friendex CLI entry point (Phase 13).

This module exposes :func:`main` — the synchronous CLI shim — and
:func:`amain` — the async core that loads settings, configures logging,
builds the engine + sessionmaker, constructs the
:class:`~friendex.adapters.container.Container`, and then raises
:class:`NotImplementedError` at the bot-construction seam.

Phase 14 will replace the ``NotImplementedError`` with the actual
``build_bot`` + ``bot.start(settings.discord_token)`` call. The seam is
deliberate: Phase 13 must ship a runnable composition root that exits
cleanly (after disposing the engine) so the next phase only has to fill in
the bot-factory + lifecycle bits.

The engine disposal sits in a ``try ... finally`` — even when the
``NotImplementedError`` propagates out of ``amain`` the engine is closed
gracefully, so subsequent restarts don't inherit a stuck pool.
"""

from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from friendex.adapters.config import Settings, configure_logging
from friendex.adapters.container import Container


async def amain() -> None:
    """Async core of the CLI entry point.

    Steps:

    1. Load :class:`Settings` from environment (``.env`` + process env).
    2. Configure structured logging.
    3. Build an async SQLAlchemy engine + sessionmaker.
    4. Construct the :class:`Container` over the sessionmaker.
    5. Raise :class:`NotImplementedError` — Phase 14 fills in the bot.
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
        Container(settings, sessionmaker)
        raise NotImplementedError("Phase 14: build_bot + bot.start")
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
