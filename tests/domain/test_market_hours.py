"""Tests for ``friendex.domain.market_hours``.

Market-hours math is pure, clock-input-driven game logic. The trading week is
Monday-Saturday (Sunday closed) and the daily window wraps past midnight
(open 06:30 .. close 04:30 the next day). All functions take their inputs
explicitly — the open/close ``time`` objects come from ``Settings`` rather than
module-level constants — so behaviour is fully determined by the arguments.

Datetimes here are timezone-aware UTC to honour the Phase 3.1 invariant.
``weekday()`` is timezone-agnostic (Monday=0 .. Sunday=6), so a fixed naive vs
aware distinction does not change the day-of-week result, but we keep every
instant aware to match domain conventions.
"""

from datetime import UTC, datetime, time

import pytest

from friendex.domain.market_hours import (
    is_market_open,
    is_sunday,
    is_trading_day,
)

# Mirror the documented Settings defaults (06:30 open, 04:30 next-day close).
MARKET_OPEN = time(6, 30)
MARKET_CLOSE = time(4, 30)

# A reference week. 2026-05-18 is a Monday; the seven consecutive days run
# Monday(18) .. Sunday(24). All aware UTC, fixed at noon unless a test needs a
# specific time-of-day.
MONDAY = datetime(2026, 5, 18, 12, 0, tzinfo=UTC)
TUESDAY = datetime(2026, 5, 19, 12, 0, tzinfo=UTC)
WEDNESDAY = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
THURSDAY = datetime(2026, 5, 21, 12, 0, tzinfo=UTC)
FRIDAY = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)
SATURDAY = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)
SUNDAY = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# is_trading_day  (Mon-Sat are trading days; Sunday is not)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dt",
    [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY],
)
def test_monday_through_saturday_are_trading_days(dt: datetime) -> None:
    assert is_trading_day(dt) is True


def test_sunday_is_not_a_trading_day() -> None:
    assert is_trading_day(SUNDAY) is False


def test_trading_day_returns_bool() -> None:
    assert isinstance(is_trading_day(MONDAY), bool)


# ---------------------------------------------------------------------------
# is_sunday
# ---------------------------------------------------------------------------


def test_sunday_is_sunday() -> None:
    assert is_sunday(SUNDAY) is True


@pytest.mark.parametrize(
    "dt",
    [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY],
)
def test_non_sunday_days_are_not_sunday(dt: datetime) -> None:
    assert is_sunday(dt) is False


def test_is_sunday_returns_bool() -> None:
    assert isinstance(is_sunday(MONDAY), bool)


# ---------------------------------------------------------------------------
# is_market_open  (overnight window: open 06:30 .. close 04:30 next day)
# ---------------------------------------------------------------------------


def _at(base: datetime, hour: int, minute: int) -> datetime:
    """Return ``base`` with the time-of-day replaced (timezone preserved)."""
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_sunday_market_closed_by_default() -> None:
    assert (
        is_market_open(
            _at(SUNDAY, 12, 0),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is False
    )


def test_saturday_open_during_window() -> None:
    assert (
        is_market_open(
            _at(SATURDAY, 12, 0),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is True
    )


def test_monday_open_during_window() -> None:
    assert (
        is_market_open(
            _at(MONDAY, 12, 0),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is True
    )


def test_overnight_wrap_two_am_is_open() -> None:
    # 02:00 is before the 04:30 close, inside the overnight tail of the window.
    assert (
        is_market_open(
            _at(SATURDAY, 2, 0),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is True
    )


def test_overnight_wrap_five_am_is_closed() -> None:
    # 05:00 is after 04:30 close and before 06:30 open — the daily dead zone.
    assert (
        is_market_open(
            _at(SATURDAY, 5, 0),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is False
    )


def test_exactly_at_open_minute_is_open() -> None:
    # 06:30 sharp — the open boundary is inclusive.
    assert (
        is_market_open(
            _at(SATURDAY, 6, 30),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is True
    )


def test_one_minute_before_open_is_closed() -> None:
    # 06:29 — just inside the dead zone before open.
    assert (
        is_market_open(
            _at(SATURDAY, 6, 29),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is False
    )


def test_exactly_at_close_minute_is_closed() -> None:
    # 04:30 sharp — the close boundary is exclusive.
    assert (
        is_market_open(
            _at(SATURDAY, 4, 30),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is False
    )


def test_one_minute_before_close_is_open() -> None:
    # 04:29 — still inside the overnight tail.
    assert (
        is_market_open(
            _at(SATURDAY, 4, 29),
            MARKET_OPEN,
            MARKET_CLOSE,
        )
        is True
    )


def test_sunday_buy_allowed_flips_sunday_open() -> None:
    # With the Sunday-buy toggle, a Sunday instant inside the window is open.
    assert (
        is_market_open(
            _at(SUNDAY, 12, 0),
            MARKET_OPEN,
            MARKET_CLOSE,
            sunday_buy_allowed=True,
        )
        is True
    )


def test_sunday_buy_allowed_still_respects_window() -> None:
    # The toggle un-blocks Sunday, but the time-of-day window still applies:
    # 05:00 (dead zone) stays closed even with sunday_buy_allowed.
    assert (
        is_market_open(
            _at(SUNDAY, 5, 0),
            MARKET_OPEN,
            MARKET_CLOSE,
            sunday_buy_allowed=True,
        )
        is False
    )


def test_non_overnight_window_uses_simple_range() -> None:
    # When open < close (a same-day window), the function must use the simple
    # ``open <= t < close`` rule, not the wrap branch.
    day_open = time(9, 0)
    day_close = time(17, 0)
    assert is_market_open(_at(SATURDAY, 12, 0), day_open, day_close) is True
    assert is_market_open(_at(SATURDAY, 8, 0), day_open, day_close) is False
    assert is_market_open(_at(SATURDAY, 17, 0), day_open, day_close) is False
    assert is_market_open(_at(SATURDAY, 9, 0), day_open, day_close) is True


def test_is_market_open_returns_bool() -> None:
    assert isinstance(
        is_market_open(_at(SATURDAY, 12, 0), MARKET_OPEN, MARKET_CLOSE),
        bool,
    )


# ---------------------------------------------------------------------------
# Naive datetime guard
#
# Issue #84 L (silent-failures branch): a naive ``datetime.now()`` call at a
# call site produced a silently wrong market-hours decision because the
# function happily computed ``dt.time()`` without checking ``dt.tzinfo``.
# The fix is to raise ``ValueError`` so the caller has to think about the
# timezone — match the Phase 3.1 UTC-aware invariant.
# ---------------------------------------------------------------------------


def test_is_market_open_rejects_naive_datetime() -> None:
    """A naive ``datetime`` instance must raise rather than silently mis-deciding."""
    naive_saturday = datetime(2026, 5, 23, 12, 0)  # tzinfo=None
    with pytest.raises(ValueError, match="tz-aware"):
        is_market_open(naive_saturday, MARKET_OPEN, MARKET_CLOSE)


def test_is_market_open_rejects_naive_even_with_sunday_buy_allowed() -> None:
    """The naive-tz guard fires before the sunday-buy branch."""
    naive_sunday = datetime(2026, 5, 24, 12, 0)
    with pytest.raises(ValueError, match="tz-aware"):
        is_market_open(
            naive_sunday,
            MARKET_OPEN,
            MARKET_CLOSE,
            sunday_buy_allowed=True,
        )
