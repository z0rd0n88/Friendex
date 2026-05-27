"""Small interaction helpers shared by every cog in this package.

Kept private (``_interaction``) so the cogs package surface is just the
cog classes themselves; Phase 13/14 wiring imports the cogs, not these
helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import discord


def guild_id_of(interaction: discord.Interaction) -> str:
    """Return ``str(interaction.guild.id)`` after narrowing the ``None`` case.

    Slash commands sync globally (per the project ``CLAUDE.md``); the bot
    intentionally does not support DM-scoped slash commands, so a missing
    ``interaction.guild`` would be a wiring bug. The assert narrows the
    ``Guild | None`` type for :mod:`mypy` and surfaces the misconfiguration
    eagerly at runtime — see Phase 11 signoff decision 3.
    """
    assert interaction.guild is not None, "Slash commands require a guild"
    return str(interaction.guild.id)
