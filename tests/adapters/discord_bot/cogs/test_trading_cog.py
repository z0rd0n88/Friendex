"""Tests for :class:`TradingCog` — ``/buy``, ``/sell``, ``/short``, ``/cover``.

Each command callback is invoked directly via ``Cog.command.callback(...)``
since ``dpytest`` simulates message events, not slash interactions. Assertions
walk ``interaction.response.send_message.call_args`` and the embed's
``to_dict()`` (Phase 10 review convention).

Service calls use **positional** ``(actor_id, target_id, shares)`` per the
Phase 8c digest contract — the cog must NOT introduce kwargs that do not
exist on :class:`~friendex.application.trading_service.TradingService`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import discord
import pytest
from discord import app_commands

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

from friendex.adapters.discord_bot.cogs.trading_cog import TradingCog
from friendex.adapters.discord_bot.embeds import COLOR_SUCCESS
from friendex.application.trade_results import (
    BuyResult,
    CoverResult,
    SellResult,
    ShortResult,
)
from friendex.domain.errors import (
    InsufficientFunds,
    NoPosition,
    OptedOut,
    PositionFrozen,
)
from friendex.domain.models import LongPosition, ShortPosition

# ---------------------------------------------------------------------------
# Helpers


def _buy_result() -> BuyResult:
    return BuyResult(
        buyer_id="1",
        target_id="555",
        shares=3,
        price_per_share=Decimal("100.00"),
        total_cost=Decimal("300.00"),
        old_price=Decimal("100.00"),
        new_price=Decimal("101.50"),
        new_cash_balance=Decimal("9700.00"),
        position_after=LongPosition(
            target_user_id="555", shares=3, avg_entry=Decimal("100.00")
        ),
    )


def _sell_result() -> SellResult:
    return SellResult(
        seller_id="1",
        target_id="555",
        shares=2,
        price_per_share=Decimal("105.00"),
        total_revenue=Decimal("210.00"),
        old_price=Decimal("105.00"),
        new_price=Decimal("104.00"),
        new_cash_balance=Decimal("9910.00"),
        position_after=None,
    )


def _short_result() -> ShortResult:
    return ShortResult(
        shorter_id="1",
        target_id="555",
        shares=2,
        price_per_share=Decimal("100.00"),
        notional=Decimal("200.00"),
        locked_cash=Decimal("200.00"),
        locked_fund=Decimal("0.00"),
        old_price=Decimal("100.00"),
        new_price=Decimal("99.00"),
        new_cash_balance=Decimal("9800.00"),
        new_fund_balance=Decimal("0.00"),
        position_after=ShortPosition(
            target_user_id="555",
            shares=2,
            entry_price=Decimal("100.00"),
            locked_cash=Decimal("200.00"),
            locked_fund=Decimal("0.00"),
            created_at=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        ),
    )


def _cover_result() -> CoverResult:
    return CoverResult(
        coverer_id="1",
        target_id="555",
        shares=2,
        price_per_share=Decimal("95.00"),
        cost=Decimal("190.00"),
        pnl=Decimal("10.00"),
        released_cash=Decimal("200.00"),
        released_fund=Decimal("0.00"),
        old_price=Decimal("95.00"),
        new_price=Decimal("96.00"),
        new_cash_balance=Decimal("10010.00"),
        new_fund_balance=Decimal("0.00"),
        position_after=None,
    )


def _send_call_kwargs(interaction) -> dict:  # type: ignore[no-untyped-def]
    """Return the kwargs dict of the last user-visible reply.

    Wave 1 (issue #82 H13) routed every cog reply through
    ``interaction.followup.send`` after ``interaction.response.defer(...)``.
    The helper inspects ``followup.send`` (the new reply seam).
    """
    assert interaction.followup.send.await_count >= 1
    return interaction.followup.send.await_args.kwargs


def _make_member(user_id: int):  # type: ignore[no-untyped-def]
    """Build a stub ``discord.Member`` exposing the integer ``id``."""
    from unittest.mock import MagicMock

    member = MagicMock(name="Member", spec=discord.Member)
    member.id = user_id
    return member


# ---------------------------------------------------------------------------
# /buy — PUBLIC


async def test_buy_calls_trading_service_with_positional_args(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Service call uses positional ``(buyer_id, target_id, shares)``."""
    trading_service.buy.return_value = _buy_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    target = _make_member(555)
    await TradingCog.buy.callback(cog, interaction, user=target, shares=3)
    trading_service.buy.assert_awaited_once_with("42", "555", 3)


async def test_buy_routes_through_per_guild_factory(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
) -> None:
    """Factory is invoked with ``str(interaction.guild.id)``."""
    trading_service.buy.return_value = _buy_result()
    seen_guild_ids: list[str] = []

    def factory(guild_id: str):
        seen_guild_ids.append(guild_id)
        return trading_service

    cog = TradingCog(trading_service_factory=factory)
    interaction = fake_interaction(user_id=1, guild_id=777)
    target = _make_member(555)
    await TradingCog.buy.callback(cog, interaction, user=target, shares=1)
    assert seen_guild_ids == ["777"]


async def test_buy_reply_is_public_and_uses_buy_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/buy`` is PUBLIC (no ``ephemeral=True``) and renders the buy embed."""
    trading_service.buy.return_value = _buy_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    await TradingCog.buy.callback(cog, interaction, user=target, shares=3)
    kwargs = _send_call_kwargs(interaction)
    # Mutation-hardening: /buy is PUBLIC. ``ephemeral`` must be False or unset.
    assert kwargs.get("ephemeral", False) is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    # Mutation-hardening: assert content that the *buy* embed (not sell/short/
    # cover) produces — "Buy Confirmed" title is unique to build_buy_*.
    assert data["title"] == "Buy Confirmed"
    assert "$300.00" in rendered  # total_cost


async def test_buy_propagates_insufficient_funds(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """DomainError escapes the cog uncaught — Phase 13 owns the handler."""
    trading_service.buy.side_effect = InsufficientFunds(
        need=Decimal("999.00"), have=Decimal("100.00")
    )
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    with pytest.raises(InsufficientFunds):
        await TradingCog.buy.callback(cog, interaction, user=target, shares=10)
    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# /sell — PUBLIC


async def test_sell_calls_trading_service_with_positional_args(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.sell.return_value = _sell_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    target = _make_member(555)
    await TradingCog.sell.callback(cog, interaction, user=target, shares=2)
    trading_service.sell.assert_awaited_once_with("42", "555", 2)


async def test_sell_reply_is_public_and_uses_sell_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.sell.return_value = _sell_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    await TradingCog.sell.callback(cog, interaction, user=target, shares=2)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral", False) is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value
    assert data["title"] == "Sell Confirmed"
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$210.00" in rendered  # total_revenue


async def test_sell_propagates_no_position(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.sell.side_effect = NoPosition(target_id="555", position_type="long")
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    with pytest.raises(NoPosition):
        await TradingCog.sell.callback(cog, interaction, user=target, shares=1)
    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# /short — PUBLIC


async def test_short_calls_trading_service_with_positional_args(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.short.return_value = _short_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    target = _make_member(555)
    await TradingCog.short.callback(cog, interaction, user=target, shares=2)
    trading_service.short.assert_awaited_once_with("42", "555", 2)


async def test_short_reply_is_public_and_uses_short_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.short.return_value = _short_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    await TradingCog.short.callback(cog, interaction, user=target, shares=2)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral", False) is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value
    assert data["title"] == "Short Opened"
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$200.00" in rendered  # notional


async def test_short_propagates_opted_out(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.short.side_effect = OptedOut(target_id="555")
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    with pytest.raises(OptedOut):
        await TradingCog.short.callback(cog, interaction, user=target, shares=1)
    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# /cover — PUBLIC


async def test_cover_calls_trading_service_with_positional_args(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.cover.return_value = _cover_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    target = _make_member(555)
    await TradingCog.cover.callback(cog, interaction, user=target, shares=2)
    trading_service.cover.assert_awaited_once_with("42", "555", 2)


async def test_cover_reply_is_public_and_uses_cover_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.cover.return_value = _cover_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    await TradingCog.cover.callback(cog, interaction, user=target, shares=2)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral", False) is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value
    assert data["title"] == "Short Covered"
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$190.00" in rendered  # cost


async def test_cover_propagates_position_frozen(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    trading_service.cover.side_effect = PositionFrozen(target_id="555")
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    with pytest.raises(PositionFrozen):
        await TradingCog.cover.callback(cog, interaction, user=target, shares=1)
    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Slash-command registration sanity


def test_trading_cog_registers_all_four_app_commands() -> None:
    """All four commands are registered as ``app_commands.Command`` instances."""
    import discord.app_commands as app_commands

    assert isinstance(TradingCog.buy, app_commands.Command)
    assert isinstance(TradingCog.sell, app_commands.Command)
    assert isinstance(TradingCog.short, app_commands.Command)
    assert isinstance(TradingCog.cover, app_commands.Command)


# ---------------------------------------------------------------------------
# Wave 1: defer(ephemeral=False) + guild_only — pinning the boundary contract


async def test_buy_defers_publicly_before_service_call(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/buy`` defers with ``ephemeral=False`` (public reply) before the service.

    Wave 1 (#82 H13): action commands stay PUBLIC, so the defer also reserves
    a public reply slot. ``ephemeral=False`` is load-bearing on the defer —
    if the defer is ephemeral the followup must also be ephemeral.
    """
    trading_service.buy.return_value = _buy_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)

    await TradingCog.buy.callback(cog, interaction, user=target, shares=3)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)


async def test_buy_defer_runs_before_service_call(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """The defer must happen BEFORE the service call (3 s ack deadline)."""
    from unittest.mock import MagicMock

    trading_service.buy.return_value = _buy_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)

    parent = MagicMock(name="parent")
    parent.attach_mock(interaction.response.defer, "defer")
    parent.attach_mock(trading_service.buy, "buy")

    await TradingCog.buy.callback(cog, interaction, user=target, shares=3)

    names = [c[0] for c in parent.mock_calls if c[0]]
    assert names[0] == "defer"
    assert names.index("defer") < names.index("buy")


def test_trading_cog_commands_are_guild_only() -> None:
    """Wave 1 (#82 H14): every command refuses DM dispatch."""
    for cmd in (TradingCog.buy, TradingCog.sell, TradingCog.short, TradingCog.cover):
        assert getattr(cmd, "guild_only", None) is True, (
            f"{cmd.name}: must be decorated @app_commands.guild_only()"
        )


# ---------------------------------------------------------------------------
# Wave 1 review LOW-2: defer(ephemeral=False) coverage matrix
#
# The original Wave 1 cog tests pinned ``/buy`` defer-public; the other
# three trading commands relied on the public-reply test asserting the
# followup's ephemeral flag, which is a softer signal (a regression that
# flips defer to ``ephemeral=True`` would surface as a followup mismatch
# downstream rather than a direct diagnostic). Close the matrix with one
# explicit defer assertion per command.


async def test_sell_defers_publicly_before_service_call(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/sell`` defers with ``ephemeral=False`` (public reply)."""
    trading_service.sell.return_value = _sell_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)

    await TradingCog.sell.callback(cog, interaction, user=target, shares=2)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)


async def test_short_defers_publicly_before_service_call(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/short`` defers with ``ephemeral=False`` (public reply)."""
    trading_service.short.return_value = _short_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)

    await TradingCog.short.callback(cog, interaction, user=target, shares=2)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)


async def test_cover_defers_publicly_before_service_call(
    fake_interaction,  # type: ignore[no-untyped-def]
    trading_service: AsyncMock,
    trading_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/cover`` defers with ``ephemeral=False`` (public reply)."""
    trading_service.cover.return_value = _cover_result()
    cog = TradingCog(trading_service_factory=trading_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)

    await TradingCog.cover.callback(cog, interaction, user=target, shares=2)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)


# ---------------------------------------------------------------------------
# Issue #84 L — every ``shares`` parameter has a bounded upper limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command_name", ["buy", "sell", "short", "cover"])
def test_shares_parameter_is_upper_bounded(command_name: str) -> None:
    """Issue #84 L — every ``shares`` Range carries a finite ``max_value``.

    The pre-fix annotation ``Range[int, 1, None]`` let a caller pass
    ``2**53 - 1`` shares; the service then performed Decimal arithmetic
    over a 16-digit integer (``Decimal(shares) * price``), tying up the
    event loop and blocking every other coroutine. A bounded
    ``Range[int, 1, 1_000_000]`` keeps the worst-case integer small
    enough that the arithmetic stays sub-millisecond.

    The parameter exposes ``min_value`` / ``max_value`` via the discord.py
    :class:`app_commands.Command._params` mapping; both must be set.
    """
    command = getattr(TradingCog, command_name)
    assert isinstance(command, app_commands.Command)
    shares_param = command._params["shares"]
    assert shares_param.min_value == 1, (
        f"/{command_name} shares min_value must remain 1"
    )
    assert shares_param.max_value is not None, (
        f"/{command_name} shares must be upper-bounded "
        "(was unbounded Range[int, 1, None])"
    )
    # Sanity bound — the actual cap is 1_000_000 per the remediation plan;
    # the test asserts the *shape* (any finite cap) so future re-tuning
    # without going unbounded again does not break the regression contract.
    assert shares_param.max_value <= 1_000_000, (
        f"/{command_name} shares max_value should not exceed 1_000_000"
    )
