"""``AccountCog`` — ``/balance``, ``/optin``, ``/optout`` slash commands.

The account cog is the read-mostly half of the Phase 11 cog surface: it lets
a member see their cash + portfolio summary (``/balance``) and toggle their
own consent to be a tradeable stock (``/optin`` / ``/optout``). Every reply
is ephemeral so toggling consent does not leak into the channel.

The cog holds per-guild service *factories*, not service instances —
matching the Phase 9 service_factory convention (``baton-runner/
br-2026-05-25-phase-9/digest-phase-9.md``). On each invocation the cog
calls the factory with ``guild_id_of(interaction)`` to obtain the
guild-scoped service, then delegates the use case.

Domain errors **propagate uncaught**; Phase 13 will install a tree-wide
``app_commands`` error handler. Cogs neither catch
:class:`~friendex.domain.errors.DomainError` nor render
:func:`build_error_embed` here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of
from friendex.adapters.discord_bot.embeds import (
    COLOR_NEUTRAL,
    COLOR_SUCCESS,
    build_balance_embed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.activity_service import ActivityService
    from friendex.application.portfolio_service import PortfolioService


class AccountCog(commands.Cog):
    """Account-management slash commands.

    Ctor takes per-guild service factories so a single cog instance serves
    every guild the bot is in. Phase 13/14 wires the factories — they
    construct each per-guild service on demand against the shared
    persistence + lock-manager singletons.
    """

    def __init__(
        self,
        *,
        portfolio_service_factory: Callable[[str], PortfolioService],
        activity_service_factory: Callable[[str], ActivityService],
    ) -> None:
        self._portfolio_factory = portfolio_service_factory
        self._activity_factory = activity_service_factory

    # -- /balance -----------------------------------------------------------

    @app_commands.command(
        name="balance",
        description="View your cash, net worth, and hedge fund summary.",
    )
    async def balance(self, interaction: discord.Interaction) -> None:
        """Reply ephemerally with the invoker's portfolio snapshot."""
        portfolio_service = self._portfolio_factory(guild_id_of(interaction))
        snapshot = await portfolio_service.portfolio_snapshot(
            user_id=str(interaction.user.id)
        )
        if snapshot is None:
            embed = discord.Embed(
                title="Account Balance",
                color=COLOR_NEUTRAL,
                description=(
                    "No account found yet — run `/daily` to open one and "
                    "claim your starter cash."
                ),
            )
        else:
            embed = build_balance_embed(snapshot)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /optin -------------------------------------------------------------

    @app_commands.command(
        name="optin",
        description="Opt in to being a tradeable stock on this server.",
    )
    async def optin(self, interaction: discord.Interaction) -> None:
        """Mark the invoker's account as tradeable and confirm ephemerally."""
        activity_service = self._activity_factory(guild_id_of(interaction))
        await activity_service.set_opt_in(str(interaction.user.id), True)
        embed = discord.Embed(
            title="Opted in",
            color=COLOR_SUCCESS,
            description="You are now a tradeable stock on this server.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- /optout ------------------------------------------------------------

    @app_commands.command(
        name="optout",
        description="Opt out of being a tradeable stock on this server.",
    )
    async def optout(self, interaction: discord.Interaction) -> None:
        """Remove the invoker's account from the market and confirm ephemerally."""
        activity_service = self._activity_factory(guild_id_of(interaction))
        await activity_service.set_opt_in(str(interaction.user.id), False)
        embed = discord.Embed(
            title="Opted out",
            color=COLOR_SUCCESS,
            description="Your stock has been removed from the market.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
