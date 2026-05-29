"""``DailyCog`` — the ``/daily`` reward-claim slash command.

The cog passes ``str(interaction.user.id)`` plus a fresh
``datetime.now(tz=UTC)`` through to
:meth:`~friendex.application.daily_service.DailyService.claim_daily` and
replies publicly so the channel sees the claim — matching the spec's
``/daily`` visibility (Bot Commands table in the project ``CLAUDE.md``).

**Wave 1 (#82 H13)**: ``defer(ephemeral=False)`` runs first so Discord sees
the ack within 3 s, then the result embed is delivered via
``interaction.followup.send``. **Wave 1 (#82 H14)**: ``@app_commands.guild_only()``
refuses DM dispatch at the gateway.

:class:`~friendex.domain.errors.AlreadyClaimedToday` propagates uncaught.
Phase 13 wires a tree-wide ``app_commands`` error handler that renders the
error embed; the cog must not catch :class:`DomainError` here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of
from friendex.adapters.discord_bot.embeds import build_daily_embed

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.daily_service import DailyService


class DailyCog(commands.Cog):
    """The ``/daily`` slash command."""

    def __init__(
        self,
        *,
        daily_service_factory: Callable[[str], DailyService],
    ) -> None:
        self._daily_factory = daily_service_factory

    @app_commands.command(
        name="daily",
        description="Claim your daily reward — streak bonus every 7 days.",
    )
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction) -> None:
        """Credit the daily reward and announce the outcome publicly."""
        await interaction.response.defer(ephemeral=False)
        daily_service = self._daily_factory(guild_id_of(interaction))
        now = datetime.now(tz=UTC)
        result = await daily_service.claim_daily(str(interaction.user.id), now)
        embed = build_daily_embed(result)
        await interaction.followup.send(embed=embed)
