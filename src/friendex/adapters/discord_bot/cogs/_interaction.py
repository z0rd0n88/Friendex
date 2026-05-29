"""Small interaction helpers shared by every cog in this package.

Kept private (``_interaction``) so the cogs package surface is just the
cog classes themselves; Phase 13/14 wiring imports the cogs, not these
helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord import app_commands

if TYPE_CHECKING:
    import discord


def guild_id_of(interaction: discord.Interaction) -> str:
    """Return ``str(interaction.guild.id)`` after narrowing the ``None`` case.

    Slash commands sync globally (per the project ``CLAUDE.md``) and the
    project commands also carry ``dm_permission=False`` decorators so Discord
    never dispatches them in a DM context. If a misconfigured deployment lets
    one slip through, we raise :class:`discord.app_commands.NoPrivateMessage`
    — discord.py's canonical DM-context error — instead of an ``assert``
    (which the central handler would route to the CRITICAL "Unexpected
    error" fallthrough). ``NoPrivateMessage`` subclasses
    :class:`app_commands.CheckFailure`, so the central error handler's
    CheckFailure branch already renders a user-facing reply.

    Wave 1 (issues #82 H14 / #84 M): the previous ``assert`` form was a
    type-narrowing hack rather than a real defence — bare asserts can be
    stripped by ``python -O`` and they leak internals when they do fire.
    """
    if interaction.guild is None:
        raise app_commands.NoPrivateMessage
    return str(interaction.guild.id)
