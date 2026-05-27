"""Tests for :func:`build_bot` — the Phase 14 bot factory + ``setup_hook``.

The factory constructs a :class:`discord.ext.commands.Bot` with
:attr:`Intents.all` and a ``commands.when_mentioned`` prefix (slash-only bot;
the prefix is API-required but inert). Its :attr:`Bot.setup_hook` is the
single Phase 14 lifecycle entry point: it calls
:meth:`Container.bind_runtime`, starts every task, and syncs the slash-command
tree globally (plus an optional dev-guild instant sync when
``settings.dev_guild_id`` is set).

These tests verify the factory's *seams* without bringing up a real Discord
gateway connection: ``bot.tree.sync`` and the task ``start()`` methods are
patched so the assertions exercise the wiring contract, not Discord's
network.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from friendex.adapters.config import Settings
from friendex.adapters.container import Container
from friendex.adapters.discord_bot.bot import build_bot

_VALID_TOKEN = "x" * 32


@pytest.fixture
def settings() -> Settings:
    return Settings(discord_token=_VALID_TOKEN)


@pytest.fixture
def fake_sessionmaker() -> MagicMock:
    return MagicMock(name="async_sessionmaker")


@pytest.fixture
def container(settings: Settings, fake_sessionmaker: MagicMock) -> Container:
    return Container(settings, fake_sessionmaker)


def test_build_bot_returns_commands_bot(
    settings: Settings, container: Container
) -> None:
    bot = build_bot(settings, container)
    assert isinstance(bot, commands.Bot)


def test_build_bot_intents_are_all(settings: Settings, container: Container) -> None:
    """The bot opts into every privileged intent — Phase 14 spec."""
    bot = build_bot(settings, container)
    # ``Intents.all().value`` is the bitfield with every flag set; the bot
    # should at minimum carry the same value.
    assert bot.intents.value == discord.Intents.all().value


def test_build_bot_setup_hook_is_set_and_overridden(
    settings: Settings, container: Container
) -> None:
    """``setup_hook`` is no longer the default discord.py no-op."""
    bot = build_bot(settings, container)
    # commands.Bot.setup_hook (the inherited default) is identifiable by
    # its qualified name on the Bot class.
    assert bot.setup_hook is not None
    # The instance attribute must differ from the class-level default.
    assert bot.setup_hook != commands.Bot.setup_hook.__get__(bot)


async def test_setup_hook_starts_every_task_and_syncs_tree(
    settings: Settings,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup_hook calls bind_runtime, starts every task, syncs commands globally."""
    bot = build_bot(settings, container)

    # Patch the tree sync so no network call escapes.
    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot.tree.copy_global_to = MagicMock(name="tree.copy_global_to")  # type: ignore[method-assign]
    # Empty guild list keeps bind_runtime's iter callable trivial.
    bot._connection._guilds = {}
    # Patch every task's ``start`` to a no-op MagicMock so we can assert calls.
    for task in container.tasks:
        task.start = MagicMock(name=f"{type(task).__name__}.start")  # type: ignore[method-assign]

    await bot.setup_hook()

    for task in container.tasks:
        assert task.start.call_count == 1, (
            f"{type(task).__name__}.start() was not called"
        )
    bot.tree.sync.assert_awaited()
    # No dev_guild_id set on this fixture → copy_global_to is NOT called.
    bot.tree.copy_global_to.assert_not_called()


async def test_setup_hook_dev_guild_sync_when_dev_guild_id_set(
    fake_sessionmaker: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``settings.dev_guild_id`` is set, the tree is also synced to that guild."""
    dev_settings = Settings(discord_token=_VALID_TOKEN, dev_guild_id=424242)
    container = Container(dev_settings, fake_sessionmaker)
    bot = build_bot(dev_settings, container)

    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot.tree.copy_global_to = MagicMock(name="tree.copy_global_to")  # type: ignore[method-assign]
    bot._connection._guilds = {}
    for task in container.tasks:
        task.start = MagicMock(name=f"{type(task).__name__}.start")  # type: ignore[method-assign]

    await bot.setup_hook()

    bot.tree.copy_global_to.assert_called_once()
    # The kwarg ``guild=`` carries a ``discord.Object`` with the dev id.
    call = bot.tree.copy_global_to.call_args
    guild_obj = call.kwargs.get("guild") or call.args[0]
    assert int(guild_obj.id) == 424242
    # tree.sync called at least twice — global + dev-guild.
    assert bot.tree.sync.await_count >= 2


async def test_setup_hook_binds_runtime_before_starting_tasks(
    settings: Settings,
    container: Container,
) -> None:
    """After ``setup_hook`` runs, ``bind_runtime`` has swapped iter_guild_ids.

    The swap is observable: the task's ``_iter_guild_ids`` attribute is no
    longer the module-level ``_empty_guild_ids`` placeholder.
    """
    from friendex.adapters.container import _empty_guild_ids, _noop_notifier

    bot = build_bot(settings, container)
    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot._connection._guilds = {}
    for task in container.tasks:
        task.start = MagicMock()  # type: ignore[method-assign]

    # Pre-condition: every task carries the placeholder.
    for task in container.tasks:
        assert task._iter_guild_ids is _empty_guild_ids

    await bot.setup_hook()

    # Post-condition: every task has been re-wired.
    for task in container.tasks:
        assert task._iter_guild_ids is not _empty_guild_ids
    # LiquidationTask's notifier was also replaced.
    from friendex.adapters.tasks.liquidation_task import LiquidationTask

    liquidation = next(t for t in container.tasks if isinstance(t, LiquidationTask))
    assert liquidation._notifier is not _noop_notifier


async def test_setup_hook_registers_cogs_and_listeners(
    settings: Settings,
    container: Container,
) -> None:
    """``register_with`` is invoked from ``setup_hook`` so every cog lands on the bot.

    Phase 14 may invoke ``register_with`` either before or from inside
    ``setup_hook``; the contract only requires that by the time setup_hook
    returns, every cog (7) + listener (4) is on the bot — total 11 ``add_cog``
    calls.
    """
    bot = build_bot(settings, container)
    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot._connection._guilds = {}
    bot.add_cog = AsyncMock(name="add_cog")  # type: ignore[method-assign]
    for task in container.tasks:
        task.start = MagicMock()  # type: ignore[method-assign]

    await bot.setup_hook()

    assert bot.add_cog.await_count == 11
