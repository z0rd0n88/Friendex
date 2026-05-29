"""Tests for the private ``_interaction`` helpers shared by every cog.

Wave 1 (issues #82 H14 / #84 M) hardens :func:`guild_id_of`:

* A missing ``interaction.guild`` no longer aborts with an ``AssertionError``
  (which the error handler then routes to the CRITICAL ``Unexpected error``
  branch — bad UX for what is really a DM-context bug). Instead the helper
  raises :class:`discord.app_commands.NoPrivateMessage`, which discord.py
  ships as the canonical DM-context error and which the central error handler
  already routes to the user-friendly ``CheckFailure`` ephemeral path
  (``NoPrivateMessage`` subclasses ``CheckFailure``).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from discord import app_commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of


def test_guild_id_of_returns_str_guild_id() -> None:
    """Happy path: a guild-attached interaction returns ``str(guild.id)``."""
    interaction = MagicMock(name="Interaction")
    interaction.guild = MagicMock(name="Guild")
    interaction.guild.id = 1234567890
    assert guild_id_of(interaction) == "1234567890"


def test_guild_id_of_raises_no_private_message_when_guild_none() -> None:
    """DM-context: ``interaction.guild is None`` raises ``NoPrivateMessage``.

    Mutation-hardening: a regression that re-introduces the ``assert`` would
    raise ``AssertionError``, which routes to the CRITICAL ``Unexpected
    error`` branch in the central handler. ``NoPrivateMessage`` is the
    discord.py-sanctioned error and already wires into the friendly
    ``CheckFailure`` reply path.
    """
    interaction = MagicMock(name="Interaction")
    interaction.guild = None
    with pytest.raises(app_commands.NoPrivateMessage):
        guild_id_of(interaction)


def test_no_private_message_is_subclass_of_check_failure() -> None:
    """Routing invariant: ``NoPrivateMessage`` is a ``CheckFailure`` subclass.

    The central handler's CheckFailure branch must catch this — discord.py
    declares the inheritance, but if a future discord.py upgrade reshuffles
    the taxonomy, this test trips first.
    """
    assert issubclass(app_commands.NoPrivateMessage, app_commands.CheckFailure)
