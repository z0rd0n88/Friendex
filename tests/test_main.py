"""Tests for :mod:`friendex.main` — the CLI entry point lifecycle.

The entry point's two hard contracts (Wave 1, issue #84 C):

* Engine creation goes through :func:`friendex.adapters.persistence.db.build_engine`
  — the factory installs the SQLite ``PRAGMA foreign_keys=ON`` event listener.
  Calling ``create_async_engine`` directly silently bypasses FK enforcement.
* ``bot.start(token)`` is wrapped in a ``try/finally: await bot.close()`` so the
  aiohttp connector cannot leak when ``start`` raises. ``engine.dispose()``
  stays inside its own ``finally`` so the pool always drains.

Tests stub the engine, sessionmaker, container, and bot so the entry point is
exercised without touching Discord or SQLAlchemy.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

# ``friendex/__init__.py`` re-exports ``friendex.main.main`` as ``friendex.main``,
# which shadows the submodule attribute on the package. Resolve the actual
# submodule via :mod:`importlib` so ``monkeypatch.setattr`` targets the real
# module namespace (where ``Settings``, ``build_engine``, etc. live).
main_module = importlib.import_module("friendex.main")
amain = main_module.amain


_VALID_TOKEN = "x" * 32


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub ``Settings`` so ``amain`` does not touch the real env."""
    settings = MagicMock(name="Settings")
    settings.database_url = "sqlite+aiosqlite:///:memory:"
    settings.discord_token = SecretStr(_VALID_TOKEN)
    settings.dev_guild_id = None
    monkeypatch.setattr(main_module, "Settings", MagicMock(return_value=settings))
    monkeypatch.setattr(main_module, "configure_logging", MagicMock())
    return settings


@pytest.mark.usefixtures("patched_settings")
async def test_amain_uses_build_engine_not_raw_create_async_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine is built via ``build_engine`` so the SQLite FK pragma fires.

    Mutation-hardening: a regression that re-introduces ``create_async_engine``
    fails this test because the patched ``build_engine`` is the only path that
    yields the recorded engine.
    """
    engine = MagicMock(name="AsyncEngine")
    engine.dispose = AsyncMock(name="engine.dispose")
    build_engine_mock = MagicMock(name="build_engine", return_value=engine)
    monkeypatch.setattr(main_module, "build_engine", build_engine_mock, raising=False)

    container_mock = MagicMock(name="Container")
    monkeypatch.setattr(
        main_module, "Container", MagicMock(return_value=container_mock)
    )

    bot = MagicMock(name="Bot")
    bot.start = AsyncMock(name="bot.start")
    bot.close = AsyncMock(name="bot.close")
    monkeypatch.setattr(main_module, "build_bot", MagicMock(return_value=bot))

    monkeypatch.setattr(
        main_module, "async_sessionmaker", MagicMock(return_value=MagicMock())
    )

    await amain()

    build_engine_mock.assert_called_once_with("sqlite+aiosqlite:///:memory:")
    # The shape contract — ``build_engine`` is the seam, never raw
    # ``create_async_engine`` (which would skip the FK pragma listener).


@pytest.mark.usefixtures("patched_settings")
async def test_amain_calls_bot_close_when_start_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bot.start`` raising must still run ``bot.close()`` and ``engine.dispose()``.

    Mutation-hardening: a regression that omits the ``try/finally: bot.close()``
    leaves the aiohttp connector leaked when ``start`` raises (e.g. invalid
    token, gateway disconnect). The test pins both close-then-dispose calls.
    """
    engine = MagicMock(name="AsyncEngine")
    engine.dispose = AsyncMock(name="engine.dispose")
    build_engine_mock = MagicMock(name="build_engine", return_value=engine)
    monkeypatch.setattr(main_module, "build_engine", build_engine_mock, raising=False)

    container_mock = MagicMock(name="Container")
    monkeypatch.setattr(
        main_module, "Container", MagicMock(return_value=container_mock)
    )

    bot = MagicMock(name="Bot")
    boom = RuntimeError("gateway exploded")
    bot.start = AsyncMock(name="bot.start", side_effect=boom)
    bot.close = AsyncMock(name="bot.close")
    monkeypatch.setattr(main_module, "build_bot", MagicMock(return_value=bot))
    monkeypatch.setattr(
        main_module, "async_sessionmaker", MagicMock(return_value=MagicMock())
    )

    with pytest.raises(RuntimeError, match="gateway exploded"):
        await amain()

    bot.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.usefixtures("patched_settings")
async def test_amain_calls_bot_close_on_clean_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even on a clean ``bot.start`` return, ``bot.close()`` still runs."""
    engine = MagicMock(name="AsyncEngine")
    engine.dispose = AsyncMock(name="engine.dispose")
    monkeypatch.setattr(
        main_module, "build_engine", MagicMock(return_value=engine), raising=False
    )

    container_mock = MagicMock(name="Container")
    monkeypatch.setattr(
        main_module, "Container", MagicMock(return_value=container_mock)
    )

    bot = MagicMock(name="Bot")
    bot.start = AsyncMock(name="bot.start")
    bot.close = AsyncMock(name="bot.close")
    monkeypatch.setattr(main_module, "build_bot", MagicMock(return_value=bot))
    monkeypatch.setattr(
        main_module, "async_sessionmaker", MagicMock(return_value=MagicMock())
    )

    await amain()

    bot.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.usefixtures("patched_settings")
async def test_amain_close_failure_still_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``bot.close()`` raising must not skip ``engine.dispose()``.

    Defence in depth: ``bot.close`` failing in the middle of cleanup should
    still tear down the engine pool. The inner ``finally`` wrapping
    ``engine.dispose`` is load-bearing here.
    """
    engine = MagicMock(name="AsyncEngine")
    engine.dispose = AsyncMock(name="engine.dispose")
    monkeypatch.setattr(
        main_module, "build_engine", MagicMock(return_value=engine), raising=False
    )

    container_mock = MagicMock(name="Container")
    monkeypatch.setattr(
        main_module, "Container", MagicMock(return_value=container_mock)
    )

    bot = MagicMock(name="Bot")
    bot.start = AsyncMock(name="bot.start")
    bot.close = AsyncMock(name="bot.close", side_effect=RuntimeError("close boom"))
    monkeypatch.setattr(main_module, "build_bot", MagicMock(return_value=bot))
    monkeypatch.setattr(
        main_module, "async_sessionmaker", MagicMock(return_value=MagicMock())
    )

    with pytest.raises(RuntimeError, match="close boom"):
        await amain()

    bot.close.assert_awaited_once()
    engine.dispose.assert_awaited_once()
