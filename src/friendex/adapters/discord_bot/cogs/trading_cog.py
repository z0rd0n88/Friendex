"""``TradingCog`` — ``/buy``, ``/sell``, ``/short``, ``/cover`` slash commands.

The trading cog is the mutation-heavy slice of the Phase 11 cog surface. It
exposes:

* ``/buy <user> <shares>`` — open or add to a long position (public).
* ``/sell <user> <shares>`` — close (some or all of) a long position (public).
* ``/short <user> <shares>`` — open or add to a short position (public; the
  service applies a short/cover cooldown).
* ``/cover <user> <shares>`` — close (some or all of) a short position
  (public; cooldown-gated like ``/short``).

All four commands are PUBLIC (action commands stay visible in-channel per the
project ``CLAUDE.md`` reply-visibility rule). Every send carries
``allowed_mentions=AllowedMentions.none()`` for I2 consistency — although the
confirmation embeds today only mention real Discord snowflakes (from service
result DTOs, not user-provided text), the carry-forward bar applies uniformly
to every send in the cog package.

**Wave 1: defer + followup** (issue #82 H13). Every callback ``await
interaction.response.defer(ephemeral=False)`` as its FIRST line, then replies
via ``interaction.followup.send(...)``. ``defer(ephemeral=False)`` keeps the
reply visible in-channel — Discord requires a 3 s ack and the service call
(which acquires a per-guild lock and may flush to SQLite) can exceed that
window.

**Wave 1: ``@app_commands.guild_only()``** (issue #82 H14). Every command
refuses DM dispatch at the Discord level.

The cog holds a per-guild :class:`TradingService` *factory*, not an instance
(Phase 9 service_factory convention). On each invocation it calls the factory
with ``guild_id_of(interaction)`` and delegates to the service. Service calls
are **positional** ``(actor_id, target_id, shares)`` per the Phase 8c digest
contract — the service does NOT accept ``buyer_id=`` / ``seller_id=`` /
``shorter_id=`` / ``coverer_id=`` kwargs.

Domain errors **propagate uncaught**; Phase 13 owns the tree-wide
``app_commands`` error handler. The cog must not ``try/except DomainError``
and must not call :func:`build_error_embed`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of
from friendex.adapters.discord_bot.embeds import (
    build_buy_confirmation_embed,
    build_cover_confirmation_embed,
    build_sell_confirmation_embed,
    build_short_confirmation_embed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.trading_service import TradingService

# Issue #84 L — every ``shares`` ``Range`` carries this finite upper bound so
# a malicious caller cannot pass ``2**53 - 1`` and tie up the event loop in
# Decimal arithmetic over a 16-digit integer. The ceiling is well above any
# realistic in-game position size (initial cash $10 000 / min price $70 puts
# the natural cap around 142 shares; a generous five-order-of-magnitude
# headroom leaves room for future economy rebalances without re-tuning).
_MAX_SHARES = 1_000_000


class TradingCog(commands.Cog):
    """Buy / sell / short / cover slash commands.

    Ctor takes a per-guild :class:`TradingService` factory so a single cog
    instance serves every guild the bot is in. Phase 13/14 wires the
    factory — it constructs each per-guild service on demand against the
    shared persistence + lock-manager singletons.
    """

    def __init__(
        self,
        *,
        trading_service_factory: Callable[[str], TradingService],
    ) -> None:
        self._trading_factory = trading_service_factory

    # -- /buy ---------------------------------------------------------------

    @app_commands.command(
        name="buy",
        description="Buy shares of a member's stock (open or add to a long).",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        user="The member whose stock you want to buy.",
        shares="Number of shares to buy (must be at least 1).",
    )
    async def buy(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        shares: app_commands.Range[int, 1, _MAX_SHARES],
    ) -> None:
        """Open or add to a long position on ``user`` and confirm publicly."""
        await interaction.response.defer(ephemeral=False)
        trading_service = self._trading_factory(guild_id_of(interaction))
        result = await trading_service.buy(
            str(interaction.user.id), str(user.id), shares
        )
        embed = build_buy_confirmation_embed(result)
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /sell --------------------------------------------------------------

    @app_commands.command(
        name="sell",
        description="Sell shares of a member's stock (close some/all of a long).",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        user="The member whose stock you want to sell.",
        shares="Number of shares to sell (must be at least 1).",
    )
    async def sell(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        shares: app_commands.Range[int, 1, _MAX_SHARES],
    ) -> None:
        """Close some/all of a long position on ``user`` and confirm publicly."""
        await interaction.response.defer(ephemeral=False)
        trading_service = self._trading_factory(guild_id_of(interaction))
        result = await trading_service.sell(
            str(interaction.user.id), str(user.id), shares
        )
        embed = build_sell_confirmation_embed(result)
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /short -------------------------------------------------------------

    @app_commands.command(
        name="short",
        description="Open a short position on a member's stock (15-min cooldown).",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        user="The member whose stock you want to short.",
        shares="Number of shares to short (must be at least 1).",
    )
    async def short(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        shares: app_commands.Range[int, 1, _MAX_SHARES],
    ) -> None:
        """Open or add to a short position on ``user`` and confirm publicly."""
        await interaction.response.defer(ephemeral=False)
        trading_service = self._trading_factory(guild_id_of(interaction))
        result = await trading_service.short(
            str(interaction.user.id), str(user.id), shares
        )
        embed = build_short_confirmation_embed(result)
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /cover -------------------------------------------------------------

    @app_commands.command(
        name="cover",
        description="Cover (close) a short position on a member's stock.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        user="The member whose short you want to cover.",
        shares="Number of shares to cover (must be at least 1).",
    )
    async def cover(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        shares: app_commands.Range[int, 1, _MAX_SHARES],
    ) -> None:
        """Close some/all of a short position on ``user`` and confirm publicly."""
        await interaction.response.defer(ephemeral=False)
        trading_service = self._trading_factory(guild_id_of(interaction))
        result = await trading_service.cover(
            str(interaction.user.id), str(user.id), shares
        )
        embed = build_cover_confirmation_embed(result)
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
