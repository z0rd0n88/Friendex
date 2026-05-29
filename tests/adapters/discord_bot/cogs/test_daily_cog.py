"""Tests for :class:`DailyCog` — ``/daily``."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import discord
import pytest

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

from friendex.adapters.discord_bot.cogs.daily_cog import DailyCog
from friendex.adapters.discord_bot.embeds import COLOR_SUCCESS
from friendex.application.daily_result import DailyClaimResult
from friendex.domain.errors import AlreadyClaimedToday


def _daily_result(
    *,
    streak: int = 1,
    reward: Decimal = Decimal("500.00"),
    is_streak_bonus: bool = False,
    new_cash: Decimal = Decimal("10500.00"),
) -> DailyClaimResult:
    return DailyClaimResult(
        user_id="4242",
        streak=streak,
        reward=reward,
        is_streak_bonus=is_streak_bonus,
        new_cash_balance=new_cash,
        claim_date=datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
    )


def _send_call_kwargs(interaction) -> dict:  # type: ignore[no-untyped-def]
    """Return the kwargs of the last user-visible reply.

    Wave 1 (#82 H13) routed cog replies through ``followup.send`` after a
    ``response.defer(...)``.
    """
    assert interaction.followup.send.await_count >= 1
    return interaction.followup.send.await_args.kwargs


# ---------------------------------------------------------------------------
# Happy path


async def test_daily_calls_claim_daily_with_user_id_and_utc_now(
    fake_interaction,  # type: ignore[no-untyped-def]
    daily_service: AsyncMock,
    daily_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    daily_service.claim_daily.return_value = _daily_result()
    cog = DailyCog(daily_service_factory=daily_service_factory)
    interaction = fake_interaction(user_id=4242, guild_id=99)
    await DailyCog.daily.callback(cog, interaction)

    daily_service.claim_daily.assert_awaited_once()
    args, _kwargs = daily_service.claim_daily.await_args
    assert args[0] == "4242"
    now = args[1]
    assert isinstance(now, datetime)
    assert now.tzinfo is UTC


async def test_daily_routes_through_per_guild_factory(
    fake_interaction,  # type: ignore[no-untyped-def]
    daily_service: AsyncMock,
) -> None:
    """The factory must be called with ``str(interaction.guild.id)``."""
    daily_service.claim_daily.return_value = _daily_result()
    seen_guild_ids: list[str] = []

    def factory(guild_id: str):
        seen_guild_ids.append(guild_id)
        return daily_service

    cog = DailyCog(daily_service_factory=factory)
    interaction = fake_interaction(user_id=1, guild_id=777)
    await DailyCog.daily.callback(cog, interaction)
    assert seen_guild_ids == ["777"]


async def test_daily_reply_is_public_and_uses_daily_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    daily_service: AsyncMock,
    daily_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    daily_service.claim_daily.return_value = _daily_result(
        reward=Decimal("500.00"), streak=3
    )
    cog = DailyCog(daily_service_factory=daily_service_factory)
    interaction = fake_interaction()
    await DailyCog.daily.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    # Mutation-hardening: ``/daily`` is public. ``ephemeral`` must be False
    # or unset (defaults to False on discord.py); pinning to ``not True``.
    assert kwargs.get("ephemeral", False) is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$500.00" in rendered


# ---------------------------------------------------------------------------
# Error propagation: AlreadyClaimedToday escapes the cog uncaught


async def test_daily_propagates_already_claimed_today(
    fake_interaction,  # type: ignore[no-untyped-def]
    daily_service: AsyncMock,
    daily_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """Phase 13 owns the error handler; cogs propagate :class:`DomainError`."""
    daily_service.claim_daily.side_effect = AlreadyClaimedToday(seconds_remaining=3600)
    cog = DailyCog(daily_service_factory=daily_service_factory)
    interaction = fake_interaction()
    with pytest.raises(AlreadyClaimedToday):
        await DailyCog.daily.callback(cog, interaction)
    # No reply was sent — Phase 13 handler will render the error embed.
    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Slash-command registration sanity


def test_daily_cog_registers_daily_app_command() -> None:
    import discord.app_commands as app_commands

    assert isinstance(DailyCog.daily, app_commands.Command)


# ---------------------------------------------------------------------------
# Wave 1 contracts


async def test_daily_defers_publicly(
    fake_interaction,  # type: ignore[no-untyped-def]
    daily_service: AsyncMock,
    daily_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/daily`` is a public action command — defer with ``ephemeral=False``."""
    daily_service.claim_daily.return_value = _daily_result()
    cog = DailyCog(daily_service_factory=daily_service_factory)
    interaction = fake_interaction()

    await DailyCog.daily.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)


def test_daily_command_is_guild_only() -> None:
    """Wave 1 (#82 H14): ``/daily`` refuses DM dispatch."""
    assert getattr(DailyCog.daily, "guild_only", None) is True
