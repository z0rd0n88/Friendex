"""Tests for :class:`ReactionListener` — ``on_reaction_add``.

The listener delegates to :meth:`ActivityService.record_reaction` for every
non-self, non-bot reaction. Tests instantiate the listener and invoke
``await listener.on_reaction_add(reaction, user)`` directly — mirroring the
Phase 11 cog test idiom (``dpytest`` simulates message events but adds heavy
fixture overhead).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from friendex.adapters.discord_bot.listeners.reaction_listener import ReactionListener
from friendex.domain.errors import OptedOut

if TYPE_CHECKING:
    from collections.abc import Callable
    from unittest.mock import AsyncMock

# ---------------------------------------------------------------------------
# Helpers


def _reaction(*, author_id: int, guild_id: int) -> MagicMock:
    """Build a stub :class:`discord.Reaction` carrying author + guild."""
    reaction = MagicMock(name="Reaction")
    reaction.message.author.id = author_id
    reaction.message.guild.id = guild_id
    return reaction


def _user(*, user_id: int, is_bot: bool = False) -> MagicMock:
    """Build a stub :class:`discord.User` for the reactor."""
    user = MagicMock(name="User")
    user.id = user_id
    user.bot = is_bot
    return user


# ---------------------------------------------------------------------------
# Happy path


async def test_on_reaction_add_records_reaction_for_reactor(
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
) -> None:
    """A normal reaction by user A on user B's message credits A."""
    listener = ReactionListener(activity_service_factory=activity_service_factory)
    reaction = _reaction(author_id=10, guild_id=999)
    user = _user(user_id=42)

    await listener.on_reaction_add(reaction, user)

    activity_service.record_reaction.assert_awaited_once_with(user_id="42")


async def test_on_reaction_add_routes_through_per_guild_factory(
    activity_service: AsyncMock,
) -> None:
    """The factory must be called with ``str(reaction.message.guild.id)``."""
    seen_guild_ids: list[str] = []

    def factory(guild_id: str) -> object:
        seen_guild_ids.append(guild_id)
        return activity_service

    listener = ReactionListener(activity_service_factory=factory)
    reaction = _reaction(author_id=10, guild_id=12345)
    user = _user(user_id=42)

    await listener.on_reaction_add(reaction, user)

    assert seen_guild_ids == ["12345"]


# ---------------------------------------------------------------------------
# Self-reaction silently ignored


async def test_on_reaction_add_self_reaction_is_silently_ignored(
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
) -> None:
    """``reactor.id == message.author.id`` short-circuits without service call."""
    listener = ReactionListener(activity_service_factory=activity_service_factory)
    reaction = _reaction(author_id=42, guild_id=999)
    user = _user(user_id=42)  # same id as message author

    await listener.on_reaction_add(reaction, user)

    activity_service.record_reaction.assert_not_called()


# ---------------------------------------------------------------------------
# Bot-skip mutation-hardening
#
# This test is the load-bearing guard for A6: if the implementation
# stops checking ``user.bot``, this test must fail.


async def test_on_reaction_add_skips_bot_reactor(
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
) -> None:
    """Reactions from bots (including other bots) are silently dropped."""
    listener = ReactionListener(activity_service_factory=activity_service_factory)
    reaction = _reaction(author_id=10, guild_id=999)
    user = _user(user_id=42, is_bot=True)

    await listener.on_reaction_add(reaction, user)

    activity_service.record_reaction.assert_not_called()


# ---------------------------------------------------------------------------
# DM-narrowing — reactions in DMs (no guild) are dropped silently


async def test_on_reaction_add_skips_dm_reaction(
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
) -> None:
    """A reaction on a DM message (``reaction.message.guild is None``) is a no-op.

    Slash commands sync globally and the economy is per-guild (ADR-0001);
    the bot has no economy outside guilds, so DM reactions are dropped
    rather than routed to a non-existent guild service.
    """
    listener = ReactionListener(activity_service_factory=activity_service_factory)
    reaction = MagicMock(name="Reaction")
    reaction.message.author.id = 10
    reaction.message.guild = None
    user = _user(user_id=42)

    await listener.on_reaction_add(reaction, user)

    activity_service.record_reaction.assert_not_called()


# ---------------------------------------------------------------------------
# DomainError propagation (A7)


async def test_on_reaction_add_propagates_domain_error(
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
) -> None:
    """``record_reaction`` raising :class:`DomainError` surfaces uncaught.

    Phase 13 owns the tree-wide handler; listeners must not swallow
    domain errors (mirrors the cogs convention).
    """
    activity_service.record_reaction.side_effect = OptedOut(target_id="42")
    listener = ReactionListener(activity_service_factory=activity_service_factory)
    reaction = _reaction(author_id=10, guild_id=999)
    user = _user(user_id=42)

    with pytest.raises(OptedOut):
        await listener.on_reaction_add(reaction, user)


# ---------------------------------------------------------------------------
# Cog registration sanity


def test_reaction_listener_is_a_cog() -> None:
    """The listener subclasses ``commands.Cog`` so Phase 13 can ``add_cog`` it."""
    from discord.ext import commands

    assert issubclass(ReactionListener, commands.Cog)


def test_reaction_listener_registers_on_reaction_add_listener(
    activity_service_factory: Callable[[str], object],
) -> None:
    """``on_reaction_add`` is decorated with :meth:`commands.Cog.listener`.

    ``commands.Cog`` stores listener-decorated methods so the bot can
    register them automatically when the cog is added; verify the
    listener metadata is set rather than relying on coincidence.
    """
    listener = ReactionListener(activity_service_factory=activity_service_factory)
    names = [name for name, _ in listener.get_listeners()]
    assert "on_reaction_add" in names
