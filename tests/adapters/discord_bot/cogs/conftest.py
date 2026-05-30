"""Shared fixtures for the Discord cog tests.

The cogs (Phase 11) read interaction state and dispatch to per-guild
application services obtained from a ``Callable[[str], TService]`` factory
injected at construction time (Phase 9 service_factory convention; see
``baton-runner/br-2026-05-25-phase-9/digest-phase-9.md``). For testing we
swap each service for an :class:`unittest.mock.AsyncMock` whose method
spelling matches the real service class, and wrap it in a trivial factory
that returns the same mock regardless of ``guild_id`` — exercising the
per-guild routing call without forcing the test to set up two mocks.

``fake_interaction`` builds the slot of a :class:`discord.Interaction` the
cog actually touches: ``response.send_message`` / ``response.defer`` /
``followup.send`` (all :class:`AsyncMock`), and integer ``user.id`` /
``guild.id`` (Discord snowflakes are 64-bit ints in the live API). Anything
else stays a permissive :class:`MagicMock` so cog code can read it without
the fixture having to predict every attribute access.

The fixtures wrap **every** application service used across Phase 11a/b/c —
even the ones (stats, trading, fund) the foundation slice 11a doesn't
exercise — because 11b and 11c re-use this same conftest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from friendex.application.activity_service import ActivityService
from friendex.application.daily_service import DailyService
from friendex.application.fund_service import FundService
from friendex.application.portfolio_service import PortfolioService
from friendex.application.stats_service import StatsService
from friendex.application.trading_service import TradingService

if TYPE_CHECKING:
    from collections.abc import Callable

# Stub Discord snowflake IDs — large positive ints so they always serialise
# through ``str(interaction.guild.id)`` cleanly (real Discord IDs are 64-bit
# unsigned ints rendered as decimal strings).
_DEFAULT_USER_ID = 9876543210
_DEFAULT_GUILD_ID = 1234567890


@pytest.fixture
def fake_interaction() -> Callable[..., MagicMock]:
    """Factory that builds a stub :class:`discord.Interaction`.

    Used by every cog test to fabricate the interaction object the slash
    command callback receives. The factory takes ``user_id`` and
    ``guild_id`` as ints (real Discord snowflakes are 64-bit ints) so a
    test that needs to discriminate by user or guild can pin them; both
    have sensible defaults.

    The returned :class:`MagicMock` exposes:

    * ``response.send_message`` — :class:`AsyncMock`
    * ``response.defer`` — :class:`AsyncMock`
    * ``followup.send`` — :class:`AsyncMock`
    * ``user.id`` — int
    * ``guild.id`` — int
    """

    def _make(
        *,
        user_id: int = _DEFAULT_USER_ID,
        guild_id: int = _DEFAULT_GUILD_ID,
    ) -> MagicMock:
        interaction = MagicMock(name="Interaction")
        interaction.response.send_message = AsyncMock(name="response.send_message")
        interaction.response.defer = AsyncMock(name="response.defer")
        interaction.followup.send = AsyncMock(name="followup.send")
        # Populate as real ints — cogs call ``str(interaction.guild.id)`` to
        # route per-guild services, and ``MagicMock``'s default int coercion
        # would otherwise return a random ``int(mock)`` placeholder.
        interaction.user.id = user_id
        interaction.guild.id = guild_id
        return interaction

    return _make


# ---------------------------------------------------------------------------
# Application-service AsyncMock fixtures.
#
# Each fixture uses ``spec=`` to bind the mock's attribute surface to the
# real service class: ``spec`` ensures every async method on the class is
# present as an :class:`AsyncMock` on the mock, *and* that any typo'd
# method access (``portfolio_service.portfolio_snapsot``) raises
# :class:`AttributeError` instead of silently no-op'ing.


def _async_mock_for(cls: type) -> AsyncMock:
    """Build an :class:`AsyncMock` whose surface tracks ``cls``."""
    return AsyncMock(spec=cls)


@pytest.fixture
def portfolio_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`PortfolioService`."""
    return _async_mock_for(PortfolioService)


@pytest.fixture
def activity_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`ActivityService`."""
    return _async_mock_for(ActivityService)


@pytest.fixture
def daily_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`DailyService`."""
    return _async_mock_for(DailyService)


@pytest.fixture
def stats_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`StatsService`."""
    return _async_mock_for(StatsService)


@pytest.fixture
def trading_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`TradingService`."""
    return _async_mock_for(TradingService)


@pytest.fixture
def fund_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`FundService`."""
    return _async_mock_for(FundService)


# ---------------------------------------------------------------------------
# Per-guild service factories.
#
# Each factory is ``Callable[[str], TService]`` — the same shape Phase 14's
# composition layer will inject (Phase 9 service_factory digest). The
# test-time factory returns the same mock regardless of ``guild_id`` so
# tests exercise the routing call without setting up per-guild branches.


@pytest.fixture
def portfolio_service_factory(
    portfolio_service: AsyncMock,
) -> Callable[[str], PortfolioService]:
    """Return a factory yielding ``portfolio_service`` for any guild id."""

    def _factory(_guild_id: str) -> PortfolioService:
        return cast("PortfolioService", portfolio_service)

    return _factory


@pytest.fixture
def activity_service_factory(
    activity_service: AsyncMock,
) -> Callable[[str], ActivityService]:
    """Return a factory yielding ``activity_service`` for any guild id."""

    def _factory(_guild_id: str) -> ActivityService:
        return cast("ActivityService", activity_service)

    return _factory


@pytest.fixture
def daily_service_factory(
    daily_service: AsyncMock,
) -> Callable[[str], DailyService]:
    """Return a factory yielding ``daily_service`` for any guild id."""

    def _factory(_guild_id: str) -> DailyService:
        return cast("DailyService", daily_service)

    return _factory


@pytest.fixture
def stats_service_factory(
    stats_service: AsyncMock,
) -> Callable[[str], StatsService]:
    """Return a factory yielding ``stats_service`` for any guild id."""

    def _factory(_guild_id: str) -> StatsService:
        return cast("StatsService", stats_service)

    return _factory


@pytest.fixture
def trading_service_factory(
    trading_service: AsyncMock,
) -> Callable[[str], TradingService]:
    """Return a factory yielding ``trading_service`` for any guild id."""

    def _factory(_guild_id: str) -> TradingService:
        return cast("TradingService", trading_service)

    return _factory


@pytest.fixture
def fund_service_factory(
    fund_service: AsyncMock,
) -> Callable[[str], FundService]:
    """Return a factory yielding ``fund_service`` for any guild id."""

    def _factory(_guild_id: str) -> FundService:
        return cast("FundService", fund_service)

    return _factory
