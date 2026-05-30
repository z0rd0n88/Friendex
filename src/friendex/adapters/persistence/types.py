"""Custom SQLAlchemy column types that round-trip domain values exactly.

SQLite has no native ``DECIMAL`` or timezone-aware ``DATETIME`` storage — its
dynamic typing collapses ``NUMERIC`` columns to IEEE-754 floats and strips tz
information. Both would silently violate the Phase 3.1 invariants (money is
``Decimal``; datetimes are UTC-aware). These ``TypeDecorator`` wrappers store
the canonical *string* form and reconstruct the exact Python value on load:

* :class:`DecimalText` — persists ``str(Decimal)`` in a ``TEXT`` column, so
  ``Decimal('100.00')`` reloads as an equal ``Decimal`` with the same
  quantisation (``Decimal('100.0')`` and ``Decimal('100.00')`` stay distinct).
* :class:`UtcDateTime` — persists an ISO-8601 UTC string and reloads a
  tz-aware :class:`datetime` in UTC, normalising any aware input to UTC first
  and rejecting naive datetimes (a naive value is a bug at the call site).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

if TYPE_CHECKING:
    from sqlalchemy.engine import Dialect


class DecimalText(TypeDecorator[Decimal]):
    """Store a :class:`~decimal.Decimal` as its exact string form in ``TEXT``.

    Round-trips precision and quantisation losslessly, sidestepping SQLite's
    float-backed ``NUMERIC`` storage. The Python-side type is always
    ``Decimal``; ``None`` passes through unchanged for nullable columns.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal):
            raise TypeError(f"DecimalText expects Decimal, got {type(value).__name__}")
        return str(value)

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)


class UtcDateTime(TypeDecorator[datetime]):
    """Store a tz-aware UTC :class:`~datetime.datetime` as ISO-8601 ``TEXT``.

    Aware inputs are converted to UTC before storage; the loaded value is
    always tz-aware in UTC. Naive datetimes are rejected at **both**
    boundaries — the bind side because the domain layer never produces them
    (Phase 3.1), the read side because a stored value without tz info is a
    signal of schema drift (another writer / older migration / direct SQL
    insert that bypassed this type) and silently re-tagging it would let
    semantically-wrong data flow into the domain layer.
    """

    impl = String
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> str | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UtcDateTime requires a tz-aware datetime")
        return value.astimezone(UTC).isoformat()

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            # #82 M7 — surface schema drift loudly. Every value bound through
            # this type carries tz info; a naive value at the read boundary
            # means the column was written outside this code path. Re-tagging
            # would let semantically-wrong data flow inward; raising forces
            # the operator to investigate the offending writer.
            #
            # PR #92 review L-4: the message quotes the offending value but
            # cannot name the source column — a ``TypeDecorator`` does not
            # see the column it is bound to. Operators tracing a drift
            # should grep the writer set for the quoted value pattern.
            raise ValueError(
                f"UtcDateTime read a naive datetime ({value!r}); "
                "expected a tz-aware value (schema drift?)"
            )
        return parsed.astimezone(UTC)
