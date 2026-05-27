"""Shared fixtures for the Discord listener tests.

The listeners (Phase 12) react to Discord events — :class:`discord.Reaction` /
:class:`discord.Member` / :class:`discord.Message` / :class:`discord.VoiceState`
— rather than slash-command :class:`discord.Interaction`\\ s. Each listener
holds per-guild service *factories* (``Callable[[str], TService]``) injected at
construction time (Phase 9 service_factory convention; see
``baton-runner/br-2026-05-25-phase-9/digest-phase-9.md``); the listener resolves
the per-guild service at event time via ``factory(str(guild_id))``.

For testing we swap each service for an :class:`unittest.mock.AsyncMock` whose
method spelling matches the real service class, and wrap it in a trivial
factory that returns the same mock regardless of ``guild_id`` — exercising the
per-guild routing call without forcing the test to set up two mocks.

The event factories (``fake_message`` / ``fake_member`` / ``fake_voice_state``)
build the minimal slot of each :mod:`discord` event the listener actually
touches: ``author.id`` / ``author.bot`` / ``guild.id`` / ``content`` /
``reference`` / ``mentions`` on a message; ``id`` / ``guild.id`` /
``timed_out_until`` on a member; ``channel`` on a voice state. Anything else
stays a permissive :class:`MagicMock` so listener code can read it without the
fixture having to predict every attribute access.

The service fixtures wrap **every** application service used across Phase
12a/b — even the ones (voice_ping, trading, fund, daily, portfolio, stats) the
foundation slice 12a does not exercise — because 12b re-uses this same
conftest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from friendex.application.activity_service import ActivityService
    from friendex.application.daily_service import DailyService
    from friendex.application.discipline_service import DisciplineService
    from friendex.application.fund_service import FundService
    from friendex.application.portfolio_service import PortfolioService
    from friendex.application.stats_service import StatsService
    from friendex.application.trading_service import TradingService
    from friendex.application.voice_ping_service import VoicePingService


# Stub Discord snowflake IDs — large positive ints so they always serialise
# through ``str(guild.id)`` cleanly (real Discord IDs are 64-bit unsigned ints
# rendered as decimal strings).
_DEFAULT_USER_ID = 9876543210
_DEFAULT_GUILD_ID = 1234567890


# ---------------------------------------------------------------------------
# Discord-event factories
#
# Each factory returns a permissive :class:`MagicMock` with just enough
# attributes for the listener under test. Snowflake-shaped ids are real
# ``int`` s (Discord ids are 64-bit ints rendered as decimal strings); the
# listener stringifies them at the routing boundary.


@pytest.fixture
def fake_message() -> Callable[..., MagicMock]:
    """Factory that builds a stub :class:`discord.Message`.

    The returned :class:`MagicMock` exposes:

    * ``author.id`` — int (snowflake)
    * ``author.bot`` — bool
    * ``guild.id`` — int (snowflake)
    * ``content`` — str
    * ``reference`` — :class:`MagicMock` with ``message_id`` (or ``None``)
    * ``mentions`` — list of stub user mocks (each with ``.id`` populated)
    * ``attachments`` — empty list by default
    """

    def _make(
        *,
        author_id: int,
        guild_id: int,
        content: str = "",
        is_bot: bool = False,
        reference_id: int | None = None,
        mentions: list[int] | None = None,
    ) -> MagicMock:
        message = MagicMock(name="Message")
        message.author.id = author_id
        message.author.bot = is_bot
        message.guild.id = guild_id
        message.content = content
        message.attachments = []
        if reference_id is None:
            message.reference = None
        else:
            ref = MagicMock(name="MessageReference")
            ref.message_id = reference_id
            message.reference = ref
        mention_mocks: list[MagicMock] = []
        for mention_id in mentions or []:
            mention = MagicMock(name="Mention")
            mention.id = mention_id
            mention_mocks.append(mention)
        message.mentions = mention_mocks
        return message

    return _make


@pytest.fixture
def fake_member() -> Callable[..., MagicMock]:
    """Factory that builds a stub :class:`discord.Member`.

    The returned :class:`MagicMock` exposes:

    * ``id`` — int (snowflake)
    * ``bot`` — bool (defaults False; flip in tests as needed)
    * ``guild.id`` — int (snowflake)
    * ``timed_out_until`` — :class:`datetime` | ``None``
    """

    def _make(
        *,
        user_id: int,
        guild_id: int,
        timed_out_until: datetime | None = None,
    ) -> MagicMock:
        member = MagicMock(name="Member")
        member.id = user_id
        member.bot = False
        member.guild.id = guild_id
        member.timed_out_until = timed_out_until
        return member

    return _make


@pytest.fixture
def fake_voice_state() -> Callable[..., MagicMock]:
    """Factory that builds a stub :class:`discord.VoiceState`.

    The returned :class:`MagicMock` exposes:

    * ``channel`` — :class:`MagicMock` with ``.id`` (or ``None`` if not in VC)
    """

    def _make(*, channel_id: int | None) -> MagicMock:
        state = MagicMock(name="VoiceState")
        if channel_id is None:
            state.channel = None
        else:
            channel = MagicMock(name="VoiceChannel")
            channel.id = channel_id
            state.channel = channel
        return state

    return _make


# ---------------------------------------------------------------------------
# Application-service AsyncMock fixtures.
#
# Each fixture uses ``spec=`` to bind the mock's attribute surface to the
# real service class: ``spec`` ensures every async method on the class is
# present as an :class:`AsyncMock` on the mock, *and* that any typo'd
# method access raises :class:`AttributeError` instead of silently no-op'ing.


def _async_mock_for(cls: type) -> AsyncMock:
    """Build an :class:`AsyncMock` whose surface tracks ``cls``."""
    return AsyncMock(spec=cls)


@pytest.fixture
def activity_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`ActivityService`."""
    from friendex.application.activity_service import ActivityService

    return _async_mock_for(ActivityService)


@pytest.fixture
def discipline_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`DisciplineService`."""
    from friendex.application.discipline_service import DisciplineService

    return _async_mock_for(DisciplineService)


@pytest.fixture
def voice_ping_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`VoicePingService`."""
    from friendex.application.voice_ping_service import VoicePingService

    return _async_mock_for(VoicePingService)


@pytest.fixture
def trading_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`TradingService` (Phase 12b carry-over)."""
    from friendex.application.trading_service import TradingService

    return _async_mock_for(TradingService)


@pytest.fixture
def fund_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`FundService` (Phase 12b carry-over)."""
    from friendex.application.fund_service import FundService

    return _async_mock_for(FundService)


@pytest.fixture
def daily_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`DailyService` (Phase 12b carry-over)."""
    from friendex.application.daily_service import DailyService

    return _async_mock_for(DailyService)


@pytest.fixture
def portfolio_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`PortfolioService` (Phase 12b carry-over)."""
    from friendex.application.portfolio_service import PortfolioService

    return _async_mock_for(PortfolioService)


@pytest.fixture
def stats_service() -> AsyncMock:
    """AsyncMock stand-in for :class:`StatsService` (Phase 12b carry-over)."""
    from friendex.application.stats_service import StatsService

    return _async_mock_for(StatsService)


# ---------------------------------------------------------------------------
# Per-guild service factories.
#
# Each factory is ``Callable[[str], TService]`` — the same shape Phase 14's
# composition layer will inject (Phase 9 service_factory digest). The
# test-time factory returns the same mock regardless of ``guild_id`` so
# tests exercise the routing call without setting up per-guild branches.


@pytest.fixture
def activity_service_factory(
    activity_service: AsyncMock,
) -> Callable[[str], ActivityService]:
    """Return a factory yielding ``activity_service`` for any guild id."""

    def _factory(_guild_id: str) -> ActivityService:
        return activity_service  # type: ignore[return-value]

    return _factory


@pytest.fixture
def discipline_service_factory(
    discipline_service: AsyncMock,
) -> Callable[[str], DisciplineService]:
    """Return a factory yielding ``discipline_service`` for any guild id."""

    def _factory(_guild_id: str) -> DisciplineService:
        return discipline_service  # type: ignore[return-value]

    return _factory


@pytest.fixture
def voice_ping_service_factory(
    voice_ping_service: AsyncMock,
) -> Callable[[str], VoicePingService]:
    """Return a factory yielding ``voice_ping_service`` for any guild id."""

    def _factory(_guild_id: str) -> VoicePingService:
        return voice_ping_service  # type: ignore[return-value]

    return _factory


@pytest.fixture
def trading_service_factory(
    trading_service: AsyncMock,
) -> Callable[[str], TradingService]:
    """Return a factory yielding ``trading_service`` for any guild id."""

    def _factory(_guild_id: str) -> TradingService:
        return trading_service  # type: ignore[return-value]

    return _factory


@pytest.fixture
def fund_service_factory(
    fund_service: AsyncMock,
) -> Callable[[str], FundService]:
    """Return a factory yielding ``fund_service`` for any guild id."""

    def _factory(_guild_id: str) -> FundService:
        return fund_service  # type: ignore[return-value]

    return _factory


@pytest.fixture
def daily_service_factory(
    daily_service: AsyncMock,
) -> Callable[[str], DailyService]:
    """Return a factory yielding ``daily_service`` for any guild id."""

    def _factory(_guild_id: str) -> DailyService:
        return daily_service  # type: ignore[return-value]

    return _factory


@pytest.fixture
def portfolio_service_factory(
    portfolio_service: AsyncMock,
) -> Callable[[str], PortfolioService]:
    """Return a factory yielding ``portfolio_service`` for any guild id."""

    def _factory(_guild_id: str) -> PortfolioService:
        return portfolio_service  # type: ignore[return-value]

    return _factory


@pytest.fixture
def stats_service_factory(
    stats_service: AsyncMock,
) -> Callable[[str], StatsService]:
    """Return a factory yielding ``stats_service`` for any guild id."""

    def _factory(_guild_id: str) -> StatsService:
        return stats_service  # type: ignore[return-value]

    return _factory


# Default values exposed so tests can reference them by name if they want
# (mirrors the cogs/ conftest convention).
DEFAULT_USER_ID = _DEFAULT_USER_ID
DEFAULT_GUILD_ID = _DEFAULT_GUILD_ID
