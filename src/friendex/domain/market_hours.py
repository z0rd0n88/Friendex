"""Pure market-hours predicates for Friendex.

These functions decide whether trading is permitted at a given instant. They are
pure functions of their arguments — no globals, no I/O, no mutation. The trading
week and daily window mirror the original monolith (see
``docs/spec/original-skeleton.md`` §market hours, ``is_trading_day`` /
``is_sunday`` / ``is_market_open``):

* **Trading week:** Monday-Saturday are trading days; Sunday is closed.
  ``datetime.weekday()`` is Monday=0 .. Sunday=6.
* **Daily window:** open at ``market_open`` (default 06:30), close at
  ``market_close`` (default 04:30 *the next day*). Because the close is earlier
  in the clock than the open, the window wraps past midnight, so a time like
  02:00 is *inside* the window and 05:00 is *outside* it.

The open/close :class:`~datetime.time` values are passed in explicitly (sourced
from ``Settings`` at the call site) so the domain layer holds no configuration.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, time

# ``datetime.weekday()`` returns Monday=0 .. Sunday=6.
_SATURDAY = 5
_SUNDAY = 6


def is_trading_day(dt: datetime) -> bool:
    """Return ``True`` when ``dt`` falls on a trading day (Monday-Saturday).

    Sunday (``weekday() == 6``) is the only non-trading day. ``dt`` is not
    mutated.
    """
    return dt.weekday() <= _SATURDAY


def is_sunday(dt: datetime) -> bool:
    """Return ``True`` when ``dt`` falls on a Sunday."""
    return dt.weekday() == _SUNDAY


def is_market_open(
    dt: datetime,
    market_open: time,
    market_close: time,
    sunday_buy_allowed: bool = False,
) -> bool:
    """Return ``True`` when the market is open at instant ``dt``.

    Sunday is closed unless ``sunday_buy_allowed`` is set, in which case Sunday
    is treated like any other day for the time-of-day window check.

    The time-of-day window is inclusive of ``market_open`` and exclusive of
    ``market_close`` (``[open, close)``). When ``market_open >= market_close``
    the window wraps past midnight, so an instant is inside it when its time is
    at/after ``market_open`` *or* strictly before ``market_close``. ``dt`` is
    not mutated.

    ``dt`` must be timezone-aware *and anchored to UTC* (Phase 3.1
    invariant: the project is UTC-only). The guard rejects three failure
    modes — naive ``datetime`` (``tzinfo is None``), a custom ``tzinfo``
    whose ``utcoffset`` returns ``None`` (per the
    :mod:`datetime` protocol that's still not strictly tz-aware), and any
    non-zero offset (a caller passing ``datetime.now(tz=ZoneInfo("America/
    Chicago"))`` would otherwise get a silently wrong open/closed
    decision because the time-of-day window math is UTC-anchored). The
    check fires before the Sunday + time-of-day branches so the same
    error surfaces for every input. (PR #94 review L3 tightens the
    original ``tzinfo is None`` guard with both the ``utcoffset is None``
    edge case and the UTC-only assertion.)
    """
    if dt.tzinfo is None:
        raise ValueError("is_market_open requires a tz-aware datetime")
    offset = dt.utcoffset()
    if offset is None or offset != timedelta(0):
        raise ValueError("is_market_open requires a UTC-anchored datetime")

    if is_sunday(dt) and not sunday_buy_allowed:
        return False

    now = dt.time()

    if market_open < market_close:
        # Same-day window: a simple half-open range.
        return market_open <= now < market_close

    # Overnight window: open in the evening, closing after midnight.
    return now >= market_open or now < market_close
