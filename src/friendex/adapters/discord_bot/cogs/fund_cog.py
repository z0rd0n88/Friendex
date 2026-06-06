"""``FundCog`` / ``FundGroup`` — ``/fund <subcommand>`` slash commands.

The fund cog hosts the personal hedge-fund interface for Phase 11c:

* ``/fund create [name]`` — create or rename the invoker's personal fund
  (public confirmation; mutation).
* ``/fund info [user]`` — view the invoker's (or another member's) fund;
  ephemeral.
* ``/fund withdraw <amount>`` — move cash out of the invoker's fund into
  their trading cash (public; the service applies an early-withdrawal
  penalty except on the 1st of the month).
* ``/fund send_events <amount>`` — donate cash from the invoker's fund to the
  per-guild events-wallet treasury (public; penalty-exempt).
* ``/fund invest <user> <amount>`` — invest cash from the invoker's
  trading account into another member's hedge fund (public; the service
  debits the invoker, credits the target fund's balance, and records the
  invoker's stake on the fund). Self-invest is rejected as
  :class:`InvalidAmount` per Phase 17b §Q2; an insufficient cash balance
  surfaces as :class:`InsufficientFunds`.

The :class:`FundGroup` is an :class:`app_commands.Group` (``name="fund"``) so
``/fund <sub>`` is the natural slash namespace. The accompanying
:class:`FundCog` is a thin :class:`commands.Cog` wrapper holding the group as
``self.group`` so Phase 13's bot-tree wiring can call
``bot.tree.add_command(cog.group)`` uniformly.

**I2 carry-forward (Phase 10 review).** Every ``send_message`` /
``followup.send`` call passes ``allowed_mentions=AllowedMentions.none()``
because the fund's user-provided ``name`` is echoed into the embed title and
description. A mention in a user-input string MUST NOT trigger a Discord
notification; the kwarg is load-bearing.

**Money invariant (Phase 3.1).** Discord slash command float amounts are
converted to :class:`Decimal` via ``Decimal(str(amount))`` — never
``Decimal(amount)`` directly, which would carry IEEE-754 noise.

**Wave 1 (#82 H13)**: every subcommand defers (``info`` ephemerally,
mutations publicly) before the service call, then replies via
``interaction.followup.send``. **Wave 1 (#82 H14)**:
``@app_commands.guild_only()`` is applied to the parent
``FundGroup`` — Discord refuses DM dispatch for every subcommand under a
guild-only group.

Domain errors **propagate uncaught**; Phase 13 owns the tree-wide
``app_commands`` error handler.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of
from friendex.adapters.discord_bot.embeds import (
    COLOR_NEUTRAL,
    COLOR_SUCCESS,
    build_fund_info_embed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.fund_service import FundService
    from friendex.application.snapshot_models import FundInfoResult

# Maximum fund-name length enforced at the Discord interaction layer via
# ``app_commands.Range[str, 1, _MAX_FUND_NAME_LEN]``.  Discord embed titles
# have a 256-char hard cap; the ``build_fund_info_embed`` builder also applies
# this constant as a safety clamp for names stored before this guard was
# deployed.
_MAX_FUND_NAME_LEN: int = 32


@app_commands.guild_only()
class FundGroup(app_commands.Group):
    """``app_commands.Group`` exposing the ``/fund`` subcommand namespace.

    The group is its own class (not a module-level instance) so Phase 13 can
    instantiate one per bot/guild scope and register it via
    ``bot.tree.add_command(group_instance)``. Subclassing also lets us hold
    the per-guild :class:`FundService` factory as instance state without
    monkey-patching attributes onto a stock ``app_commands.Group``.

    The ``@app_commands.guild_only()`` decorator on the group propagates to
    every subcommand under it — Discord refuses DM dispatch for the whole
    ``/fund`` namespace.

    Ctor takes a per-guild :class:`FundService` factory. APY values and
    penalty state are returned by :meth:`FundService.fund_info` as a
    :class:`~friendex.application.snapshot_models.FundInfoResult` so the
    cog does not need access to :class:`~friendex.adapters.config.Settings`.
    """

    def __init__(
        self,
        *,
        fund_service_factory: Callable[[str], FundService],
    ) -> None:
        super().__init__(name="fund", description="Hedge fund management")
        self._fund_factory = fund_service_factory

    # -- /fund create --------------------------------------------------------

    @app_commands.command(
        name="create",
        description="Create or rename your personal hedge fund.",
    )
    @app_commands.describe(
        name="Optional new name for your fund (leave blank to use the default).",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, _MAX_FUND_NAME_LEN] | None = None,
    ) -> None:
        """Create or rename the invoker's personal fund and confirm publicly.

        Renders the confirmation via :func:`build_fund_info_embed` after the
        create call. Calls :meth:`FundService.fund_info` to get the display
        DTO — one extra read on a fund we just wrote, but it keeps the embed
        path consistent with ``/fund info`` and avoids needing
        :class:`Settings` in the cog.
        """
        await interaction.response.defer(ephemeral=False)
        now = datetime.now(tz=UTC)
        fund_service = self._fund_factory(guild_id_of(interaction))
        await fund_service.create_or_rename(str(interaction.user.id), name=name)
        result = await fund_service.fund_info(str(interaction.user.id), now)
        embed = (
            self._build_fund_info_embed_for(result)
            if result is not None
            else discord.Embed(
                title="Hedge Fund", color=COLOR_SUCCESS, description="Fund created."
            )
        )
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /fund info ----------------------------------------------------------

    @app_commands.command(
        name="info",
        description="View your (or another member's) hedge fund summary.",
    )
    @app_commands.describe(
        user="The member whose fund to look up. Defaults to you.",
    )
    async def info(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
    ) -> None:
        """Reply ephemerally with the fund summary.

        Target defaults to the invoker. ``None`` from
        :meth:`FundService.fund_info` renders a small inline ``COLOR_NEUTRAL``
        embed — mirroring the no-fund / brand-new account path in
        :class:`AccountCog.balance` and :class:`PortfolioCog.portfolio`.
        """
        await interaction.response.defer(ephemeral=True)
        target_user = user if user is not None else interaction.user
        now = datetime.now(tz=UTC)
        fund_service = self._fund_factory(guild_id_of(interaction))
        result = await fund_service.fund_info(str(target_user.id), now)
        if result is None:
            embed = discord.Embed(
                title="Hedge Fund",
                color=COLOR_NEUTRAL,
                description=("No hedge fund yet — run `/fund create` to open one."),
            )
        else:
            embed = self._build_fund_info_embed_for(result)
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /fund withdraw ------------------------------------------------------

    @app_commands.command(
        name="withdraw",
        description="Withdraw cash from your hedge fund back into your trading cash.",
    )
    @app_commands.describe(
        amount="Amount to withdraw (USD).",
    )
    async def withdraw(
        self,
        interaction: discord.Interaction,
        amount: float,
    ) -> None:
        """Withdraw ``amount`` from the invoker's fund and confirm publicly.

        ``Decimal(str(amount))`` — never ``Decimal(amount)`` directly — avoids
        IEEE-754 noise; ``datetime.now(tz=UTC)`` is the canonical Phase 3.1
        UTC-aware boundary value (the service uses it to decide whether the
        early-withdrawal penalty applies — spec line 1434 ``if now.day != 1``).
        """
        await interaction.response.defer(ephemeral=False)
        fund_service = self._fund_factory(guild_id_of(interaction))
        decimal_amount = Decimal(str(amount))
        now = datetime.now(tz=UTC)
        await fund_service.withdraw(str(interaction.user.id), decimal_amount, now)
        embed = discord.Embed(
            title="Fund Withdrawal",
            color=COLOR_NEUTRAL,
            description=f"Withdrew **${decimal_amount:,.2f}** from your hedge fund.",
        )
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /fund send_events ---------------------------------------------------

    @app_commands.command(
        name="send_events",
        description="Donate cash from your hedge fund to the events wallet.",
    )
    @app_commands.describe(
        amount="Amount to send to the events wallet (USD).",
    )
    async def send_events(
        self,
        interaction: discord.Interaction,
        amount: float,
    ) -> None:
        """Donate ``amount`` from the invoker's fund to the events wallet.

        Exempt from the early-withdrawal penalty (spec line 1475). The cog
        uses ``Decimal(str(amount))`` to avoid IEEE-754 noise.
        """
        await interaction.response.defer(ephemeral=False)
        fund_service = self._fund_factory(guild_id_of(interaction))
        decimal_amount = Decimal(str(amount))
        await fund_service.send_to_events(str(interaction.user.id), decimal_amount)
        embed = discord.Embed(
            title="Events Wallet Donation",
            color=COLOR_NEUTRAL,
            description=(
                f"Sent **${decimal_amount:,.2f}** from your fund to the events wallet."
            ),
        )
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /fund invest --------------------------------------------------------

    @app_commands.command(
        name="invest",
        description="Invest cash from your account into another member's hedge fund.",
    )
    @app_commands.describe(
        user="The member whose fund to invest in.",
        amount="Amount to invest (USD).",
    )
    async def invest(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        amount: float,
    ) -> None:
        """Invest ``amount`` from the invoker's cash into ``user``'s fund.

        Calls :meth:`FundService.invest`, which debits the invoker's
        trading cash, credits the target fund's balance, and records the
        invoker as an investor on the fund. ``Decimal(str(amount))`` —
        never ``Decimal(amount)`` — avoids IEEE-754 noise (Phase 3.1
        money invariant).

        Domain errors propagate uncaught (the cog never wraps in
        ``try/except``): a non-positive amount, an absent fund, a
        manager attempting to self-invest (Phase 17b §Q2), or an
        investor account without enough cash all raise the appropriate
        :class:`~friendex.domain.errors.DomainError`; Phase 13's
        tree-wide ``app_commands`` error handler renders them for the
        user.
        """
        await interaction.response.defer(ephemeral=False)
        fund_service = self._fund_factory(guild_id_of(interaction))
        decimal_amount = Decimal(str(amount))
        await fund_service.invest(
            str(interaction.user.id),
            str(user.id),
            decimal_amount,
        )
        embed = discord.Embed(
            title="Invested",
            color=COLOR_SUCCESS,
            description=(
                f"Invested **${decimal_amount:,.2f}** into <@{user.id}>'s hedge fund."
            ),
        )
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- helpers -------------------------------------------------------------

    def _build_fund_info_embed_for(self, result: FundInfoResult) -> discord.Embed:
        """Render the ``/fund info`` embed from a :class:`FundInfoResult` DTO."""
        return build_fund_info_embed(
            fund=result.fund,
            base_apy=result.base_apy,
            effective_apy=result.effective_apy,
            has_penalty=result.has_penalty,
        )


class FundCog(commands.Cog):
    """Thin :class:`commands.Cog` wrapper exposing :class:`FundGroup`.

    Phase 13 reads ``cog.group`` and calls
    ``bot.tree.add_command(cog.group)``. The cog itself does not register
    individual app commands — the group owns its own subcommand surface.
    """

    def __init__(
        self,
        *,
        fund_service_factory: Callable[[str], FundService],
    ) -> None:
        self.group: FundGroup = FundGroup(
            fund_service_factory=fund_service_factory,
        )
