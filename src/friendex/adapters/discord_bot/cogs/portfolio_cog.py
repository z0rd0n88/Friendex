"""``PortfolioCog`` — the ``/portfolio [user]`` slash command.

The portfolio cog is the read-only "look at my (or someone else's) book"
half of the Phase 11b cog surface: it lets a member view their own or a
target member's long + short positions plus cash/net-worth/fund summary.
The reply is **ephemeral** so a personal-finance read does not spam the
channel.

The cog holds a per-guild ``PortfolioService`` *factory*, not a service
instance — matching the Phase 9 service_factory convention (``baton-runner/
br-2026-05-25-phase-9/digest-phase-9.md``). On each invocation the cog
calls the factory with ``guild_id_of(interaction)`` to obtain the
guild-scoped service, then delegates to
:meth:`~friendex.application.portfolio_service.PortfolioService.portfolio_snapshot`.

When the target user has no :class:`UserAccount` yet (``snapshot is None``)
the cog renders a small inline ``COLOR_NEUTRAL`` embed pointing at ``/daily``
— mirroring :meth:`AccountCog.balance`'s "brand-new account" path
(documented exception per Phase 11a digest §convention 4).

Domain errors **propagate uncaught**; Phase 13 owns the central
``app_commands`` error handler. The cog must not ``try/except
DomainError`` and must not call :func:`build_error_embed`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of
from friendex.adapters.discord_bot.embeds import (
    COLOR_NEUTRAL,
    build_portfolio_embed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.portfolio_service import PortfolioService


class PortfolioCog(commands.Cog):
    """The ``/portfolio [user]`` slash command.

    Ctor takes a per-guild :class:`PortfolioService` factory so a single
    cog instance serves every guild the bot is in. Phase 13/14 wires the
    factory — it constructs each per-guild service on demand against the
    shared persistence + lock-manager singletons.
    """

    def __init__(
        self,
        *,
        portfolio_service_factory: Callable[[str], PortfolioService],
    ) -> None:
        self._portfolio_factory = portfolio_service_factory

    # -- /portfolio ---------------------------------------------------------

    @app_commands.command(
        name="portfolio",
        description="View your (or another member's) long + short positions.",
    )
    @app_commands.describe(
        user="The member whose portfolio to look up. Defaults to you.",
    )
    async def portfolio(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Reply ephemerally with ``user``'s (or the invoker's) portfolio."""
        target_user = user if user is not None else interaction.user
        portfolio_service = self._portfolio_factory(guild_id_of(interaction))
        snapshot = await portfolio_service.portfolio_snapshot(
            user_id=str(target_user.id)
        )
        if snapshot is None:
            embed = discord.Embed(
                title="Portfolio",
                color=COLOR_NEUTRAL,
                description=(
                    "No account found yet — run `/daily` to open one and "
                    "claim your starter cash."
                ),
            )
        else:
            embed = build_portfolio_embed(snapshot)
        await interaction.response.send_message(embed=embed, ephemeral=True)
