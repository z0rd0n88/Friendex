"""``AdminCog`` — ``/help`` (ephemeral) and ``/game_intro`` (public, admin).

``/help`` renders the canonical command list (built by
:func:`~friendex.adapters.discord_bot.embeds.build_help_embed`) so a member
can list every slash command without leaving the chat. Reply is ephemeral so
the help-text noise stays on the requester's side.

``/game_intro`` posts the static intro embed (
:func:`~friendex.adapters.discord_bot.embeds.build_intro_embed`) publicly —
moderators run it once per server when on-boarding. The command is gated by
``@app_commands.checks.has_permissions(manage_guild=True)``, so only members
with the *Manage Server* permission can broadcast the intro.

The cog needs no application-service deps; the embeds are static so we have
no per-guild routing to perform. Phase 13/14 will instantiate this cog
exactly once and hand it to the bot.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.embeds import (
    build_help_embed,
    build_intro_embed,
)


class AdminCog(commands.Cog):
    """Static admin / help slash commands — no service dependencies."""

    def __init__(self) -> None:
        # No state; explicit empty ctor for symmetry with the other cogs and
        # so Phase 13 can call ``AdminCog()`` uniformly.
        pass

    @app_commands.command(
        name="help",
        description="List every Friendex slash command.",
    )
    async def help(self, interaction: discord.Interaction) -> None:
        """Reply ephemerally with the static help embed."""
        embed = build_help_embed()
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="game_intro",
        description="Post the Friendex introduction embed for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def game_intro(self, interaction: discord.Interaction) -> None:
        """Reply publicly with the static intro embed.

        Gated by ``manage_guild`` — Discord will reject the command for
        members without the *Manage Server* permission via the local
        permission check installed by :func:`app_commands.checks.has_permissions`.
        """
        embed = build_intro_embed()
        await interaction.response.send_message(embed=embed)
