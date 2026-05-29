"""Tests for :class:`StatsCog` — ``/trending``, ``/mystats``, ``/price``, ``/mystock``.

Each command callback is invoked directly via ``Cog.command.callback(...)``
since ``dpytest`` simulates message events, not slash interactions. Assertions
walk ``interaction.response.send_message.call_args`` and the embed's
``to_dict()`` (Phase 10 review convention).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from unittest.mock import AsyncMock

from friendex.adapters.discord_bot.cogs.stats_cog import StatsCog
from friendex.adapters.discord_bot.embeds import COLOR_NEUTRAL
from friendex.application.snapshot_models import (
    PriceStats,
    TrendingEntry,
    UserStats,
)

# ---------------------------------------------------------------------------
# Helpers


def _price_stats(
    *,
    user_id: str = "9876543210",
    current: Decimal = Decimal("100.00"),
    high: Decimal = Decimal("110.00"),
    low: Decimal = Decimal("90.00"),
    ath: Decimal = Decimal("120.00"),
) -> PriceStats:
    return PriceStats(
        user_id=user_id,
        current=current,
        high_24h=high,
        low_24h=low,
        all_time_high=ath,
    )


def _user_stats(
    *,
    user_id: str = "9876543210",
    score: float = 2.5,
    tier: str = "Medium",
    last_activity: datetime = datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
) -> UserStats:
    return UserStats(
        user_id=user_id,
        trending_score=score,
        engagement_tier=tier,
        last_activity=last_activity,
    )


def _trending_entry(
    *,
    rank: int = 1,
    user_id: str = "111",
    score: float = 5.0,
    price: Decimal = Decimal("105.50"),
) -> TrendingEntry:
    return TrendingEntry(
        rank=rank,
        user_id=user_id,
        score=score,
        current_price=price,
    )


def _send_call_kwargs(interaction) -> dict:  # type: ignore[no-untyped-def]
    """Return the kwargs dict of the last user-visible reply.

    Wave 1 (#82 H13) routed every cog reply through
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
# /trending — PUBLIC


async def test_trending_calls_trending_snapshot(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    stats_service.trending_snapshot.return_value = [_trending_entry()]
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    await StatsCog.trending.callback(cog, interaction)
    stats_service.trending_snapshot.assert_awaited_once_with()


async def test_trending_routes_through_per_guild_factory(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
) -> None:
    """Factory is invoked with ``str(interaction.guild.id)``."""
    stats_service.trending_snapshot.return_value = []
    seen_guild_ids: list[str] = []

    def factory(guild_id: str):
        seen_guild_ids.append(guild_id)
        return stats_service

    cog = StatsCog(stats_service_factory=factory)
    interaction = fake_interaction(user_id=1, guild_id=777)
    await StatsCog.trending.callback(cog, interaction)
    assert seen_guild_ids == ["777"]


async def test_trending_reply_is_public_and_uses_trending_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    stats_service.trending_snapshot.return_value = [
        _trending_entry(rank=1, user_id="111", score=10.0),
        _trending_entry(rank=2, user_id="222", score=5.0),
    ]
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    await StatsCog.trending.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    # Mutation-hardening: /trending is PUBLIC. ``ephemeral`` must be False
    # or unset (defaults to False on discord.py); pinning to ``not True``.
    assert kwargs.get("ephemeral", False) is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value


# ---------------------------------------------------------------------------
# /mystats — EPHEMERAL


async def test_mystats_calls_user_stats_for_invoking_user(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    stats_service.user_stats.return_value = _user_stats()
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    await StatsCog.mystats.callback(cog, interaction)
    stats_service.user_stats.assert_awaited_once_with(user_id="4242")


async def test_mystats_reply_is_ephemeral_and_uses_mystats_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    stats_service.user_stats.return_value = _user_stats(tier="Elite", score=9.42)
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    await StatsCog.mystats.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    # Mutation-hardening: /mystats is ephemeral.
    assert kwargs.get("ephemeral") is True
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "Elite" in rendered
    assert "9.42" in rendered


async def test_mystats_with_no_account_replies_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``user_stats`` returning ``None`` still yields an ephemeral reply."""
    stats_service.user_stats.return_value = None
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    await StatsCog.mystats.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value


# ---------------------------------------------------------------------------
# /price <user> — EPHEMERAL


async def test_price_calls_get_price_stats_for_specified_user(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    stats_service.get_price_stats.return_value = _price_stats(user_id="555")
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    target = _make_member(555)
    await StatsCog.price.callback(cog, interaction, user=target)
    stats_service.get_price_stats.assert_awaited_once_with(user_id="555")


async def test_price_reply_is_ephemeral_and_uses_price_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    stats_service.get_price_stats.return_value = _price_stats(
        user_id="555",
        current=Decimal("123.45"),
    )
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    await StatsCog.price.callback(cog, interaction, user=target)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True  # mutation-hardening
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$123.45" in rendered


async def test_price_with_no_price_history_replies_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``get_price_stats`` returning ``None`` still yields an ephemeral reply."""
    stats_service.get_price_stats.return_value = None
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    target = _make_member(555)
    await StatsCog.price.callback(cog, interaction, user=target)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value


# ---------------------------------------------------------------------------
# /mystock — EPHEMERAL (same builder as /price, invoker as the target)


async def test_mystock_calls_get_price_stats_for_invoking_user(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/mystock`` looks up the invoker (no ``user`` arg on the surface)."""
    stats_service.get_price_stats.return_value = _price_stats(user_id="4242")
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    await StatsCog.mystock.callback(cog, interaction)
    stats_service.get_price_stats.assert_awaited_once_with(user_id="4242")


async def test_mystock_reply_is_ephemeral_and_uses_price_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    stats_service.get_price_stats.return_value = _price_stats(current=Decimal("88.88"))
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    await StatsCog.mystock.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True  # mutation-hardening
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$88.88" in rendered


async def test_mystock_with_no_price_history_replies_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/mystock`` returning ``None`` still yields an ephemeral reply."""
    stats_service.get_price_stats.return_value = None
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()
    await StatsCog.mystock.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True
    embed = kwargs.get("embed")
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_NEUTRAL.value


# ---------------------------------------------------------------------------
# /mystock surface — must NOT take a ``user`` argument (the acceptance
# criteria pin this as a SEPARATE @app_commands.command from /price).


def test_mystock_app_command_takes_no_user_argument() -> None:
    """``/mystock`` is a distinct command and has no ``user`` parameter.

    Phase 11b acceptance: ``/mystock`` and ``/price`` share the same embed
    builder, but ``/mystock`` omits the user argument from the slash command
    surface (this is a separate ``@app_commands.command``).
    """
    import discord.app_commands as app_commands

    assert isinstance(StatsCog.mystock, app_commands.Command)
    params = StatsCog.mystock.parameters
    assert not any(p.name == "user" for p in params)


# ---------------------------------------------------------------------------
# Slash-command registration sanity


def test_stats_cog_registers_all_four_app_commands() -> None:
    import discord.app_commands as app_commands

    assert isinstance(StatsCog.trending, app_commands.Command)
    assert isinstance(StatsCog.mystats, app_commands.Command)
    assert isinstance(StatsCog.price, app_commands.Command)
    assert isinstance(StatsCog.mystock, app_commands.Command)


# ---------------------------------------------------------------------------
# Wave 1 contracts — defer ephemerality and guild_only


async def test_trending_defers_publicly(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/trending`` is the public command of the cog — defer ``ephemeral=False``."""
    stats_service.trending_snapshot.return_value = []
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()

    await StatsCog.trending.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)


async def test_mystats_defers_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
    stats_service: AsyncMock,
    stats_service_factory,  # type: ignore[no-untyped-def]
) -> None:
    """``/mystats`` is ephemeral."""
    stats_service.user_stats.return_value = None
    cog = StatsCog(stats_service_factory=stats_service_factory)
    interaction = fake_interaction()

    await StatsCog.mystats.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)


def test_stats_commands_are_guild_only() -> None:
    """Wave 1 (#82 H14): every stats command refuses DM dispatch."""
    for cmd in (
        StatsCog.trending,
        StatsCog.mystats,
        StatsCog.price,
        StatsCog.mystock,
    ):
        assert getattr(cmd, "guild_only", None) is True, (
            f"{cmd.name}: must be decorated @app_commands.guild_only()"
        )
