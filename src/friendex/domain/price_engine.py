"""Pure price-engine math for Friendex stocks.

Every function here is a pure function of its arguments — no globals, no I/O,
no mutation of inputs. They mirror the price logic of the original monolith
(see ``docs/spec/original-skeleton.md`` §PRICE MANAGEMENT / §ENGAGEMENT).

**Numeric typing (Phase 3.1 invariant — Decimal at the boundary):**

* Price / money parameters and return values are :class:`~decimal.Decimal` so
  trade accounting never drifts via IEEE-754. All money returns are quantised
  to currency precision (:data:`CENT` = ``Decimal('0.01')``) with banker's
  rounding (``ROUND_HALF_EVEN``).
* Rate / factor tunables (``k``, ``decay``) stay ``float`` to match
  ``Settings`` — they are multipliers, not money.
* Transcendental math (``ln`` in :func:`compute_activity_return`) is computed
  in ``float`` then converted back to a quantised ``Decimal``.

The ``min_price`` floor (default $70) is passed in explicitly rather than read
from a module-level constant, keeping the domain layer free of configuration.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from friendex.domain.activity import calculate_trending_score

if TYPE_CHECKING:
    from friendex.domain.models import ActivityBucket

# Currency quantisation unit — two decimal places.
CENT = Decimal("0.01")

# Trade impact in the original bot is expressed per-100-shares:
# ``price ± k * (volume / 100)``.
_SHARES_PER_IMPACT_UNIT = Decimal("100")

# Down-move attenuation window (in dollars) used by :func:`apply_floor_stall`:
# the closer the current price is to the floor, the smaller the realised drop.
_ATTENUATION_DISTANCE = Decimal("10.0")
_MIN_DISTANCE = Decimal("0.1")


def _quantise(value: Decimal) -> Decimal:
    """Round ``value`` to two decimal places with banker's rounding."""
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def apply_trade_impact(
    current: Decimal,
    shares: int,
    is_buy: bool,
    k: float,
    min_price: Decimal,
) -> Decimal:
    """Return the price after a buy/sell trade shifts it.

    A buy nudges the price up, a sell nudges it down, by
    ``k * (shares / 100)``. The result is clamped to ``min_price`` and
    quantised to cents. Inputs are never mutated.
    """
    impact = Decimal(str(k)) * (Decimal(shares) / _SHARES_PER_IMPACT_UNIT)
    proposed = current + impact if is_buy else current - impact
    floored = max(proposed, min_price)
    return _quantise(floored)


def apply_floor_stall(
    current: Decimal,
    proposed: Decimal,
    min_price: Decimal,
) -> Decimal:
    """Clamp a proposed price to the floor, stalling drops near the floor.

    * **Up move** (``proposed >= current``): clamp at or above ``min_price``.
    * **Down move**: the realised drop is attenuated the closer ``current`` is
      to ``min_price`` (a stock near the floor sinks more slowly), and the
      result never falls below ``min_price``.

    Mirrors ``apply_floor_stall`` in the original skeleton.
    """
    if proposed >= current:
        return _quantise(max(proposed, min_price))

    if current <= min_price:
        return _quantise(min_price)

    distance = max(current - min_price, _MIN_DISTANCE)
    attenuation = min(Decimal("1.0"), distance / _ATTENUATION_DISTANCE)
    new_price = current - (current - proposed) * attenuation
    return _quantise(max(new_price, min_price))


def compute_activity_return(bucket: ActivityBucket, k: float) -> Decimal:
    """Return the price delta earned by a bucket's engagement.

    The bucket's many activity fields collapse to a single weighted scalar via
    :func:`~friendex.domain.activity.calculate_trending_score`; the price
    return is then ``ΔP = k · ln(1 + activity)``. The natural log is computed
    in ``float`` and the result quantised back to cents. A bucket with no
    activity yields ``Decimal('0.00')``.
    """
    activity = calculate_trending_score(bucket)
    delta = k * math.log1p(activity)
    return _quantise(Decimal(str(delta)))


def apply_inactivity_decay(
    current: Decimal,
    decay: float,
    min_price: Decimal,
) -> Decimal:
    """Return the price after an inactivity tick decays it.

    The price drops to ``current * (1 - decay)`` and is clamped to
    ``min_price``, then quantised to cents. Inputs are never mutated.
    """
    proposed = current * (Decimal("1") - Decimal(str(decay)))
    return _quantise(max(proposed, min_price))
