"""Tests for ``friendex.domain.fund_math``.

Fund math is pure accounting over the domain models. Per the Phase 3.1
Decimal-at-the-boundary invariant:

* Money parameters/returns (balances, accruals, net worth) are
  :class:`~decimal.Decimal` quantised to ``Decimal('0.01')``.
* Rate values (APYs) stay ``float`` to match ``Settings.hedge_fund_base_apy``;
  a ``Decimal`` ``penalty_apr`` is converted to ``float`` when subtracted from
  a float base rate.

Net-worth valuation convention exercised here (see fund_math docstring):
``cash + sum(long.shares * current_price) + sum(locked collateral -
short.shares * current_price) + the account's hedge-fund stake``.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from friendex.domain.fund_math import (
    compute_apy_accrual,
    compute_apy_accrual_raw,
    compute_effective_apy,
    compute_net_worth,
)
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    FundPenalty,
    HedgeFund,
    LongPosition,
    PricePoint,
    ShortPosition,
    Stock,
    UserAccount,
)

CENT = Decimal("0.01")
NOW = datetime(2026, 5, 23, 12, 0, tzinfo=UTC)


def _is_quantised(value: Decimal) -> bool:
    """True when ``value`` carries exactly two decimal places."""
    return value == value.quantize(CENT) and -value.as_tuple().exponent == 2


def _make_account(
    *,
    user_id: str = "u1",
    cash: Decimal = Decimal("0.00"),
    longs: dict[str, LongPosition] | None = None,
    shorts: dict[str, ShortPosition] | None = None,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for net-worth tests."""
    bucket = ActivityBucket(bucket_start=NOW)
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=Decimal("0.00"),
        month_start_net_worth=Decimal("0.00"),
        long_positions=longs or {},
        short_positions=shorts or {},
        today=bucket,
        week=bucket,
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=NOW,
    )


def _make_stock(user_id: str, current: Decimal) -> Stock:
    """Build a :class:`Stock` priced at ``current``."""
    return Stock(
        user_id=user_id,
        current=current,
        history=[PricePoint(price=current, timestamp=NOW)],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


# ---------------------------------------------------------------------------
# compute_apy_accrual
# ---------------------------------------------------------------------------


def test_monthly_accrual_is_balance_times_apy_over_twelve() -> None:
    # 1200 * 0.15 / 12 = 15.00
    result = compute_apy_accrual(Decimal("1200.00"), 0.15, "monthly")
    assert result == Decimal("15.00")


def test_annual_accrual_is_balance_times_apy() -> None:
    # 1200 * 0.15 = 180.00
    result = compute_apy_accrual(Decimal("1200.00"), 0.15, "annual")
    assert result == Decimal("180.00")


def test_annual_accrual_exceeds_monthly_for_same_inputs() -> None:
    monthly = compute_apy_accrual(Decimal("1000.00"), 0.15, "monthly")
    annual = compute_apy_accrual(Decimal("1000.00"), 0.15, "annual")
    assert annual > monthly


def test_zero_balance_accrues_nothing() -> None:
    assert compute_apy_accrual(Decimal("0.00"), 0.15, "monthly") == Decimal("0.00")


def test_zero_apy_accrues_nothing() -> None:
    assert compute_apy_accrual(Decimal("5000.00"), 0.0, "annual") == Decimal("0.00")


@pytest.mark.parametrize("period", ["monthly", "annual"])
def test_accrual_is_quantised(period: str) -> None:
    # 1000.33 * 0.15 / 12 = 12.50412... -> must quantise to two places.
    result = compute_apy_accrual(Decimal("1000.33"), 0.15, period)  # type: ignore[arg-type]
    assert _is_quantised(result)


def test_accrual_does_not_mutate_balance() -> None:
    balance = Decimal("1200.00")
    compute_apy_accrual(balance, 0.15, "monthly")
    assert balance == Decimal("1200.00")


# ---------------------------------------------------------------------------
# compute_apy_accrual_raw — unquantised accrual for sum-then-quantise call sites
# ---------------------------------------------------------------------------


def test_raw_accrual_skips_quantisation_for_sub_cent_values() -> None:
    """#82 H3: ``compute_apy_accrual_raw`` keeps sub-cent precision so callers
    that sum many small accruals can quantise the total once instead of
    individually rounding each share down to zero.
    """
    # 0.40 * 0.15 / 12 = 0.005 — under one cent.
    raw = compute_apy_accrual_raw(Decimal("0.40"), 0.15, "monthly")
    # The raw helper does NOT quantise; the unscaled multiplication is exact.
    assert raw == Decimal("0.005")
    # The quantising version rounds this away.
    assert compute_apy_accrual(Decimal("0.40"), 0.15, "monthly") == Decimal("0.00")


def test_raw_accrual_matches_quantised_when_total_is_clean() -> None:
    """A balance large enough that the raw product is already at cent
    precision must produce the same value through both APIs.
    """
    raw = compute_apy_accrual_raw(Decimal("1200.00"), 0.15, "monthly")
    quantised = compute_apy_accrual(Decimal("1200.00"), 0.15, "monthly")
    assert raw == Decimal("15.00")
    assert quantised == Decimal("15.00")


@pytest.mark.parametrize("period", ["monthly", "annual"])
def test_raw_accrual_does_not_mutate_balance(period: str) -> None:
    balance = Decimal("1200.00")
    compute_apy_accrual_raw(balance, 0.15, period)  # type: ignore[arg-type]
    assert balance == Decimal("1200.00")


# ---------------------------------------------------------------------------
# compute_effective_apy
# ---------------------------------------------------------------------------


def test_no_penalty_returns_base_apy() -> None:
    assert compute_effective_apy(0.15, None, NOW) == 0.15


def test_expired_penalty_is_ignored() -> None:
    # penalty_until in the past (<= now) -> penalty no longer applies.
    expired = FundPenalty(
        user_id="u1",
        penalty_apr=Decimal("0.05"),
        penalty_until=NOW - timedelta(days=1),
    )
    assert compute_effective_apy(0.15, expired, NOW) == 0.15


def test_penalty_expiring_exactly_now_is_ignored() -> None:
    # Boundary: penalty_until == now counts as expired (<= now).
    boundary = FundPenalty(
        user_id="u1",
        penalty_apr=Decimal("0.05"),
        penalty_until=NOW,
    )
    assert compute_effective_apy(0.15, boundary, NOW) == 0.15


def test_active_penalty_is_subtracted() -> None:
    active = FundPenalty(
        user_id="u1",
        penalty_apr=Decimal("0.05"),
        penalty_until=NOW + timedelta(days=7),
    )
    result = compute_effective_apy(0.15, active, NOW)
    assert result == pytest.approx(0.10)


def test_effective_apy_floored_at_zero() -> None:
    # A penalty larger than the base rate must not produce a negative APY.
    huge = FundPenalty(
        user_id="u1",
        penalty_apr=Decimal("0.50"),
        penalty_until=NOW + timedelta(days=7),
    )
    assert compute_effective_apy(0.15, huge, NOW) == 0.0


def test_effective_apy_returns_float() -> None:
    active = FundPenalty(
        user_id="u1",
        penalty_apr=Decimal("0.05"),
        penalty_until=NOW + timedelta(days=7),
    )
    assert isinstance(compute_effective_apy(0.15, active, NOW), float)


# ---------------------------------------------------------------------------
# compute_net_worth
# ---------------------------------------------------------------------------


def test_net_worth_cash_only() -> None:
    account = _make_account(cash=Decimal("10000.00"))
    result = compute_net_worth(account, {}, None)
    assert result == Decimal("10000.00")


def test_net_worth_zero_position_account_is_just_cash() -> None:
    account = _make_account(cash=Decimal("250.50"))
    result = compute_net_worth(account, {}, None)
    assert result == Decimal("250.50")


def test_net_worth_values_long_at_current_price() -> None:
    # 10 shares now worth 120 each = 1200 of stock value on top of cash.
    account = _make_account(
        cash=Decimal("500.00"),
        longs={
            "t1": LongPosition(
                target_user_id="t1", shares=10, avg_entry=Decimal("100.00")
            )
        },
    )
    prices = {"t1": _make_stock("t1", Decimal("120.00"))}
    result = compute_net_worth(account, prices, None)
    assert result == Decimal("1700.00")  # 500 + 10*120


def test_net_worth_mixed_long_and_short() -> None:
    # Cash 1000.
    # Long: 5 shares of t1 @ current 200 = +1000.
    # Short: 4 shares of t2, locked collateral 600, current price 110:
    #   collateral 600 - buyback (4*110=440) = +160.
    # Total = 1000 + 1000 + 160 = 2160.
    account = _make_account(
        cash=Decimal("1000.00"),
        longs={
            "t1": LongPosition(
                target_user_id="t1", shares=5, avg_entry=Decimal("150.00")
            )
        },
        shorts={
            "t2": ShortPosition(
                target_user_id="t2",
                shares=4,
                entry_price=Decimal("150.00"),
                locked_cash=Decimal("400.00"),
                locked_fund=Decimal("200.00"),
                created_at=NOW,
            )
        },
    )
    prices = {
        "t1": _make_stock("t1", Decimal("200.00")),
        "t2": _make_stock("t2", Decimal("110.00")),
    }
    result = compute_net_worth(account, prices, None)
    assert result == Decimal("2160.00")


def test_net_worth_includes_fund_stake_for_account() -> None:
    account = _make_account(user_id="u1", cash=Decimal("100.00"))
    fund = HedgeFund(
        fund_id="f1",
        name="Vault",
        manager_id="mgr",
        cash_balance=Decimal("9999.00"),
        investors={"u1": Decimal("750.00"), "other": Decimal("50.00")},
    )
    result = compute_net_worth(account, {}, fund)
    assert result == Decimal("850.00")  # 100 cash + 750 stake


def test_net_worth_ignores_fund_when_account_not_invested() -> None:
    account = _make_account(user_id="u1", cash=Decimal("100.00"))
    fund = HedgeFund(
        fund_id="f1",
        name="Vault",
        manager_id="mgr",
        cash_balance=Decimal("9999.00"),
        investors={"someone-else": Decimal("750.00")},
    )
    result = compute_net_worth(account, {}, fund)
    assert result == Decimal("100.00")


def test_net_worth_long_with_missing_price_contributes_only_cash() -> None:
    # A long position whose target has no Stock in ``prices`` adds nothing for
    # its price-valued component (defensive: missing market data).
    account = _make_account(
        cash=Decimal("300.00"),
        longs={
            "t1": LongPosition(
                target_user_id="t1", shares=10, avg_entry=Decimal("100.00")
            )
        },
    )
    result = compute_net_worth(account, {}, None)
    assert result == Decimal("300.00")


def test_net_worth_short_with_missing_price_counts_collateral_only() -> None:
    # A short with no matching Stock has zero buy-back cost, so only its locked
    # collateral counts toward net worth.
    account = _make_account(
        cash=Decimal("0.00"),
        shorts={
            "t2": ShortPosition(
                target_user_id="t2",
                shares=4,
                entry_price=Decimal("150.00"),
                locked_cash=Decimal("400.00"),
                locked_fund=Decimal("200.00"),
                created_at=NOW,
            )
        },
    )
    result = compute_net_worth(account, {}, None)
    assert result == Decimal("600.00")  # collateral only, no buyback


def test_net_worth_return_is_quantised() -> None:
    account = _make_account(
        cash=Decimal("100.00"),
        longs={
            "t1": LongPosition(
                target_user_id="t1", shares=3, avg_entry=Decimal("33.33")
            )
        },
    )
    prices = {"t1": _make_stock("t1", Decimal("33.33"))}
    assert _is_quantised(compute_net_worth(account, prices, None))


def test_net_worth_does_not_mutate_account_cash() -> None:
    account = _make_account(cash=Decimal("500.00"))
    compute_net_worth(account, {}, None)
    assert account.cash_balance == Decimal("500.00")
