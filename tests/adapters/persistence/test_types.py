"""Tests for the custom SQLAlchemy column types (``DecimalText`` / ``UtcDateTime``).

These exercise ``process_bind_param`` and ``process_result_value`` directly
without a live SQLite engine, so the contract is checked at the type-decorator
boundary — the layer where Phase 3.1's "money is Decimal, datetimes are UTC-
aware" invariants are enforced.

Item #82 M7 in particular pins the *read-side* behaviour: a naive datetime
coming back from the column type means upstream schema drift (some other
process wrote a value without tz info), and the type must surface the bug
rather than silently UTC-tag the value and let wrong data flow inward.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from friendex.adapters.persistence.types import DecimalText, UtcDateTime

# ---------------------------------------------------------------------------
# UtcDateTime — bind side (unchanged contract; pinned for regression)
# ---------------------------------------------------------------------------


def test_utc_datetime_bind_rejects_naive() -> None:
    """A naive datetime at the bind boundary is a programmer bug, not data."""
    column = UtcDateTime()
    with pytest.raises(ValueError, match="tz-aware"):
        column.process_bind_param(datetime(2026, 5, 29, 12, 0, 0), dialect=object())


def test_utc_datetime_bind_normalises_non_utc_to_utc() -> None:
    """An aware non-UTC datetime is converted to UTC before storage."""
    column = UtcDateTime()
    plus_one = timezone(timedelta(hours=1))
    value = datetime(2026, 5, 29, 13, 0, 0, tzinfo=plus_one)

    stored = column.process_bind_param(value, dialect=object())

    assert stored is not None
    parsed = datetime.fromisoformat(stored)
    assert parsed == datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)


def test_utc_datetime_bind_none_passes_through() -> None:
    """Nullable columns must round-trip ``None`` unchanged on the bind side."""
    column = UtcDateTime()
    assert column.process_bind_param(None, dialect=object()) is None


# ---------------------------------------------------------------------------
# UtcDateTime — read side (#82 M7 hardening: reject naive datetimes)
# ---------------------------------------------------------------------------


def test_utc_datetime_read_returns_tz_aware_utc() -> None:
    """A stored ISO-8601 UTC string reloads as tz-aware UTC (happy path)."""
    column = UtcDateTime()
    stored = "2026-05-29T12:00:00+00:00"

    loaded = column.process_result_value(stored, dialect=object())

    assert loaded == datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)
    assert loaded is not None
    assert loaded.tzinfo is not None


def test_utc_datetime_read_rejects_naive_iso_string() -> None:
    """A naive ISO-8601 string at the read boundary signals schema drift.

    #82 M7 — silently UTC-tagging a naive read masks a column that was written
    without tz info (a different writer / older migration / direct SQL). The
    column type must surface the bug at the boundary instead of producing a
    superficially-correct but semantically-wrong tz-aware value.
    """
    column = UtcDateTime()
    naive_stored = "2026-05-29T12:00:00"

    with pytest.raises(ValueError, match="tz-aware"):
        column.process_result_value(naive_stored, dialect=object())


def test_utc_datetime_read_normalises_non_utc_to_utc() -> None:
    """An aware non-UTC string reloads as UTC (defensive against legacy data)."""
    column = UtcDateTime()
    stored = "2026-05-29T13:00:00+01:00"

    loaded = column.process_result_value(stored, dialect=object())

    assert loaded == datetime(2026, 5, 29, 12, 0, 0, tzinfo=UTC)
    assert loaded is not None
    assert loaded.tzinfo == UTC


def test_utc_datetime_read_none_passes_through() -> None:
    """Nullable columns must round-trip ``None`` unchanged on the read side."""
    column = UtcDateTime()
    assert column.process_result_value(None, dialect=object()) is None


# ---------------------------------------------------------------------------
# DecimalText — bind / read contract (pinned to prevent regression)
# ---------------------------------------------------------------------------


def test_decimal_text_round_trips_exact_quantisation() -> None:
    """``Decimal('100.00')`` reloads with the same scale (no float coercion)."""
    column = DecimalText()
    stored = column.process_bind_param(Decimal("100.00"), dialect=object())
    assert stored == "100.00"

    loaded = column.process_result_value(stored, dialect=object())
    assert loaded == Decimal("100.00")
    assert isinstance(loaded, Decimal)
    assert loaded is not None
    assert loaded.as_tuple().exponent == Decimal("100.00").as_tuple().exponent


def test_decimal_text_bind_rejects_non_decimal() -> None:
    """Binding a float is a programmer bug (would lose precision)."""
    column = DecimalText()
    with pytest.raises(TypeError, match="Decimal"):
        column.process_bind_param(100.0, dialect=object())  # type: ignore[arg-type]
