"""Tests for :mod:`friendex.application.snapshot_models` invariants (#84 M / L).

Pin the read-only collection types added by the Wave 3 domain-consolidation
pass:

* :attr:`PortfolioSnapshot.long_positions` / ``short_positions`` accept a
  plain dict at runtime (existing service callers pass one) but typecheck
  as :class:`~collections.abc.Mapping`.
* :attr:`UserStats.engagement_tier` is narrowed to
  ``Literal["Elite", "High", "Medium", "Low"]``.
* :class:`FundInfoResult.from_fund` produces a read-only ``MappingProxyType``
  snapshot of the underlying :class:`HedgeFund.investors` dict.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest

from friendex.application.snapshot_models import (
    FundInfoResult,
    PortfolioSnapshot,
    UserStats,
)
from friendex.domain.models import HedgeFund, LongPosition, ShortPosition

_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# PortfolioSnapshot — Mapping positions (#84 M)
# ---------------------------------------------------------------------------


def test_portfolio_snapshot_accepts_dict_positions_at_runtime() -> None:
    """Existing services pass a plain dict — runtime behaviour unchanged.

    The Mapping type is structural; passing a dict still works.
    """
    longs = {
        "t1": LongPosition(target_user_id="t1", shares=5, avg_entry=Decimal("100.00"))
    }
    snapshot = PortfolioSnapshot(
        user_id="u1",
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        fund_balance=Decimal("0.00"),
        long_positions=longs,
        short_positions={},
    )
    assert snapshot.long_positions == longs


def test_portfolio_snapshot_is_frozen() -> None:
    """Embed builders cannot mutate the snapshot mid-render."""
    snapshot = PortfolioSnapshot(
        user_id="u1",
        cash_balance=Decimal("0"),
        net_worth=Decimal("0"),
        month_start_net_worth=Decimal("0"),
        fund_balance=Decimal("0"),
    )
    with pytest.raises(FrozenInstanceError):
        snapshot.cash_balance = Decimal("999")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# UserStats — Literal engagement_tier (#84 L)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier", ["Elite", "High", "Medium", "Low"])
def test_user_stats_accepts_every_valid_tier(tier: str) -> None:
    """All four tier strings the activity helper returns flow through cleanly."""
    stats = UserStats(
        user_id="u1",
        trending_score=42.0,
        engagement_tier=tier,  # type: ignore[arg-type]
        last_activity=_NOW,
    )
    assert stats.engagement_tier == tier


# ---------------------------------------------------------------------------
# FundInfoResult — immutable investors snapshot (#84 L)
# ---------------------------------------------------------------------------


def _fund() -> HedgeFund:
    return HedgeFund(
        fund_id="f1",
        name="Test Fund",
        manager_id="m1",
        cash_balance=Decimal("1000.00"),
        investors={"i1": Decimal("100.00"), "i2": Decimal("250.00")},
    )


def test_fund_info_from_fund_returns_mappingproxytype_view() -> None:
    fund = _fund()
    result = FundInfoResult.from_fund(
        fund,
        base_apy=0.15,
        effective_apy=0.15,
        has_penalty=False,
    )
    assert isinstance(result.investors_view, MappingProxyType)
    assert dict(result.investors_view) == {
        "i1": Decimal("100.00"),
        "i2": Decimal("250.00"),
    }


def test_fund_info_investors_view_rejects_mutation() -> None:
    """A consumer cannot scribble on the investor stakes via the DTO."""
    fund = _fund()
    result = FundInfoResult.from_fund(
        fund,
        base_apy=0.15,
        effective_apy=0.15,
        has_penalty=False,
    )
    with pytest.raises(TypeError):
        result.investors_view["new"] = Decimal("999")  # type: ignore[index]


def test_fund_info_view_is_independent_of_subsequent_aggregate_mutation() -> None:
    """The factory copies ``fund.investors`` before wrapping it, so a later
    mutation of the underlying aggregate does not race into the frozen
    DTO snapshot.
    """
    fund = _fund()
    result = FundInfoResult.from_fund(
        fund,
        base_apy=0.15,
        effective_apy=0.15,
        has_penalty=False,
    )
    fund.investors["new"] = Decimal("999")
    assert "new" not in result.investors_view


def test_fund_info_result_is_frozen() -> None:
    result = FundInfoResult.from_fund(
        _fund(),
        base_apy=0.15,
        effective_apy=0.15,
        has_penalty=False,
    )
    with pytest.raises(FrozenInstanceError):
        result.base_apy = 0.20  # type: ignore[misc]


def test_fund_info_bare_constructor_default_investors_view_is_empty() -> None:
    """Direct construction (legacy path) gets an empty read-only view by
    default rather than ``None`` — the field is always callable as a
    ``Mapping``.
    """
    result = FundInfoResult(
        fund=_fund(),
        base_apy=0.15,
        effective_apy=0.15,
        has_penalty=False,
    )
    assert isinstance(result.investors_view, MappingProxyType)
    assert dict(result.investors_view) == {}


# ---------------------------------------------------------------------------
# PortfolioSnapshot accepts ShortPosition too (sanity)
# ---------------------------------------------------------------------------


def test_portfolio_snapshot_accepts_short_position_dict() -> None:
    shorts = {
        "t1": ShortPosition(
            target_user_id="t1",
            shares=3,
            entry_price=Decimal("100.00"),
            locked_cash=Decimal("150.00"),
            locked_fund=Decimal("150.00"),
            created_at=_NOW,
        )
    }
    snapshot = PortfolioSnapshot(
        user_id="u1",
        cash_balance=Decimal("0"),
        net_worth=Decimal("0"),
        month_start_net_worth=Decimal("0"),
        fund_balance=Decimal("0"),
        short_positions=shorts,
    )
    assert snapshot.short_positions == shorts
