"""Self-tests for the cog-test ``conftest`` fixtures.

These tests pin the conftest contract that the per-cog test modules rely on:
the shape of :func:`fake_interaction`, and the AsyncMock-based service
fixtures plus their per-guild factory wrappers. The fixtures must spell the
real service method names so :func:`unittest.mock.MagicMock.assert_called_*`
catches typos that would silently no-op against a permissive mock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from friendex.application.activity_service import ActivityService
from friendex.application.daily_service import DailyService
from friendex.application.fund_service import FundService
from friendex.application.portfolio_service import PortfolioService
from friendex.application.stats_service import StatsService
from friendex.application.trading_service import TradingService

# ---------------------------------------------------------------------------
# fake_interaction


def test_fake_interaction_user_and_guild_ids_are_ints(fake_interaction):  # type: ignore[no-untyped-def]
    interaction = fake_interaction(user_id=4242, guild_id=1010)
    assert isinstance(interaction.user.id, int)
    assert isinstance(interaction.guild.id, int)
    assert interaction.user.id == 4242
    assert interaction.guild.id == 1010


def test_fake_interaction_has_async_response_helpers(fake_interaction):  # type: ignore[no-untyped-def]
    interaction = fake_interaction()
    assert isinstance(interaction.response.send_message, AsyncMock)
    assert isinstance(interaction.response.defer, AsyncMock)
    assert isinstance(interaction.followup.send, AsyncMock)


def test_fake_interaction_user_id_defaults_are_int_snowflakes(fake_interaction):  # type: ignore[no-untyped-def]
    interaction = fake_interaction()
    # Default snowflakes must still be ints (real Discord IDs are ints).
    assert isinstance(interaction.user.id, int)
    assert isinstance(interaction.guild.id, int)


# ---------------------------------------------------------------------------
# Service AsyncMock fixtures — every method on the real service must be
# spelled on the mock so assert_called_* catches typos / missing service hooks.


def _async_method_names(cls: type) -> set[str]:
    """Return public async-method names declared on ``cls``."""
    import inspect

    return {
        name
        for name, value in vars(cls).items()
        if inspect.iscoroutinefunction(value) and not name.startswith("_")
    }


def test_portfolio_service_mock_has_real_method_names(portfolio_service):  # type: ignore[no-untyped-def]
    expected = _async_method_names(PortfolioService)
    for name in expected:
        attr = getattr(portfolio_service, name)
        assert isinstance(attr, AsyncMock), f"{name} should be AsyncMock"


def test_activity_service_mock_has_real_method_names(activity_service):  # type: ignore[no-untyped-def]
    expected = _async_method_names(ActivityService)
    for name in expected:
        attr = getattr(activity_service, name)
        assert isinstance(attr, AsyncMock), f"{name} should be AsyncMock"


def test_daily_service_mock_has_real_method_names(daily_service):  # type: ignore[no-untyped-def]
    expected = _async_method_names(DailyService)
    for name in expected:
        attr = getattr(daily_service, name)
        assert isinstance(attr, AsyncMock), f"{name} should be AsyncMock"


def test_stats_service_mock_has_real_method_names(stats_service):  # type: ignore[no-untyped-def]
    expected = _async_method_names(StatsService)
    for name in expected:
        attr = getattr(stats_service, name)
        assert isinstance(attr, AsyncMock), f"{name} should be AsyncMock"


def test_trading_service_mock_has_real_method_names(trading_service):  # type: ignore[no-untyped-def]
    expected = _async_method_names(TradingService)
    for name in expected:
        attr = getattr(trading_service, name)
        assert isinstance(attr, AsyncMock), f"{name} should be AsyncMock"


def test_fund_service_mock_has_real_method_names(fund_service):  # type: ignore[no-untyped-def]
    expected = _async_method_names(FundService)
    for name in expected:
        attr = getattr(fund_service, name)
        assert isinstance(attr, AsyncMock), f"{name} should be AsyncMock"


# ---------------------------------------------------------------------------
# Per-guild service factories — same mock returned for any guild_id (no
# branching), but a Callable[[str], TService] so the cogs can call it with
# str(interaction.guild.id).


@pytest.mark.parametrize(
    "factory_fixture, service_fixture",
    [
        ("portfolio_service_factory", "portfolio_service"),
        ("activity_service_factory", "activity_service"),
        ("daily_service_factory", "daily_service"),
        ("stats_service_factory", "stats_service"),
        ("trading_service_factory", "trading_service"),
        ("fund_service_factory", "fund_service"),
    ],
)
def test_service_factory_returns_underlying_mock(
    request,  # type: ignore[no-untyped-def]
    factory_fixture: str,
    service_fixture: str,
) -> None:
    factory = request.getfixturevalue(factory_fixture)
    service = request.getfixturevalue(service_fixture)
    assert factory("guild-A") is service
    assert factory("guild-B") is service  # per-guild routing, but same mock
