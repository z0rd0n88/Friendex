"""Tests for :class:`AccountCog` — ``/balance``, ``/optin``, ``/optout``.

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

from friendex.adapters.discord_bot.cogs.account_cog import AccountCog
from friendex.adapters.discord_bot.embeds import COLOR_NEUTRAL, COLOR_SUCCESS
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


# ---------------------------------------------------------------------------
# /balance


async def test_balance_calls_portfolio_snapshot_for_invoking_user(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    await AccountCog.balance.callback(cog, interaction)
    portfolio_service.portfolio_snapshot.assert_awaited_once_with("4242")


async def test_balance_routes_through_per_guild_factory(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """The factory must be called with ``str(interaction.guild.id)``."""
    portfolio_service.portfolio_snapshot.return_value = _snapshot()
    seen_guild_ids: list[str] = []

    def factory(guild_id: str):
        seen_guild_ids.append(guild_id)
        return portfolio_service

    cog = AccountCog(
        portfolio_service_factory=factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=1, guild_id=999)
    await AccountCog.balance.callback(cog, interaction)
    assert seen_guild_ids == ["999"]


async def test_balance_reply_is_ephemeral_and_uses_balance_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    portfolio_service.portfolio_snapshot.return_value = _snapshot(
        cash=Decimal("1234.50")
    )
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.balance.callback(cog, interaction)
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


async def test_balance_with_no_account_replies_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``portfolio_snapshot`` returning ``None`` still yields an ephemeral reply."""
    portfolio_service.portfolio_snapshot.return_value = None
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.balance.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    # The reply still carries an embed so the user sees structured output.
    assert isinstance(kwargs.get("embed"), discord.Embed)


# ---------------------------------------------------------------------------
# /optin · /optout


async def test_optin_calls_set_opt_in_true(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    await AccountCog.optin.callback(cog, interaction)
    activity_service.set_opt_in.assert_awaited_once_with("4242", True)


async def test_optout_calls_set_opt_in_false(
    fake_interaction,  # type: ignore[no-untyped-def]
    activity_service: AsyncMock,
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction(user_id=4242, guild_id=99)
    await AccountCog.optout.callback(cog, interaction)
    activity_service.set_opt_in.assert_awaited_once_with("4242", False)


async def test_optin_reply_is_ephemeral_success_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.optin.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True  # mutation-hardening
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


async def test_optout_reply_is_ephemeral_success_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    portfolio_service_factory,  # type: ignore[no-untyped-def]
    activity_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    cog = AccountCog(
        portfolio_service_factory=portfolio_service_factory,
        activity_service_factory=activity_service_factory,
    )
    interaction = fake_interaction()
    await AccountCog.optout.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True  # mutation-hardening
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


# ---------------------------------------------------------------------------
# Slash-command registration sanity


def test_account_cog_registers_balance_optin_optout_app_commands() -> None:
    """All three commands are registered as ``app_commands.Command`` instances."""
    import discord.app_commands as app_commands

    assert isinstance(AccountCog.balance, app_commands.Command)
    assert isinstance(AccountCog.optin, app_commands.Command)
    assert isinstance(AccountCog.optout, app_commands.Command)
