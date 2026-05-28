"""Tests for the dependency-injection :class:`Container` (Phase 13).

The container is the single composition seam: it constructs every repo, the
shared :class:`LockManager`, all per-guild service factories, all 8 background
tasks (single-instance), all 7 cogs, all 4 listeners, then exposes
:meth:`register_with` which adds every cog/listener to a passed ``bot`` and
installs the central error handler on ``bot.tree.on_error``.

Per the Phase 13 AC bar the test exercises construction + registration counts;
it does **not** start any task (tasks are started via
:meth:`~Container.build_runners` in the Phase-14 ``setup_hook``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from friendex.adapters.config import Settings
from friendex.adapters.container import Container
from friendex.adapters.discord_bot.cogs.account_cog import AccountCog
from friendex.adapters.discord_bot.cogs.admin_cog import AdminCog
from friendex.adapters.discord_bot.cogs.daily_cog import DailyCog
from friendex.adapters.discord_bot.cogs.fund_cog import FundCog
from friendex.adapters.discord_bot.cogs.portfolio_cog import PortfolioCog
from friendex.adapters.discord_bot.cogs.stats_cog import StatsCog
from friendex.adapters.discord_bot.cogs.trading_cog import TradingCog
from friendex.adapters.discord_bot.listeners.member_listener import MemberListener
from friendex.adapters.discord_bot.listeners.message_listener import MessageListener
from friendex.adapters.discord_bot.listeners.reaction_listener import ReactionListener
from friendex.adapters.discord_bot.listeners.voice_listener import VoiceListener
from friendex.adapters.tasks.activity_tick_task import ActivityTickTask
from friendex.adapters.tasks.daily_reset_task import DailyResetTask
from friendex.adapters.tasks.freeze_check_task import FreezeCheckTask
from friendex.adapters.tasks.inactivity_decay_task import InactivityDecayTask
from friendex.adapters.tasks.liquidation_task import LiquidationTask
from friendex.adapters.tasks.monthly_rollover_task import MonthlyRolloverTask
from friendex.adapters.tasks.vc_boost_task import VcBoostTask
from friendex.adapters.tasks.weekly_reset_task import WeeklyResetTask
from friendex.application.fund_service import FundService
from friendex.application.liquidation_events import LiquidationEvent
from friendex.application.portfolio_service import PortfolioService
from friendex.application.trading_service import TradingService

_VALID_TOKEN = "x" * 32  # any non-placeholder string passes the Settings validator


@pytest.fixture
def settings() -> Settings:
    return Settings(discord_token=_VALID_TOKEN)


@pytest.fixture
def fake_sessionmaker() -> MagicMock:
    """A stand-in for ``async_sessionmaker[AsyncSession]``.

    The Phase 13 container only **stores** the sessionmaker — it does not
    call it. Every repo ctor accepts it positionally, then dormant; nothing
    issues SQL during construction. So a permissive ``MagicMock`` is enough.
    """
    return MagicMock(name="async_sessionmaker")


# ---------------------------------------------------------------------------
# Construction


def test_container_constructs_without_raising(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    Container(settings, fake_sessionmaker)


def test_container_exposes_seven_cogs(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    assert len(container.cogs) == 7


def test_container_exposes_four_listeners(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    assert len(container.listeners) == 4


def test_container_exposes_eight_tasks(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    assert len(container.raw_tasks) == 8


def test_container_cog_types_match_inventory(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    cog_types = {type(c) for c in container.cogs}
    assert cog_types == {
        AccountCog,
        AdminCog,
        DailyCog,
        FundCog,
        PortfolioCog,
        StatsCog,
        TradingCog,
    }


def test_container_listener_types_match_inventory(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    listener_types = {type(listener) for listener in container.listeners}
    assert listener_types == {
        MessageListener,
        VoiceListener,
        ReactionListener,
        MemberListener,
    }


def test_container_task_types_match_inventory(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    task_types = {type(t) for t in container.raw_tasks}
    assert task_types == {
        ActivityTickTask,
        DailyResetTask,
        FreezeCheckTask,
        InactivityDecayTask,
        LiquidationTask,
        MonthlyRolloverTask,
        VcBoostTask,
        WeeklyResetTask,
    }


# ---------------------------------------------------------------------------
# Per-guild service factories


def test_trading_service_factory_builds_trading_service(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    service = container.trading_service_factory("guild-123")
    assert isinstance(service, TradingService)
    # ``_guild_id`` is the convention every per-guild service captures.
    assert service._guild_id == "guild-123"


def test_portfolio_service_factory_builds_portfolio_service(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    service = container.portfolio_service_factory("guild-9")
    assert isinstance(service, PortfolioService)
    assert service._guild_id == "guild-9"


def test_fund_service_factory_builds_fund_service(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    service = container.fund_service_factory("guild-abc")
    assert isinstance(service, FundService)
    assert service._guild_id == "guild-abc"


# ---------------------------------------------------------------------------
# register_with


async def test_register_with_adds_every_cog_and_listener(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    container = Container(settings, fake_sessionmaker)
    bot = MagicMock(name="Bot")
    bot.add_cog = AsyncMock(name="add_cog")
    bot.tree = MagicMock(name="tree")

    await container.register_with(bot)

    # 7 cogs + 4 listeners = 11 add_cog calls.
    assert bot.add_cog.await_count == 11


async def test_register_with_installs_error_handler(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    """After ``register_with``, ``bot.tree.on_error`` is the installed handler."""
    container = Container(settings, fake_sessionmaker)
    bot = MagicMock(name="Bot")
    bot.add_cog = AsyncMock(name="add_cog")
    bot.tree = MagicMock(name="tree")

    await container.register_with(bot)

    assert bot.tree.on_error is not None
    assert callable(bot.tree.on_error)


# ---------------------------------------------------------------------------
# build_runners (Phase 14 AC2 / AC6)


def _stub_bot_with_guilds(guild_ids: list[int]) -> MagicMock:
    """Build a ``MagicMock`` bot whose ``.guilds`` is a list of stub guilds."""
    bot = MagicMock(name="Bot")
    bot.guilds = [MagicMock(name=f"Guild({gid})", id=gid) for gid in guild_ids]
    return bot


async def test_build_runners_swaps_iter_guild_ids_to_walk_bot_guilds(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    """After ``build_runners``, every task yields ``[str(g.id) for g in bot.guilds]``."""
    container = Container(settings, fake_sessionmaker)
    bot = _stub_bot_with_guilds([1111, 2222])

    container.build_runners(bot)

    for task in container.raw_tasks:
        result = await task._iter_guild_ids()
        assert list(result) == ["1111", "2222"], (
            f"{type(task).__name__} did not pick up bot.guilds"
        )


async def test_build_runners_iter_guild_ids_reflects_live_bot_guilds(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    """The closure must re-read ``bot.guilds`` on each call, not snapshot."""
    container = Container(settings, fake_sessionmaker)
    bot = _stub_bot_with_guilds([100])

    container.build_runners(bot)
    # Mutate after bind — the closure should see the new state.
    bot.guilds = [MagicMock(name="Guild(200)", id=200)]

    task = container.raw_tasks[0]
    result = await task._iter_guild_ids()
    assert list(result) == ["200"]


async def test_build_runners_replaces_liquidation_notifier(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    """``LiquidationTask._notifier`` is no longer the no-op after ``build_runners``."""
    from friendex.adapters.container import _noop_notifier

    container = Container(settings, fake_sessionmaker)
    liquidation_task = next(
        t for t in container.raw_tasks if isinstance(t, LiquidationTask)
    )
    assert liquidation_task._notifier is _noop_notifier  # pre-condition

    bot = _stub_bot_with_guilds([42])
    container.build_runners(bot)

    assert liquidation_task._notifier is not _noop_notifier


async def test_build_runners_liquidation_notifier_dispatches_to_system_channel(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    """Notifier sends the embed to ``bot.get_guild(...).system_channel``."""
    container = Container(settings, fake_sessionmaker)

    guild = MagicMock(name="Guild")
    system_channel = MagicMock(name="SystemChannel")
    system_channel.send = AsyncMock(name="send")
    guild.system_channel = system_channel

    bot = MagicMock(name="Bot")
    bot.guilds = [guild]
    bot.get_guild = MagicMock(return_value=guild)

    container.build_runners(bot)
    liquidation_task = next(
        t for t in container.raw_tasks if isinstance(t, LiquidationTask)
    )

    event = LiquidationEvent(
        guild_id="42",
        holder_id="holder",
        target_id="target",
        shares=5,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("150.00"),
        collateral_returned=Decimal("0.00"),
        pnl=Decimal("-250.00"),
        timestamp=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
    )
    await liquidation_task._notifier(event)

    bot.get_guild.assert_called_once_with(42)
    assert system_channel.send.await_count == 1
    call = system_channel.send.await_args
    assert "embed" in call.kwargs
    assert isinstance(call.kwargs["embed"], discord.Embed)
    allowed = call.kwargs["allowed_mentions"]
    # AllowedMentions.none() means every flag is False / empty.
    assert allowed.everyone is False
    assert allowed.users is False
    assert allowed.roles is False


async def test_build_runners_liquidation_notifier_skips_when_guild_missing(
    settings: Settings,
    fake_sessionmaker: MagicMock,
) -> None:
    """When ``bot.get_guild`` returns None, the notifier skips without raising."""
    container = Container(settings, fake_sessionmaker)
    bot = MagicMock(name="Bot")
    bot.guilds = []
    bot.get_guild = MagicMock(return_value=None)

    container.build_runners(bot)
    liquidation_task = next(
        t for t in container.raw_tasks if isinstance(t, LiquidationTask)
    )

    event = LiquidationEvent(
        guild_id="999",
        holder_id="holder",
        target_id="target",
        shares=1,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("150.00"),
        collateral_returned=Decimal("0.00"),
        pnl=Decimal("-50.00"),
        timestamp=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
    )
    await liquidation_task._notifier(event)

    bot.get_guild.assert_called_once_with(999)


async def test_build_runners_liquidation_notifier_skips_when_no_system_channel(
    settings: Settings,
    fake_sessionmaker: MagicMock,
) -> None:
    """When ``guild.system_channel`` is None, the notifier skips without raising."""
    container = Container(settings, fake_sessionmaker)
    guild = MagicMock(name="Guild")
    guild.system_channel = None
    channel_send = MagicMock(name="send")

    bot = MagicMock(name="Bot")
    bot.guilds = [guild]
    bot.get_guild = MagicMock(return_value=guild)

    container.build_runners(bot)
    liquidation_task = next(
        t for t in container.raw_tasks if isinstance(t, LiquidationTask)
    )

    event = LiquidationEvent(
        guild_id="55",
        holder_id="holder",
        target_id="target",
        shares=1,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("150.00"),
        collateral_returned=Decimal("0.00"),
        pnl=Decimal("-50.00"),
        timestamp=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
    )
    await liquidation_task._notifier(event)

    channel_send.assert_not_called()
