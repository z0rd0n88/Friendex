"""``StatsCog`` — ``/trending``, ``/mystats``, ``/price``, ``/mystock``.

The stats cog is the *market-information* half of the Phase 11b cog surface.
It exposes:

* ``/trending`` — **public** leaderboard of top movers (price + score).
* ``/mystats`` — **ephemeral** personal engagement-tier + trending-score
  snapshot for the invoker.
* ``/price <user>`` — **ephemeral** price look-up for an explicit target.
* ``/mystock`` — **ephemeral** convenience alias that price-looks-up the
  invoker; same embed builder as ``/price`` but with no user argument on
  the slash surface.

``/mystock`` is intentionally a separate ``@app_commands.command`` rather
than a default-argument variant of ``/price``: Discord slash commands have
no aliases, and the canonical-name + autocomplete approach in the project
``CLAUDE.md`` is to expose two distinct names that happen to share a
back-end.

Per Phase 11a digest §convention 4, ``None``-returning read paths render a
small inline ``COLOR_NEUTRAL`` embed (no builder; this is a brand-new /
empty edge case). Domain errors **propagate uncaught** — Phase 13 owns the
``app_commands`` error handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of
from friendex.adapters.discord_bot.embeds import (
    COLOR_NEUTRAL,
    build_mystats_embed,
    build_price_embed,
    build_trending_embed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.stats_service import StatsService


class StatsCog(commands.Cog):
    """Market-information slash commands.

    Ctor takes a per-guild :class:`StatsService` factory so a single cog
    instance serves every guild the bot is in. Phase 13/14 wires the
    factory — it constructs each per-guild service on demand against the
    shared persistence singletons (``StatsService`` is lockless;
    ``LockManager`` is not on its ctor).
    """

    def __init__(
        self,
        *,
        stats_service_factory: Callable[[str], StatsService],
    ) -> None:
        self._stats_factory = stats_service_factory

    # -- /trending ----------------------------------------------------------

    @app_commands.command(
        name="trending",
        description="Top movers leaderboard for this server.",
    )
    async def trending(self, interaction: discord.Interaction) -> None:
        """Reply publicly with the trending-stocks leaderboard."""
        stats_service = self._stats_factory(guild_id_of(interaction))
        entries = await stats_service.trending_snapshot()
        embed = build_trending_embed(entries)
        await interaction.response.send_message(embed=embed)

    # -- /mystats -----------------------------------------------------------

    @app_commands.command(
        name="mystats",
        description="Your personal activity stats — engagement tier and score.",
    )
    async def mystats(self, interaction: discord.Interaction) -> None:
        """Reply ephemerally with the invoker's engagement snapshot."""
        stats_service = self._stats_factory(guild_id_of(interaction))
        stats = await stats_service.user_stats(user_id=str(interaction.user.id))
        if stats is None:
            embed = discord.Embed(
                title="Your Activity Stats",
                color=COLOR_NEUTRAL,
                description=(
                    "No activity recorded yet — chat in this server to "
                    "start building your engagement score."
                ),
            )
        else:
            embed = build_mystats_embed(stats)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /price <user> ------------------------------------------------------

    @app_commands.command(
        name="price",
        description="Look up the current price and 24h stats for a member's stock.",
    )
    @app_commands.describe(user="The member whose stock price to look up.")
    async def price(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Reply ephemerally with ``user``'s price stats."""
        stats_service = self._stats_factory(guild_id_of(interaction))
        stats = await stats_service.get_price_stats(user_id=str(user.id))
        if stats is None:
            embed = discord.Embed(
                title="Price",
                color=COLOR_NEUTRAL,
                description=(
                    "No price data for this member yet — they may not have "
                    "any trading activity recorded."
                ),
            )
        else:
            embed = build_price_embed(stats)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /mystock -----------------------------------------------------------

    @app_commands.command(
        name="mystock",
        description="View your own stock's current price and 24h stats.",
    )
    async def mystock(self, interaction: discord.Interaction) -> None:
        """Reply ephemerally with the invoker's own price stats.

        Distinct ``@app_commands.command`` from ``/price`` — no user
        argument on the slash surface — but reuses
        :func:`build_price_embed` for the rendering.
        """
        stats_service = self._stats_factory(guild_id_of(interaction))
        stats = await stats_service.get_price_stats(user_id=str(interaction.user.id))
        if stats is None:
            embed = discord.Embed(
                title="Your Stock",
                color=COLOR_NEUTRAL,
                description=(
                    "No price data for your stock yet — run `/optin` and "
                    "stay active to start building one."
                ),
            )
        else:
            embed = build_price_embed(stats)
        await interaction.response.send_message(embed=embed, ephemeral=True)
