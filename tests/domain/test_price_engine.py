"""Tests for ``friendex.domain.price_engine``.

The price engine is pure game math: every function takes value objects and
tunables and returns a new :class:`~decimal.Decimal`. Money returns are
quantised to currency precision (``Decimal('0.01')``) and never drop below the
``min_price`` floor. Tunables (``k``, ``decay``) are plain ``float`` to match
``Settings``; price parameters/returns are ``Decimal`` per the Phase 3.1
invariant.
"""

from decimal import Decimal

import pytest

from friendex.domain.models import ActivityBucket
from friendex.domain.price_engine import (
    apply_floor_stall,
    apply_inactivity_decay,
    apply_trade_impact,
    compute_activity_return,
)

CENT = Decimal("0.01")
MIN_PRICE = Decimal("70.00")


def _is_quantised(value: Decimal) -> bool:
    """True when ``value`` carries exactly two decimal places."""
    exponent = value.as_tuple().exponent
    # ``exponent`` is ``int | Literal["n", "N", "F"]`` per the stubs (the
    # literal cases cover NaN / sNaN / inf). Real quantised currency values
    # carry an int exponent — narrow before negating.
    return (
        value == value.quantize(CENT) and isinstance(exponent, int) and -exponent == 2
    )


# ---------------------------------------------------------------------------
# apply_trade_impact
# ---------------------------------------------------------------------------


def test_buy_raises_price() -> None:
    result = apply_trade_impact(
        current=Decimal("100.00"),
        shares=100,
        is_buy=True,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert result > Decimal("100.00")


def test_sell_lowers_price() -> None:
    result = apply_trade_impact(
        current=Decimal("100.00"),
        shares=100,
        is_buy=False,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert result < Decimal("100.00")


@pytest.mark.parametrize("shares", [1, 50, 100, 500, 1000])
def test_buy_impact_scales_monotonically_with_shares(shares: int) -> None:
    smaller = apply_trade_impact(
        current=Decimal("100.00"),
        shares=shares,
        is_buy=True,
        k=0.5,
        min_price=MIN_PRICE,
    )
    larger = apply_trade_impact(
        current=Decimal("100.00"),
        shares=shares + 100,
        is_buy=True,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert larger > smaller


@pytest.mark.parametrize("k", [0.1, 0.5, 1.0, 2.0])
def test_buy_impact_scales_with_k(k: float) -> None:
    weaker = apply_trade_impact(
        current=Decimal("100.00"),
        shares=100,
        is_buy=True,
        k=k,
        min_price=MIN_PRICE,
    )
    stronger = apply_trade_impact(
        current=Decimal("100.00"),
        shares=100,
        is_buy=True,
        k=k + 0.5,
        min_price=MIN_PRICE,
    )
    assert stronger > weaker


def test_trade_impact_clamps_to_min_price() -> None:
    result = apply_trade_impact(
        current=Decimal("70.50"),
        shares=100000,
        is_buy=False,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert result == MIN_PRICE


def test_trade_impact_at_floor_sell_stays_at_floor() -> None:
    result = apply_trade_impact(
        current=MIN_PRICE,
        shares=100,
        is_buy=False,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert result == MIN_PRICE


def test_trade_impact_zero_shares_is_noop() -> None:
    result = apply_trade_impact(
        current=Decimal("100.00"),
        shares=0,
        is_buy=True,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert result == Decimal("100.00")


def test_trade_impact_return_is_quantised() -> None:
    result = apply_trade_impact(
        current=Decimal("100.00"),
        shares=33,
        is_buy=True,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert _is_quantised(result)


def test_trade_impact_does_not_mutate_inputs() -> None:
    current = Decimal("100.00")
    apply_trade_impact(
        current=current,
        shares=100,
        is_buy=True,
        k=0.5,
        min_price=MIN_PRICE,
    )
    assert current == Decimal("100.00")


# ---------------------------------------------------------------------------
# apply_floor_stall
# ---------------------------------------------------------------------------


def test_floor_stall_up_move_returns_proposed() -> None:
    result = apply_floor_stall(
        current=Decimal("100.00"),
        proposed=Decimal("110.00"),
        min_price=MIN_PRICE,
    )
    assert result == Decimal("110.00")


def test_floor_stall_returns_floor_when_proposed_below_floor() -> None:
    result = apply_floor_stall(
        current=MIN_PRICE,
        proposed=Decimal("50.00"),
        min_price=MIN_PRICE,
    )
    assert result == MIN_PRICE


def test_floor_stall_up_move_below_floor_clamps_to_floor() -> None:
    result = apply_floor_stall(
        current=Decimal("60.00"),
        proposed=Decimal("65.00"),
        min_price=MIN_PRICE,
    )
    assert result == MIN_PRICE


def test_floor_stall_attenuates_down_move_near_floor() -> None:
    # A down move far from the floor moves nearly the full distance; the same
    # nominal drop close to the floor is attenuated (smaller realised drop).
    far = apply_floor_stall(
        current=Decimal("200.00"),
        proposed=Decimal("190.00"),
        min_price=MIN_PRICE,
    )
    near = apply_floor_stall(
        current=Decimal("75.00"),
        proposed=Decimal("65.00"),
        min_price=MIN_PRICE,
    )
    far_drop = Decimal("200.00") - far
    near_drop = Decimal("75.00") - near
    assert far_drop > near_drop


@pytest.mark.parametrize(
    ("current", "proposed", "expected"),
    [
        # Partial attenuation: distance-to-floor 8 < window 10, so the realised
        # drop scales by 8/10 = 0.8 → (78 - 70) * 0.8 = 6.40 → 78.00 - 6.40.
        (Decimal("78.00"), Decimal("70.00"), Decimal("71.60")),
        # distance 5 → factor 5/10 = 0.5 → (75 - 70) * 0.5 = 2.50 → 75.00 - 2.50.
        (Decimal("75.00"), Decimal("70.00"), Decimal("72.50")),
        # distance 20 >= window 10 → factor caps at 1.0 → full nominal drop of 10.
        (Decimal("90.00"), Decimal("80.00"), Decimal("80.00")),
    ],
)
def test_floor_stall_down_move_attenuation_magnitude(
    current: Decimal, proposed: Decimal, expected: Decimal
) -> None:
    # Pins the EXACT attenuated price, not merely ``far_drop > near_drop`` — so a
    # change to the attenuation window (``_ATTENUATION_DISTANCE``) or the formula
    # is caught. All cases sit well above the floor, so the result is the
    # attenuated price, never the ``min_price`` clamp.
    result = apply_floor_stall(current=current, proposed=proposed, min_price=MIN_PRICE)
    assert result == expected


def test_floor_stall_down_move_never_below_floor() -> None:
    result = apply_floor_stall(
        current=Decimal("71.00"),
        proposed=Decimal("60.00"),
        min_price=MIN_PRICE,
    )
    assert result >= MIN_PRICE


def test_floor_stall_return_is_quantised() -> None:
    result = apply_floor_stall(
        current=Decimal("123.45"),
        proposed=Decimal("120.07"),
        min_price=MIN_PRICE,
    )
    assert _is_quantised(result)


# ---------------------------------------------------------------------------
# apply_inactivity_decay
# ---------------------------------------------------------------------------


def test_inactivity_decay_drops_price() -> None:
    result = apply_inactivity_decay(
        current=Decimal("100.00"),
        decay=0.04,
        min_price=MIN_PRICE,
    )
    assert result == Decimal("96.00")


@pytest.mark.parametrize(
    ("current", "decay", "expected"),
    [
        (Decimal("100.00"), 0.04, Decimal("96.00")),
        (Decimal("200.00"), 0.10, Decimal("180.00")),
        (Decimal("100.00"), 0.0, Decimal("100.00")),
    ],
)
def test_inactivity_decay_arithmetic(
    current: Decimal, decay: float, expected: Decimal
) -> None:
    assert (
        apply_inactivity_decay(current=current, decay=decay, min_price=MIN_PRICE)
        == expected
    )


def test_inactivity_decay_clamps_to_floor() -> None:
    result = apply_inactivity_decay(
        current=Decimal("71.00"),
        decay=0.04,
        min_price=MIN_PRICE,
    )
    assert result == MIN_PRICE


def test_inactivity_decay_at_floor_stays_at_floor() -> None:
    result = apply_inactivity_decay(
        current=MIN_PRICE,
        decay=0.04,
        min_price=MIN_PRICE,
    )
    assert result == MIN_PRICE


def test_inactivity_decay_return_is_quantised() -> None:
    result = apply_inactivity_decay(
        current=Decimal("99.99"),
        decay=0.04,
        min_price=MIN_PRICE,
    )
    assert _is_quantised(result)


# ---------------------------------------------------------------------------
# compute_activity_return (ΔP = k · ln(1 + activity))
# ---------------------------------------------------------------------------


def test_activity_return_zero_activity_is_zero() -> None:
    result = compute_activity_return(ActivityBucket(), k=0.5)
    assert result == Decimal("0.00")


def test_activity_return_positive_for_activity() -> None:
    bucket = ActivityBucket(text_msgs=50, media_msgs=10, reaction_count=20)
    result = compute_activity_return(bucket, k=0.5)
    assert result > Decimal("0.00")


def test_activity_return_monotonic_in_activity() -> None:
    low = compute_activity_return(ActivityBucket(text_msgs=10), k=0.5)
    high = compute_activity_return(ActivityBucket(text_msgs=80), k=0.5)
    assert high > low


@pytest.mark.parametrize("k", [0.1, 0.5, 1.0, 2.0])
def test_activity_return_scales_with_k(k: float) -> None:
    bucket = ActivityBucket(text_msgs=50, media_msgs=10)
    weaker = compute_activity_return(bucket, k=k)
    stronger = compute_activity_return(bucket, k=k + 0.5)
    assert stronger > weaker


def test_activity_return_uses_natural_log_formula() -> None:
    # ΔP = k · ln(1 + activity), where activity is the trending score.
    import math

    from friendex.domain.activity import calculate_trending_score

    bucket = ActivityBucket(text_msgs=40, media_msgs=5, reaction_count=15)
    k = 0.5
    activity = calculate_trending_score(bucket)
    expected = Decimal(str(k * math.log(1 + activity))).quantize(CENT)
    assert compute_activity_return(bucket, k=k) == expected


def test_activity_return_is_quantised() -> None:
    bucket = ActivityBucket(text_msgs=37, media_msgs=11)
    assert _is_quantised(compute_activity_return(bucket, k=0.5))
