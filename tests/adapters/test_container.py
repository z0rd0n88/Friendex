"""Tests for the dependency-injection :class:`Container` (Phase 13).

The container is the single composition seam: it constructs every repo, the
shared :class:`LockManager`, all per-guild service factories, all 8 background
tasks (single-instance), all 7 cogs, all 4 listeners, then exposes
:meth:`register_with` which adds every cog/listener to a passed ``bot`` and
installs the central error handler on ``bot.tree.on_error``.

Per the Phase 13 AC bar the test exercises construction + registration counts;
it does **not** start any task (tasks need a live event loop and the Phase 14
composition layer to bind ``_loop``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
    assert len(container.tasks) == 8


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
    task_types = {type(t) for t in container.tasks}
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


def test_container_tasks_are_not_started(
    settings: Settings, fake_sessionmaker: MagicMock
) -> None:
    """No task has had ``start()`` called — ``_loop`` is unbound (Phase 14).

    The :class:`~friendex.adapters.tasks.base_task.BackgroundTask` base class
    leaves ``_loop`` as a declared attribute that the **composition layer**
    (Phase 14) binds with a :class:`discord.ext.tasks.Loop`. Phase 13 must
    not bind it, so any task whose ``_loop`` has been initialised would
    indicate premature wiring.
    """
    container = Container(settings, fake_sessionmaker)
    for task in container.tasks:
        # ``hasattr`` returns False when ``_loop`` is a bare class
        # annotation without instance binding.
        assert not hasattr(task, "_loop") or not _is_running(task), (
            f"{type(task).__name__} appears to have been started"
        )


def _is_running(task: object) -> bool:
    try:
        return bool(task._loop.is_running())  # type: ignore[attr-defined]
    except AttributeError:
        return False


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
