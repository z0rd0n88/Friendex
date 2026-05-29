"""Tests for :func:`build_bot` — the Phase 14 bot factory + ``setup_hook``.

The factory constructs a :class:`discord.ext.commands.Bot` with
:attr:`Intents.all` and a ``commands.when_mentioned`` prefix (slash-only bot;
the prefix is API-required but inert). Its :attr:`Bot.setup_hook` is the
single Phase 14 lifecycle entry point: it calls
:meth:`Container.build_runners`, starts every runner, and syncs the slash-command
tree globally (plus an optional dev-guild instant sync when
``settings.dev_guild_id`` is set).

These tests verify the factory's *seams* without bringing up a real Discord
gateway connection: ``bot.tree.sync`` and each runner's ``start()`` method are
patched so the assertions exercise the wiring contract, not Discord's network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from friendex.adapters.config import Settings
from friendex.adapters.container import Container
from friendex.adapters.discord_bot.bot import build_bot

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.adapters.tasks.task_runner import TaskRunner

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


def _capture_runners_stub(
    container: Container,
) -> tuple[list[TaskRunner], Callable]:
    """Return (captured_list, stub) for monkeypatching ``container.build_runners``.

    The stub calls the real ``build_runners`` (so lifecycle injection runs),
    then patches ``start`` on every returned runner to a ``MagicMock`` so no
    ``discord.ext.tasks.Loop`` is actually started in tests.
    """
    real_br = container.build_runners
    captured: list[TaskRunner] = []

    def stub(bot):  # type: ignore[return]
        runners = real_br(bot)
        for r in runners:
            r.start = MagicMock(name=f"TaskRunner.start<{type(r._task).__name__}>")
        captured.extend(runners)
        return runners

    return captured, stub


def test_build_bot_returns_commands_bot(
    settings: Settings, container: Container
) -> None:
    bot = build_bot(settings, container)
    assert isinstance(bot, commands.Bot)


def test_build_bot_intents_are_explicit_and_omit_presences(
    settings: Settings, container: Container
) -> None:
    """The bot opts into the five intents the listeners need and nothing more.

    Wave 1 (issue #82 H12): ``Intents.all()`` enabled ``presences``, which has
    no consumer in Friendex AND blocks bot verification past 100 guilds.
    The explicit set is ``message_content``, ``members``, ``voice_states``,
    ``reactions``, ``guilds``.

    Mutation-hardening: any regression that flips ``presences=True`` or
    re-introduces ``Intents.all()`` will trip the absence assertion.
    """
    bot = build_bot(settings, container)
    intents = bot.intents

    # Required intents — every flag must be ON.
    assert intents.message_content is True
    assert intents.members is True
    assert intents.voice_states is True
    assert intents.reactions is True
    assert intents.guilds is True

    # ``presences`` MUST stay off — privileged intent with no consumer.
    assert intents.presences is False
    # Sanity: we are not silently enabling every other flag via ``Intents.all``.
    assert intents.value != discord.Intents.all().value


def test_build_bot_setup_hook_is_set_and_overridden(
    settings: Settings, container: Container
) -> None:
    """``setup_hook`` is no longer the default discord.py no-op."""
    bot = build_bot(settings, container)
    assert bot.setup_hook is not None
    assert bot.setup_hook != commands.Bot.setup_hook.__get__(bot)


async def test_setup_hook_starts_every_runner_and_syncs_tree(
    settings: Settings,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """setup_hook calls build_runners, starts every runner, syncs commands globally."""
    bot = build_bot(settings, container)

    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot.tree.copy_global_to = MagicMock(name="tree.copy_global_to")  # type: ignore[method-assign]
    bot._connection._guilds = {}

    captured, stub = _capture_runners_stub(container)
    monkeypatch.setattr(container, "build_runners", stub)

    await bot.setup_hook()

    assert len(captured) == 8, f"Expected 8 runners, got {len(captured)}"
    for runner in captured:
        runner.start.assert_called_once()
    bot.tree.sync.assert_awaited()
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

    _, stub = _capture_runners_stub(container)
    monkeypatch.setattr(container, "build_runners", stub)

    await bot.setup_hook()

    bot.tree.copy_global_to.assert_called_once()
    call = bot.tree.copy_global_to.call_args
    guild_obj = call.kwargs.get("guild") or call.args[0]
    assert int(guild_obj.id) == 424242
    assert bot.tree.sync.await_count >= 2


async def test_setup_hook_build_runners_swaps_iter_guild_ids_and_notifier(
    settings: Settings,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After ``setup_hook`` runs, ``build_runners`` has swapped iter_guild_ids.

    The swap is observable: each raw task's ``_iter_guild_ids`` attribute is no
    longer the module-level ``_empty_guild_ids`` placeholder, and
    ``LiquidationTask._notifier`` is no longer the no-op.
    """
    from friendex.adapters.container import _empty_guild_ids, _noop_notifier

    bot = build_bot(settings, container)
    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot._connection._guilds = {}

    _, stub = _capture_runners_stub(container)
    monkeypatch.setattr(container, "build_runners", stub)

    for task in container.raw_tasks:
        assert task._iter_guild_ids is _empty_guild_ids

    await bot.setup_hook()

    for task in container.raw_tasks:
        assert task._iter_guild_ids is not _empty_guild_ids
    from friendex.adapters.tasks.liquidation_task import LiquidationTask

    liquidation = next(t for t in container.raw_tasks if isinstance(t, LiquidationTask))
    assert liquidation._notifier is not _noop_notifier


async def test_setup_hook_registers_cogs_and_listeners(
    settings: Settings,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``register_with`` is invoked from ``setup_hook`` so every cog lands on the bot.

    Phase 14 may invoke ``register_with`` either before or from inside
    ``setup_hook``; the contract only requires that by the time setup_hook
    returns, every cog (7) + listener (4) + the Wave 1 ``_GuildLifecycleCog``
    is on the bot — total 12 ``add_cog`` calls.
    """
    bot = build_bot(settings, container)
    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot._connection._guilds = {}
    bot.add_cog = AsyncMock(name="add_cog")  # type: ignore[method-assign]

    _, stub = _capture_runners_stub(container)
    monkeypatch.setattr(container, "build_runners", stub)

    await bot.setup_hook()

    assert bot.add_cog.await_count == 12
