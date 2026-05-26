"""Tests for :class:`FundCog` / :class:`FundGroup` — ``/fund <subcommand>``.

``/fund`` is exposed as an :class:`app_commands.Group` with five
sub-commands: ``create``, ``info``, ``withdraw``, ``send_events``, ``invest``.
Subcommands are invoked directly via ``Group.command.callback(group, ...)``
since ``dpytest`` simulates message events, not slash interactions.

I2 carry-forward (Phase 10 review): every ``send_message`` and
``followup.send`` call MUST pass
``allowed_mentions=discord.AllowedMentions.none()`` because the fund embed
echoes user-provided ``fund.name`` into the title/description.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import discord
import pytest

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

from friendex.adapters.config import Settings
from friendex.adapters.discord_bot.cogs.fund_cog import FundCog, FundGroup
from friendex.adapters.discord_bot.embeds import COLOR_NEUTRAL
from friendex.domain.errors import (
    AlreadyOptedIn,
    FundInsufficientBalance,
    InvalidAmount,
)
from friendex.domain.models import HedgeFund

# ---------------------------------------------------------------------------
# Helpers


def _settings(**overrides: object) -> Settings:
    """Build a :class:`Settings` with a non-placeholder token for tests."""
    base: dict[str, object] = {
        "discord_token": "test-token-not-placeholder",
        "hedge_fund_base_apy": 0.15,
        "early_withdraw_penalty": 0.05,
        "penalty_duration_days": 14,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _hedge_fund(
    *,
    fund_id: str = "42",
    name: str = "Fund 42",
    manager_id: str = "42",
    cash: Decimal = Decimal("1000.00"),
) -> HedgeFund:
    return HedgeFund(
        fund_id=fund_id,
        name=name,
        manager_id=manager_id,
        cash_balance=cash,
        investors={},
    )


def _send_call_kwargs(interaction) -> dict:  # type: ignore[no-untyped-def]
    """Return the kwargs dict of the last ``send_message`` call."""
    assert interaction.response.send_message.await_count >= 1
    return interaction.response.send_message.await_args.kwargs


def _make_member(user_id: int):  # type: ignore[no-untyped-def]
    """Build a stub ``discord.Member`` exposing the integer ``id``."""
    from unittest.mock import MagicMock

    member = MagicMock(name="Member", spec=discord.Member)
    member.id = user_id
    return member


def _build_group(
    fund_service_factory,  # type: ignore[no-untyped-def]
    settings: Settings | None = None,
) -> FundGroup:
    return FundGroup(
        fund_service_factory=fund_service_factory,
        settings=settings or _settings(),
    )


# ---------------------------------------------------------------------------
# /fund create — PUBLIC (mutation)


async def test_fund_create_calls_create_or_rename_with_name(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund = _hedge_fund(name="My Cool Fund")
    fund_service.create_or_rename.return_value = fund
    group = _build_group(fund_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    await FundGroup.create.callback(group, interaction, name="My Cool Fund")
    fund_service.create_or_rename.assert_awaited_once_with("42", name="My Cool Fund")


async def test_fund_create_defaults_name_to_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund_service.create_or_rename.return_value = _hedge_fund()
    group = _build_group(fund_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    await FundGroup.create.callback(group, interaction, name=None)
    fund_service.create_or_rename.assert_awaited_once_with("42", name=None)


async def test_fund_create_routes_through_per_guild_factory(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
) -> None:
    fund_service.create_or_rename.return_value = _hedge_fund()
    seen_guild_ids: list[str] = []

    def factory(guild_id: str):
        seen_guild_ids.append(guild_id)
        return fund_service

    group = _build_group(factory)
    interaction = fake_interaction(user_id=1, guild_id=777)
    await FundGroup.create.callback(group, interaction, name=None)
    assert seen_guild_ids == ["777"]


async def test_fund_create_reply_is_public_with_allowed_mentions_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund_service.create_or_rename.return_value = _hedge_fund(name="Funky Town")
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    await FundGroup.create.callback(group, interaction, name="Funky Town")
    kwargs = _send_call_kwargs(interaction)
    # PUBLIC reply (mutation visibility).
    assert kwargs.get("ephemeral", False) is False
    # I2 carry-forward: every send MUST suppress mentions (fund.name echoed).
    assert "allowed_mentions" in kwargs
    assert isinstance(kwargs["allowed_mentions"], discord.AllowedMentions)
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "Funky Town" in rendered


async def test_fund_create_propagates_already_opted_in(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """DomainError propagates uncaught — Phase 13 owns the handler."""
    fund_service.create_or_rename.side_effect = AlreadyOptedIn()
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    with pytest.raises(AlreadyOptedIn):
        await FundGroup.create.callback(group, interaction, name=None)
    interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# /fund info — EPHEMERAL (read)


async def test_fund_info_defaults_to_invoking_user(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund_service.fund_info.return_value = _hedge_fund(fund_id="42", manager_id="42")
    group = _build_group(fund_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    await FundGroup.info.callback(group, interaction, user=None)
    fund_service.fund_info.assert_awaited_once_with(user_id="42")


async def test_fund_info_uses_explicit_user_when_provided(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund_service.fund_info.return_value = _hedge_fund(fund_id="555", manager_id="555")
    group = _build_group(fund_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    target = _make_member(555)
    await FundGroup.info.callback(group, interaction, user=target)
    fund_service.fund_info.assert_awaited_once_with(user_id="555")


async def test_fund_info_reply_is_ephemeral_with_allowed_mentions_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund_service.fund_info.return_value = _hedge_fund(name="MyFund")
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    await FundGroup.info.callback(group, interaction, user=None)
    kwargs = _send_call_kwargs(interaction)
    # /fund info is EPHEMERAL.
    assert kwargs.get("ephemeral") is True
    # I2 carry-forward.
    assert "allowed_mentions" in kwargs
    assert isinstance(kwargs["allowed_mentions"], discord.AllowedMentions)
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value


async def test_fund_info_passes_base_and_effective_apy_to_builder(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """The cog computes effective APY via ``compute_effective_apy`` and renders it."""
    fund_service.fund_info.return_value = _hedge_fund()
    settings = _settings(hedge_fund_base_apy=0.15)
    group = FundGroup(fund_service_factory=fund_service_factory, settings=settings)
    interaction = fake_interaction()
    await FundGroup.info.callback(group, interaction, user=None)
    kwargs = _send_call_kwargs(interaction)
    embed = kwargs["embed"]
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    # No active penalty path → base == effective APY → renders "15.00%".
    assert "15.00%" in rendered


async def test_fund_info_renders_neutral_inline_embed_when_no_fund(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``fund_info`` returning ``None`` still yields an ephemeral neutral embed."""
    fund_service.fund_info.return_value = None
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    await FundGroup.info.callback(group, interaction, user=None)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    assert "allowed_mentions" in kwargs
    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value


# ---------------------------------------------------------------------------
# /fund withdraw — PUBLIC (mutation)


async def test_fund_withdraw_calls_service_with_decimal_and_utc_now(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Float amount converted via ``Decimal(str(amount))``; ``now`` UTC-aware."""
    group = _build_group(fund_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    await FundGroup.withdraw.callback(group, interaction, amount=100.50)
    fund_service.withdraw.assert_awaited_once()
    args, _kwargs = fund_service.withdraw.await_args
    assert args[0] == "42"
    assert args[1] == Decimal("100.50")
    # ``Decimal(str(100.50)) == Decimal('100.5')`` — but ``str(100.50)`` is
    # ``'100.5'``, so the comparison above is intentional. The cog MUST use
    # ``Decimal(str(amount))``; ``Decimal(100.50)`` directly would carry
    # IEEE-754 noise (Phase 3.1 + 8e convention).
    now = args[2]
    assert isinstance(now, datetime)
    assert now.tzinfo is UTC


async def test_fund_withdraw_reply_is_public_with_allowed_mentions_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    await FundGroup.withdraw.callback(group, interaction, amount=50.0)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral", False) is False
    assert "allowed_mentions" in kwargs
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)


async def test_fund_withdraw_propagates_fund_insufficient_balance(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund_service.withdraw.side_effect = FundInsufficientBalance(
        need=Decimal("100.00"), have=Decimal("50.00")
    )
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    with pytest.raises(FundInsufficientBalance):
        await FundGroup.withdraw.callback(group, interaction, amount=100.0)
    interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# /fund send_events — PUBLIC (mutation)


async def test_fund_send_events_calls_service_with_decimal(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    group = _build_group(fund_service_factory)
    interaction = fake_interaction(user_id=42, guild_id=99)
    await FundGroup.send_events.callback(group, interaction, amount=75.25)
    fund_service.send_to_events.assert_awaited_once_with("42", Decimal("75.25"))


async def test_fund_send_events_reply_is_public_with_allowed_mentions_none(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    await FundGroup.send_events.callback(group, interaction, amount=10.0)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral", False) is False
    assert "allowed_mentions" in kwargs


async def test_fund_send_events_propagates_invalid_amount(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    fund_service.send_to_events.side_effect = InvalidAmount(
        reason="amount must be positive"
    )
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    with pytest.raises(InvalidAmount):
        await FundGroup.send_events.callback(group, interaction, amount=0.0)
    interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# /fund invest — raises NotImplementedError per Open-Q5


async def test_fund_invest_propagates_not_implemented_uncaught(
    fake_interaction,  # type: ignore[no-untyped-def]
    fund_service: AsyncMock,
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """The cog must NOT catch :class:`NotImplementedError` from ``invest``."""
    fund_service.invest.side_effect = NotImplementedError(
        "multi-investor funds are deferred"
    )
    group = _build_group(fund_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    with pytest.raises(NotImplementedError):
        await FundGroup.invest.callback(group, interaction, user=target, amount=100.0)
    interaction.response.send_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# FundCog & FundGroup registration sanity


def test_fund_group_is_app_commands_group_named_fund() -> None:
    """``FundGroup`` is an ``app_commands.Group`` with ``name='fund'``."""
    import discord.app_commands as app_commands

    assert issubclass(FundGroup, app_commands.Group)
    # Instantiate to verify name on the live group instance.
    group = _build_group(lambda _: None)  # type: ignore[arg-type]
    assert group.name == "fund"


def test_fund_group_registers_all_five_subcommands() -> None:
    """All five subcommands are registered as ``app_commands.Command``."""
    import discord.app_commands as app_commands

    assert isinstance(FundGroup.create, app_commands.Command)
    assert isinstance(FundGroup.info, app_commands.Command)
    assert isinstance(FundGroup.withdraw, app_commands.Command)
    assert isinstance(FundGroup.send_events, app_commands.Command)
    assert isinstance(FundGroup.invest, app_commands.Command)


def test_fund_cog_exposes_group_for_phase_13_wiring(
    fund_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``FundCog`` exposes the ``FundGroup`` instance so Phase 13 can register it."""
    cog = FundCog(fund_service_factory=fund_service_factory, settings=_settings())
    assert isinstance(cog.group, FundGroup)
