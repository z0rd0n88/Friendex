"""Tests for :class:`PortfolioCog` — ``/portfolio [user]``.

Each command callback is invoked directly via ``Cog.command.callback(...)``
since ``dpytest`` simulates message events, not slash interactions. Assertions
walk ``interaction.response.send_message.call_args`` and the embed's
``to_dict()`` (Phase 10 review convention).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

from friendex.adapters.discord_bot.cogs.portfolio_cog import PortfolioCog
from friendex.adapters.discord_bot.embeds import COLOR_NEUTRAL
from friendex.application.snapshot_models import PortfolioSnapshot

# ---------------------------------------------------------------------------
# Helpers


def _snapshot(
    *,
    user_id: str = "9876543210",
    cash: Decimal = Decimal("9500.00"),
    net_worth: Decimal = Decimal("12345.67"),
    fund: Decimal = Decimal("500.00"),
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        user_id=user_id,
        cash_balance=cash,
        net_worth=net_worth,
        month_start_net_worth=Decimal("10000.00"),
        fund_balance=fund,
        long_positions={},
        short_positions={},
    )


def _send_call_kwargs(interaction) -> dict:  # type: ignore[no-untyped-def]
    """Return the kwargs dict of the last ``send_message`` call."""
    assert interaction.response.send_message.await_count >= 1
    return interaction.response.send_message.await_args.kwargs


def _make_member(user_id: int):  # type: ignore[no-untyped-def]
    """Build a stub ``discord.Member`` exposing the integer ``id``.

    ``discord.Member`` is hard to instantiate cleanly outside an active
    gateway; the cog only reads ``.id``, so a simple namespace stand-in
    suffices for tests.
    """
    from unittest.mock import MagicMock

    member = MagicMock(name="Member", spec=discord.Member)
    member.id = user_id
    return member


# ---------------------------------------------------------------------------
# /portfolio — default user fallback (invoker)


async def test_portfolio_defaults_to_invoking_user_when_user_arg_omitted(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Omitting the ``user`` arg routes to ``str(interaction.user.id)``."""
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    cog = PortfolioCog(portfolio_service_factory=portfolio_service_factory)
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    await PortfolioCog.portfolio.callback(cog, interaction, user=None)
    portfolio_service.portfolio_snapshot.assert_awaited_once_with(user_id="4242")


async def test_portfolio_uses_explicit_user_when_provided(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Passing a ``user`` arg routes to ``str(user.id)`` instead of the invoker."""
    portfolio_service.portfolio_snapshot.return_value = _snapshot(user_id="555")
    cog = PortfolioCog(portfolio_service_factory=portfolio_service_factory)
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    target = _make_member(555)
    await PortfolioCog.portfolio.callback(cog, interaction, user=target)
    portfolio_service.portfolio_snapshot.assert_awaited_once_with(user_id="555")


# ---------------------------------------------------------------------------
# /portfolio — per-guild factory routing


async def test_portfolio_routes_through_per_guild_factory(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
) -> None:
    """Factory is invoked with ``str(interaction.guild.id)``."""
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    seen_guild_ids: list[str] = []

    def factory(guild_id: str):
        seen_guild_ids.append(guild_id)
        return portfolio_service

    cog = PortfolioCog(portfolio_service_factory=factory)
    interaction = fake_interaction(user_id=1, guild_id=999)
    await PortfolioCog.portfolio.callback(cog, interaction, user=None)
    assert seen_guild_ids == ["999"]


# ---------------------------------------------------------------------------
# /portfolio — reply visibility (ephemeral) and embed shape


async def test_portfolio_reply_is_ephemeral_and_uses_portfolio_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    portfolio_service.portfolio_snapshot.return_value = _snapshot(
        cash=Decimal("1234.50"),
        net_worth=Decimal("9876.54"),
        fund=Decimal("100.00"),
    )
    cog = PortfolioCog(portfolio_service_factory=portfolio_service_factory)
    interaction = fake_interaction()
    await PortfolioCog.portfolio.callback(cog, interaction, user=None)
    kwargs = _send_call_kwargs(interaction)
    # Mutation-hardening: ephemeral flag is load-bearing.
    assert kwargs.get("ephemeral") is True
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$1,234.50" in rendered
    assert "$9,876.54" in rendered


async def test_portfolio_with_no_account_replies_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``portfolio_snapshot`` returning ``None`` still yields an ephemeral reply."""
    portfolio_service.portfolio_snapshot.return_value = None
    cog = PortfolioCog(portfolio_service_factory=portfolio_service_factory)
    interaction = fake_interaction()
    await PortfolioCog.portfolio.callback(cog, interaction, user=None)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    # The reply still carries an embed so the user sees structured output.
    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value


# ---------------------------------------------------------------------------
# Slash-command registration sanity


def test_portfolio_cog_registers_portfolio_app_command() -> None:
    """``/portfolio`` is registered as an ``app_commands.Command``."""
    import discord.app_commands as app_commands

    assert isinstance(PortfolioCog.portfolio, app_commands.Command)
