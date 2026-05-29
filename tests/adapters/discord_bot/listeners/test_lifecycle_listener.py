"""Tests for :class:`LifecycleListener` — ``on_guild_remove`` bridge.

The listener exists solely to forward discord.py's ``on_guild_remove``
event to a composition-root-supplied cleanup callback (Wave 1 #82 M2 +
review LOW-3). It must:

* expose ``on_guild_remove`` as a ``commands.Cog.listener()``-decorated
  coroutine so ``Bot.add_cog`` registers it onto the event loop;
* forward the guild argument verbatim to the injected callback;
* not import :class:`~friendex.adapters.container.Container` (the listener
  layer cannot depend inward on the composition root).

Tests instantiate the listener with an :class:`unittest.mock.AsyncMock`
callback and ``await`` the listener method directly (Phase 11 callback-
direct idiom).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from discord.ext import commands

from friendex.adapters.discord_bot.listeners.lifecycle_listener import (
    LifecycleListener,
)


def test_lifecycle_listener_is_a_commands_cog() -> None:
    """``LifecycleListener`` must subclass :class:`commands.Cog` so
    :meth:`Bot.add_cog` registers its event listeners.
    """
    listener = LifecycleListener(on_guild_remove=AsyncMock())
    assert isinstance(listener, commands.Cog)


async def test_on_guild_remove_forwards_guild_to_callback() -> None:
    """The listener forwards the guild verbatim to the injected callback."""
    callback = AsyncMock(name="on_guild_remove_callback")
    listener = LifecycleListener(on_guild_remove=callback)

    guild = MagicMock(name="Guild")
    guild.id = "doomed-guild"

    await listener.on_guild_remove(guild)

    callback.assert_awaited_once_with(guild)


async def test_on_guild_remove_propagates_callback_exceptions() -> None:
    """The bridge does not swallow callback exceptions.

    Phase 13 owns the central error handler — listener-level cleanup
    failures must surface to the operator rather than being silently
    dropped at the bridge.
    """
    callback = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    listener = LifecycleListener(on_guild_remove=callback)
    guild = MagicMock(name="Guild")

    import pytest

    with pytest.raises(RuntimeError, match="cleanup failed"):
        await listener.on_guild_remove(guild)


def test_lifecycle_listener_does_not_import_container() -> None:
    """Module-level: the listener must not import the composition root.

    Architectural pin: ``listeners/`` is closer to Discord than
    ``container.py`` (the composition root), so a listener importing the
    container would create a circular dependency and an inward arrow on
    the hexagonal graph.
    """
    import friendex.adapters.discord_bot.listeners.lifecycle_listener as mod

    # ``Container`` is not in the module's namespace — neither imported
    # directly nor via a ``from container import …`` line.
    assert not hasattr(mod, "Container"), (
        "lifecycle_listener.py must not import Container — listener layer "
        "cannot depend inward on the composition root"
    )
