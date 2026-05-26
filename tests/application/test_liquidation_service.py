"""Behavioural tests for :class:`LiquidationService` (Phase 8f).

The liquidation service sweeps every account in the guild for short positions
whose target price has rallied to at least
``entry_price * settings.liquidation_threshold`` (default 1.5x). Each such
short is auto-covered via the private
:meth:`TradingService._cover_internal(..., force=True)` so the freeze guard
is bypassed (a liquidation cannot be blocked by the short's post-open freeze
window).

Acceptance criteria pinned here:

* **F1** — a short at 149% of entry is NOT liquidated (just under the 150%
  threshold).
* **F2** — a short at exactly 150% of entry IS liquidated.
* **F3** — a FROZEN short IS still liquidated (the public
  :meth:`TradingService.cover` would raise :class:`PositionFrozen`; the
  liquidation path bypasses it via ``_cover_internal(force=True)``).
* **F4** — :class:`LiquidationEvent` payload correctness (holder, target,
  shares, entry, exit, collateral returned, P&L) for a concrete scenario.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.liquidation_events import LiquidationEvent
from friendex.application.liquidation_service import LiquidationService
from friendex.application.trading_service import TradingService
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    ShortPosition,
    Stock,
    UserAccount,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import (
        FakeFundRepo,
        FakePriceRepo,
        FakeTradeCooldownRepo,
        FakeUserRepo,
    )


GUILD = "100000000000000001"
HOLDER = "holder-1"
TARGET = "target-1"

NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
# Far enough in the past that a default 30-minute freeze window has elapsed.
SHORT_CREATED_AT = NOW - timedelta(hours=2)


def _account(
    user_id: str,
    *,
    cash: Decimal = Decimal("10000.00"),
    short_positions: dict[str, ShortPosition] | None = None,
    opt_in: bool = True,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the liquidation tests."""
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=cash,
        month_start_net_worth=cash,
        long_positions={},
        short_positions=short_positions or {},
        today=ActivityBucket(bucket_start=NOW),
        week=ActivityBucket(bucket_start=NOW),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=NOW,
        opt_in=opt_in,
    )


def _stock(user_id: str, *, current: Decimal) -> Stock:
    """Build a minimal valid :class:`Stock` with empty history."""
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _short(
    target_id: str,
    *,
    shares: int,
    entry_price: Decimal,
    locked_cash: Decimal,
    locked_fund: Decimal = Decimal("0.00"),
    frozen: bool = False,
) -> ShortPosition:
    """Build a minimal valid :class:`ShortPosition` for the liquidation tests."""
    return ShortPosition(
        target_user_id=target_id,
        shares=shares,
        entry_price=entry_price,
        locked_cash=locked_cash,
        locked_fund=locked_fund,
        created_at=SHORT_CREATED_AT,
        frozen=frozen,
    )


def _make_services(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    fund_repo: FakeFundRepo,
    cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    settings: Settings,
) -> tuple[TradingService, LiquidationService]:
    """Build :class:`TradingService` + :class:`LiquidationService` wired together."""
    trading = TradingService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=lock_manager,
        settings=settings,
    )
    liquidation = LiquidationService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=lock_manager,
        settings=settings,
        trading_service=trading,
    )
    return trading, liquidation


# ---------------------------------------------------------------------------
# F1 — short at 149% of entry is NOT liquidated
# ---------------------------------------------------------------------------


async def test_short_below_threshold_not_liquidated(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F1: entry 100, price 149 (149%) → no liquidation (threshold 1.5x)."""
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
    )
    await fake_user_repo.upsert(
        GUILD, _account(HOLDER, short_positions={TARGET: short})
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("149.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert events == []
    holder = await fake_user_repo.get(GUILD, HOLDER)
    assert holder is not None
    # Short is untouched.
    assert TARGET in holder.short_positions
    assert holder.short_positions[TARGET].shares == 10


# ---------------------------------------------------------------------------
# F2 — short at exactly 150% of entry IS liquidated
# ---------------------------------------------------------------------------


async def test_short_at_threshold_is_liquidated(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F2: entry 100, price 150 (exactly 150%) → liquidated."""
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
    )
    await fake_user_repo.upsert(
        GUILD, _account(HOLDER, short_positions={TARGET: short})
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("150.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert len(events) == 1
    holder = await fake_user_repo.get(GUILD, HOLDER)
    assert holder is not None
    # Short fully covered → position deleted (Phase 8c convention).
    assert TARGET not in holder.short_positions


# ---------------------------------------------------------------------------
# F3 — a FROZEN short is still liquidated (force=True bypasses PositionFrozen)
# ---------------------------------------------------------------------------


async def test_frozen_short_still_liquidated(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F3: a frozen short past 1.5x IS liquidated (the public cover would refuse)."""
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
        frozen=True,
    )
    await fake_user_repo.upsert(
        GUILD, _account(HOLDER, short_positions={TARGET: short})
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("160.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert len(events) == 1
    holder = await fake_user_repo.get(GUILD, HOLDER)
    assert holder is not None
    assert TARGET not in holder.short_positions


# ---------------------------------------------------------------------------
# F4 — LiquidationEvent payload correctness
# ---------------------------------------------------------------------------


async def test_liquidation_event_payload_correct(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F4: event carries holder, target, shares, entry, exit, collateral, P&L.

    Concrete scenario: holder shorted 10 shares at entry $100 with $1000 cash
    collateral (no fund collateral). The target price rallies to $150 (1.5x).
    Cover cost = 10 * 150 = $1500. Released collateral = $1000 (full close).
    P&L = (entry - exit) * shares = (100 - 150) * 10 = -$500 (loss).
    """
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
        locked_fund=Decimal("0.00"),
    )
    # Holder needs cash to pay the cover cost ($1500).
    await fake_user_repo.upsert(
        GUILD,
        _account(
            HOLDER,
            cash=Decimal("2000.00"),
            short_positions={TARGET: short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("150.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, LiquidationEvent)
    assert event.holder_id == HOLDER
    assert event.target_id == TARGET
    assert event.shares == 10
    assert event.entry_price == Decimal("100.00")
    assert event.exit_price == Decimal("150.00")
    assert event.collateral_returned == Decimal("1000.00")
    # P&L = (entry - exit) * shares = (100 - 150) * 10 = -500 (a loss).
    assert event.pnl == Decimal("-500.00")
    # Timestamp comes from the now passed to check_and_liquidate_shorts.
    assert event.timestamp == NOW


# ---------------------------------------------------------------------------
# Auxiliary — ensure a totally unrelated holder is not iterated needlessly
# (defensive coverage; the loop must skip accounts with no shorts).
# ---------------------------------------------------------------------------


async def test_account_with_no_shorts_is_skipped(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """An account without any short positions produces no events."""
    await fake_user_repo.upsert(GUILD, _account("bystander"))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("200.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert events == []
