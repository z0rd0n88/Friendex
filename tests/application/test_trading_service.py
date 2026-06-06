"""Behavioural tests for :class:`TradingService` (Phase 8c).

The service is the most complex application-layer use case in the migration —
it owns the buy / sell / short / cover use cases and enforces the full
game-rule envelope (market hours, opt-in, self-trade, cash floor, cooldown,
freeze, collateral split). Tests pin every acceptance criterion from the
Phase 8c spec and drive >=90% coverage of ``trading_service.py``.

Acceptance criteria pinned here, each named on its test(s):

* **C1** — buy happy path: cash debited, long position created/added, price
  rises by the immediate trade-impact amount, ``BuyResult`` populated.
* **C2** — sell happy path: shares removed from long position, cash credited,
  price drops by the immediate trade-impact amount.
* **C3** — short happy path: short position opened, collateral split between
  cash and 50% of the personal hedge fund per the original spec.
* **C4** — cover happy path: short closed, collateral refunded proportionally,
  positive P&L credited to cash on top, immediate price impact.
* **C5** — :class:`InsufficientFunds` raised when cash is short for a buy.
* **C6** — :class:`OptedOut` raised when ``target.opt_in is False``.
* **C7** — :class:`MarketClosed` raised outside hours; Sunday buy exception is
  honoured (sell is rejected on Sunday).
* **C8** — :class:`SelfTrade` raised when actor == target for every operation.
* **C9** — :class:`OnCooldown` raised for short/cover within
  ``trade_cooldown_seconds`` of the last short/cover (buy/sell are NOT
  cooldown-gated).
* **C10** — :class:`PositionFrozen` blocks the public :meth:`cover` (the
  liquidation bypass is Phase 8f's job; the public method always raises).
* **C11** — collateral correctly split between cash and fund on open AND
  released proportionally on cover.
* **C12** — weighted-average entry recalculated when ADDING to an existing
  long position (concrete numbers asserted).
* **C13** — position record DELETED when shares hit zero (sell/cover that
  fully closes).
* **C14** — :meth:`update_frozen_shorts` flips ``frozen=True`` for shorts past
  the freeze window and leaves recent ones alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from freezegun import freeze_time

from friendex.application.interfaces import TradeCooldown
from friendex.application.trading_service import TradingService
from friendex.domain.errors import (
    InsufficientFunds,
    InsufficientShares,
    InvalidAmount,
    MarketClosed,
    NoPosition,
    OnCooldown,
    OptedOut,
    PositionFrozen,
    SelfTrade,
)
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    HedgeFund,
    LongPosition,
    ShortPosition,
    Stock,
    UserAccount,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import (
        FakeFundRepo,
        FakePriceRepo,
        FakeTradeCooldownRepo,
        FakeUserRepo,
    )


GUILD = "100000000000000001"
BUYER = "buyer-1"
SELLER = "seller-1"
SHORTER = "shorter-1"
COVERER = "coverer-1"
TARGET = "target-1"

# A weekday inside the market-open window (06:30..04:30 next day). Monday
# 2026-05-25 at 12:00 UTC — well past 06:30 and well before 04:30 next day.
WEEKDAY_OPEN = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
# Sunday inside the time-of-day window — used to verify the Sunday-buy
# exception (sell rejects, buy permits).
SUNDAY_OPEN = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
# Monday at 05:00 UTC — inside the day, but the time-of-day check (06:30..04:30
# next day) sits in the brief 04:30..06:30 closed window.
WEEKDAY_CLOSED = datetime(2026, 5, 25, 5, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _account(
    user_id: str,
    *,
    cash: Decimal = Decimal("10000.00"),
    long_positions: dict[str, LongPosition] | None = None,
    short_positions: dict[str, ShortPosition] | None = None,
    opt_in: bool = True,
    last_activity: datetime | None = None,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the trading tests."""
    now = last_activity if last_activity is not None else datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=cash,
        month_start_net_worth=cash,
        long_positions=long_positions or {},
        short_positions=short_positions or {},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
        opt_in=opt_in,
    )


def _stock(user_id: str, *, current: Decimal = Decimal("100.00")) -> Stock:
    """Build a minimal valid :class:`Stock` with empty history."""
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _fund(user_id: str, *, cash: Decimal) -> HedgeFund:
    """Build a personal :class:`HedgeFund` keyed by ``fund_id == user_id``."""
    return HedgeFund(
        fund_id=user_id,
        name=user_id,
        manager_id=user_id,
        cash_balance=cash,
        investors={},
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    fund_repo: FakeFundRepo,
    cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    settings: Settings,
) -> TradingService:
    """Construct the service under test with explicit dependencies."""
    return TradingService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=lock_manager,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# C1 — buy happy path
# ---------------------------------------------------------------------------


async def test_buy_debits_cash_creates_position_and_raises_price(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C1: buy 10 shares at $100 debits $1000, creates the long, raises price."""
    await fake_user_repo.upsert(GUILD, _account(BUYER, cash=Decimal("5000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.buy(BUYER, TARGET, 10)

    assert result.shares == 10
    assert result.total_cost == Decimal("1000.00")
    assert result.old_price == Decimal("100.00")
    assert result.new_price > result.old_price  # buy raises the price
    assert result.new_cash_balance == Decimal("4000.00")
    assert result.position_after.shares == 10
    assert result.position_after.avg_entry == Decimal("100.00")

    after_buyer = await fake_user_repo.get(GUILD, BUYER)
    assert after_buyer is not None
    assert after_buyer.cash_balance == Decimal("4000.00")
    assert after_buyer.long_positions[TARGET].shares == 10

    after_stock = await fake_price_repo.get(GUILD, TARGET)
    assert after_stock is not None
    assert after_stock.current > Decimal("100.00")
    history = await fake_price_repo.get_history(GUILD, TARGET)
    assert len(history) == 1
    assert history[0].price == after_stock.current


# ---------------------------------------------------------------------------
# C2 — sell happy path
# ---------------------------------------------------------------------------


async def test_sell_removes_shares_credits_cash_and_lowers_price(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C2: sell 5 of 10 at $100 → 5 remain, +$500 cash, price drops."""
    seller = _account(
        SELLER,
        cash=Decimal("1000.00"),
        long_positions={
            TARGET: LongPosition(
                target_user_id=TARGET, shares=10, avg_entry=Decimal("80.00")
            )
        },
    )
    await fake_user_repo.upsert(GUILD, seller)
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.sell(SELLER, TARGET, 5)

    assert result.shares == 5
    assert result.total_revenue == Decimal("500.00")
    assert result.old_price == Decimal("100.00")
    assert result.new_price < result.old_price  # sell lowers the price
    assert result.new_cash_balance == Decimal("1500.00")
    assert result.position_after is not None
    assert result.position_after.shares == 5
    # avg_entry preserved on partial close
    assert result.position_after.avg_entry == Decimal("80.00")


# ---------------------------------------------------------------------------
# C3 — short happy path (collateral split is exercised here in detail)
# ---------------------------------------------------------------------------


async def test_short_opens_position_and_splits_collateral_between_cash_and_fund(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C3 / C11: shorter has $400 cash + $2000 fund (50%=$1000) — shorts 10 @ $100.

    notional = $1000; cash covers $400 entirely; remaining $600 drawn from the
    50% fund slice. Cash → $0; fund cash → $2000 - $600 = $1400. Locked split
    is therefore (400, 600).
    """
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("400.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("2000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.short(SHORTER, TARGET, 10)

    assert result.notional == Decimal("1000.00")
    assert result.locked_cash == Decimal("400.00")
    assert result.locked_fund == Decimal("600.00")
    assert result.new_cash_balance == Decimal("0.00")
    assert result.new_fund_balance == Decimal("1400.00")
    assert result.position_after.shares == 10
    assert result.position_after.entry_price == Decimal("100.00")
    assert result.position_after.frozen is False
    assert result.old_price == Decimal("100.00")
    assert result.new_price < result.old_price  # short lowers the price

    after_fund = await fake_fund_repo.get(GUILD, SHORTER)
    assert after_fund is not None
    assert after_fund.cash_balance == Decimal("1400.00")

    # The short/cover cooldown is set after a successful short.
    cooldown_now = WEEKDAY_OPEN
    cooldown = await fake_cooldown_repo.get(GUILD, SHORTER, now=cooldown_now)
    assert cooldown is not None
    assert cooldown.expires_at > cooldown_now


# ---------------------------------------------------------------------------
# C4 — cover happy path (proportional collateral refund + signed P&L)
# ---------------------------------------------------------------------------


async def test_cover_refunds_collateral_proportionally_and_credits_positive_pnl(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C4 / C11: cover 5 of 10 shorts at $80 (entry $100) — partial release + P&L.

    Pre-cover: 10 shares shorted at $100 with locked (400, 600); cash $0,
    fund $1400. Price drops to $80 before cover. Cover cost = 5 * $80 = $400.
    Proportion = 5/10 = 0.5 → released (200, 300). P&L = (100-80) * 5 = +$100
    (credited to cash). Final cash = 0 - 400 + 200 + 100 = -$100... fails the
    cash-floor pre-check unless cash is replenished. So seed cash to cover
    the cost up front.
    """
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("500.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_fund_repo.upsert(GUILD, _fund(COVERER, cash=Decimal("1400.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("80.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.cover(COVERER, TARGET, 5)

    assert result.cost == Decimal("400.00")
    assert result.released_cash == Decimal("200.00")
    assert result.released_fund == Decimal("300.00")
    assert result.pnl == Decimal("100.00")  # (100 - 80) * 5
    # Final cash equals starting 500 minus 400 cost plus 200 released
    # collateral plus 100 positive-PnL bonus credit, i.e. 400.
    assert result.new_cash_balance == Decimal("400.00")
    # Final fund balance equals 1400 plus the 300 of released fund collateral.
    assert result.new_fund_balance == Decimal("1700.00")
    assert result.position_after is not None
    assert result.position_after.shares == 5
    assert result.position_after.locked_cash == Decimal("200.00")
    assert result.position_after.locked_fund == Decimal("300.00")
    assert result.old_price == Decimal("80.00")
    # Cover routes the trade through ``apply_trade_impact(is_buy=False)``, so
    # the price moves DOWN by ``k * shares / 100`` — same direction as sell.
    assert result.new_price < result.old_price


async def test_cover_with_negative_pnl_does_not_credit_extra_cash(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Cover at a price ABOVE entry — P&L is negative, no cash credit on top.

    The released collateral still returns proportionally (loss is absorbed by
    the collateral itself, not double-charged to cash).
    """
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("2000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_fund_repo.upsert(GUILD, _fund(COVERER, cash=Decimal("1400.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("120.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.cover(COVERER, TARGET, 10)

    assert result.cost == Decimal("1200.00")
    assert result.pnl == Decimal("-200.00")  # (100 - 120) * 10
    # cash: 2000 - 1200 + 400 = 1200 (no extra credit for negative P&L)
    assert result.new_cash_balance == Decimal("1200.00")
    # all locked released — fund: 1400 + 600 = 2000
    assert result.new_fund_balance == Decimal("2000.00")


# ---------------------------------------------------------------------------
# C5 — InsufficientFunds for buy
# ---------------------------------------------------------------------------


async def test_buy_raises_insufficient_funds_when_cash_too_low(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C5: buyer with $50 cannot buy 1 share at $100."""
    await fake_user_repo.upsert(GUILD, _account(BUYER, cash=Decimal("50.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(InsufficientFunds):
        await service.buy(BUYER, TARGET, 1)


async def test_short_raises_insufficient_funds_when_total_collateral_too_low(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Short rejects when cash + 50% fund < notional."""
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("100.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("100.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(InsufficientFunds):
        # notional = 1000; cash 100 + 0.5*fund 100 = 150 < 1000.
        await service.short(SHORTER, TARGET, 10)


async def test_cover_raises_insufficient_funds_when_cash_below_cost(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Cover rejects when the coverer's cash does not cover ``shares * price``."""
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("100.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(InsufficientFunds):
        # cost = 10 * 100 = 1000; coverer has only 100.
        await service.cover(COVERER, TARGET, 10)


# ---------------------------------------------------------------------------
# C6 — OptedOut blocks open-position directions (buy / sell / short).
#       Cover is intentionally exempt — see the cover-when-opted-out test.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["buy", "sell", "short"])
async def test_op_raises_opted_out_when_target_opt_in_is_false(
    op: str,
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C6: a target with opt_in=False is untradable in open-position directions.

    Cover is exempt by design: a holder may have opened the short *before*
    the target opted out; blocking cover would trap the holder with no exit.
    The cover exemption is pinned by
    :func:`test_cover_succeeds_when_target_opted_out`.
    """
    actor_id = {"buy": BUYER, "sell": SELLER, "short": SHORTER}[op]
    # Seed actor with a long for sell so we get past the position-existence
    # check to the opt-in gate.
    long_for_sell = LongPosition(
        target_user_id=TARGET, shares=10, avg_entry=Decimal("80.00")
    )
    longs = {TARGET: long_for_sell} if op == "sell" else {}
    await fake_user_repo.upsert(
        GUILD,
        _account(
            actor_id,
            cash=Decimal("5000.00"),
            long_positions=longs,
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET, opt_in=False))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    await fake_fund_repo.upsert(GUILD, _fund(actor_id, cash=Decimal("2000.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(OptedOut):
        await getattr(service, op)(actor_id, TARGET, 1)


async def test_cover_succeeds_when_target_opted_out(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Issue #84 M — cover must succeed even when the target opted out post-short.

    A short can outlive the target's consent: a holder shorts ``TARGET``,
    later ``TARGET`` opts out, and the holder still needs a way to close the
    position. If cover enforced the opt-in gate (as buy/sell/short do), the
    holder would be trapped with no exit and the locked collateral would
    rot in their account.

    Invariant pinned: the cover use case skips ``_check_opt_in`` so the
    holder can always close a pre-existing short. Open-position directions
    (buy/sell/short) keep the opt-out guard via the parametrised C6 test.
    """
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    # Target has opted out *after* the short was opened.
    await fake_user_repo.upsert(GUILD, _account(TARGET, opt_in=False))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    await fake_fund_repo.upsert(GUILD, _fund(COVERER, cash=Decimal("1400.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.cover(COVERER, TARGET, 5)

    # Cover succeeds: 5 shares closed, collateral released proportionally.
    assert result.shares == 5
    assert result.released_cash == Decimal("200.00")
    assert result.released_fund == Decimal("300.00")
    assert result.position_after is not None
    assert result.position_after.shares == 5


# ---------------------------------------------------------------------------
# C7 — MarketClosed (with Sunday-buy exception)
# ---------------------------------------------------------------------------


async def test_sell_raises_market_closed_outside_hours(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C7: a sell during the closed 04:30..06:30 window raises ``MarketClosed``."""
    await fake_user_repo.upsert(
        GUILD,
        _account(
            SELLER,
            long_positions={
                TARGET: LongPosition(
                    target_user_id=TARGET, shares=5, avg_entry=Decimal("80.00")
                )
            },
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_CLOSED), pytest.raises(MarketClosed):
        await service.sell(SELLER, TARGET, 1)


async def test_sell_raises_market_closed_on_sunday(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C7: sell is blocked on Sunday even within the time-of-day window."""
    await fake_user_repo.upsert(
        GUILD,
        _account(
            SELLER,
            long_positions={
                TARGET: LongPosition(
                    target_user_id=TARGET, shares=5, avg_entry=Decimal("80.00")
                )
            },
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(SUNDAY_OPEN), pytest.raises(MarketClosed):
        await service.sell(SELLER, TARGET, 1)


async def test_buy_allowed_on_sunday(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C7: the Sunday-buy exception lets a buy succeed when sell is rejected."""
    await fake_user_repo.upsert(GUILD, _account(BUYER, cash=Decimal("5000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(SUNDAY_OPEN):
        result = await service.buy(BUYER, TARGET, 5)
    assert result.shares == 5


async def test_short_raises_market_closed_on_sunday(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """The Sunday exception is buy-only — short still rejects on Sunday."""
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("5000.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("2000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(SUNDAY_OPEN), pytest.raises(MarketClosed):
        await service.short(SHORTER, TARGET, 1)


# ---------------------------------------------------------------------------
# C8 — SelfTrade for every operation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["buy", "sell", "short", "cover"])
async def test_op_raises_self_trade_when_actor_equals_target(
    op: str,
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C8: actor == target is rejected before any state mutation."""
    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(SelfTrade):
        await getattr(service, op)("u1", "u1", 1)


# ---------------------------------------------------------------------------
# C9 — OnCooldown gates short/cover only
# ---------------------------------------------------------------------------


async def test_short_raises_on_cooldown_when_active(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C9: an active cooldown for the actor blocks a new short."""
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("5000.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("2000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    # Pre-existing cooldown — 30 s remaining.
    await fake_cooldown_repo.upsert(
        TradeCooldown(
            guild_id=GUILD,
            user_id=SHORTER,
            expires_at=WEEKDAY_OPEN + timedelta(seconds=30),
        )
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(OnCooldown):
        await service.short(SHORTER, TARGET, 1)


async def test_cover_raises_on_cooldown_when_active(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C9: an active cooldown for the actor blocks a new cover."""
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    await fake_cooldown_repo.upsert(
        TradeCooldown(
            guild_id=GUILD,
            user_id=COVERER,
            expires_at=WEEKDAY_OPEN + timedelta(seconds=30),
        )
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(OnCooldown):
        await service.cover(COVERER, TARGET, 1)


async def test_buy_and_sell_are_not_cooldown_gated(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C9: an active short/cover cooldown does NOT block a buy or a sell."""
    await fake_user_repo.upsert(
        GUILD,
        _account(
            BUYER,
            cash=Decimal("5000.00"),
            long_positions={
                TARGET: LongPosition(
                    target_user_id=TARGET, shares=5, avg_entry=Decimal("80.00")
                )
            },
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    await fake_cooldown_repo.upsert(
        TradeCooldown(
            guild_id=GUILD,
            user_id=BUYER,
            expires_at=WEEKDAY_OPEN + timedelta(seconds=600),
        )
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.buy(BUYER, TARGET, 1)
        await service.sell(BUYER, TARGET, 1)


async def test_short_sets_cooldown_after_success(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A successful short writes a cooldown TTL row that gates the next call."""
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("5000.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("2000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.short(SHORTER, TARGET, 1)
        with pytest.raises(OnCooldown):
            await service.short(SHORTER, TARGET, 1)


# ---------------------------------------------------------------------------
# C10 — PositionFrozen blocks the public cover
# ---------------------------------------------------------------------------


async def test_cover_raises_position_frozen_for_frozen_short(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C10: the public :meth:`cover` always rejects a frozen position."""
    frozen_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(hours=1),
        frozen=True,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: frozen_short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(PositionFrozen):
        await service.cover(COVERER, TARGET, 1)


# ---------------------------------------------------------------------------
# #82 M1 — cover_forced public wrapper (the liquidation entry point)
# ---------------------------------------------------------------------------


async def test_cover_forced_covers_frozen_short_with_lock_held_by_caller(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """#82 M1: ``cover_forced`` is the public wrapper around the inside-lock
    body shared with :meth:`cover`. Liquidation now invokes this method
    rather than the private :meth:`_cover_internal`; the lock + UoW
    discipline is the caller's responsibility (LiquidationService takes
    the holder+target lock around the call).

    Pin that ``cover_forced`` succeeds against a FROZEN short — the public
    :meth:`cover` would raise :class:`PositionFrozen`.
    """
    frozen_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(hours=1),
        frozen=True,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: frozen_short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    # The contract is that the caller holds the locks; replicate the
    # LiquidationService call site here.
    async with lock_manager.locked(f"{GUILD}:{COVERER}", f"{GUILD}:{TARGET}"):
        with freeze_time(WEEKDAY_OPEN):
            result = await service.cover_forced(COVERER, TARGET, 10)

    assert result.shares == 10
    assert result.coverer_id == COVERER
    assert result.target_id == TARGET
    # The frozen short was force-covered — the holder's short_positions dict
    # no longer carries the target key.
    holder = await fake_user_repo.get(GUILD, COVERER)
    assert holder is not None
    assert TARGET not in holder.short_positions


async def test_short_top_up_rejects_frozen_position(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Adding shares to a frozen short is rejected (original-spec invariant)."""
    frozen_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=WEEKDAY_OPEN - timedelta(hours=1),
        frozen=True,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            SHORTER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: frozen_short},
        ),
    )
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("2000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(PositionFrozen):
        await service.short(SHORTER, TARGET, 1)


# ---------------------------------------------------------------------------
# C12 — weighted-average entry on long-position top-up
# ---------------------------------------------------------------------------


async def test_buy_adds_to_existing_long_with_weighted_average_entry(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C12: existing 10 @ $80 + new 10 @ $100 → 20 @ $90 weighted average."""
    initial_long = LongPosition(
        target_user_id=TARGET, shares=10, avg_entry=Decimal("80.00")
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(BUYER, cash=Decimal("5000.00"), long_positions={TARGET: initial_long}),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.buy(BUYER, TARGET, 10)

    # ((10 * 80) + (10 * 100)) / 20 = 1800 / 20 = 90.00
    assert result.position_after.shares == 20
    assert result.position_after.avg_entry == Decimal("90.00")


async def test_short_adds_to_existing_short_with_weighted_average_entry(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C12: short top-up recomputes the weighted-average entry the same way."""
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("80.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("400.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            SHORTER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("4000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.short(SHORTER, TARGET, 10)

    # ((10 * 80) + (10 * 100)) / 20 = 90.00
    assert result.position_after.shares == 20
    assert result.position_after.entry_price == Decimal("90.00")


# ---------------------------------------------------------------------------
# C13 — position deleted on full close (sell + cover)
# ---------------------------------------------------------------------------


async def test_sell_deletes_long_position_when_fully_closed(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C13: selling all shares deletes the long-position record."""
    initial_long = LongPosition(
        target_user_id=TARGET, shares=5, avg_entry=Decimal("80.00")
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(SELLER, cash=Decimal("0.00"), long_positions={TARGET: initial_long}),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.sell(SELLER, TARGET, 5)

    assert result.position_after is None
    after = await fake_user_repo.get(GUILD, SELLER)
    assert after is not None
    assert TARGET not in after.long_positions


async def test_cover_deletes_short_position_when_fully_closed(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C13: covering every share deletes the short-position record."""
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=5,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("200.00"),
        locked_fund=Decimal("300.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("1000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_fund_repo.upsert(GUILD, _fund(COVERER, cash=Decimal("1000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.cover(COVERER, TARGET, 5)

    assert result.position_after is None
    after = await fake_user_repo.get(GUILD, COVERER)
    assert after is not None
    assert TARGET not in after.short_positions


# ---------------------------------------------------------------------------
# C14 — update_frozen_shorts sweep
# ---------------------------------------------------------------------------


async def test_update_frozen_shorts_freezes_old_positions_only(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C14: shorts past the freeze window flip to frozen; recent ones do not."""
    threshold = default_settings.short_freeze_minutes
    now = WEEKDAY_OPEN
    old_short = ShortPosition(
        target_user_id="t-old",
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=now - timedelta(minutes=threshold + 1),
        frozen=False,
    )
    new_short = ShortPosition(
        target_user_id="t-new",
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=now - timedelta(minutes=1),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            SHORTER,
            cash=Decimal("5000.00"),
            short_positions={"t-old": old_short, "t-new": new_short},
        ),
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(now):
        await service.update_frozen_shorts()

    after = await fake_user_repo.get(GUILD, SHORTER)
    assert after is not None
    assert after.short_positions["t-old"].frozen is True
    assert after.short_positions["t-new"].frozen is False


async def test_update_frozen_shorts_is_idempotent(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Re-running the sweep on already-frozen positions does not double-touch them.

    The store is left identical the second time (a no-change account isn't
    rewritten).
    """
    threshold = default_settings.short_freeze_minutes
    now = WEEKDAY_OPEN
    old_short = ShortPosition(
        target_user_id=TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("400.00"),
        locked_fund=Decimal("600.00"),
        created_at=now - timedelta(minutes=threshold + 1),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            SHORTER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: old_short},
        ),
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(now):
        await service.update_frozen_shorts()
        await service.update_frozen_shorts()

    after = await fake_user_repo.get(GUILD, SHORTER)
    assert after is not None
    assert after.short_positions[TARGET].frozen is True


# ---------------------------------------------------------------------------
# Boundary / validation edge cases (for >=90% coverage)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["buy", "sell", "short", "cover"])
async def test_op_rejects_non_positive_shares(
    op: str,
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Zero or negative shares raise :class:`InvalidAmount` for every op."""
    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(InvalidAmount):
        await getattr(service, op)("u1", "u2", 0)


async def test_sell_raises_no_position_when_seller_has_none(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Sell on a target the seller never traded raises ``InsufficientShares``."""
    await fake_user_repo.upsert(GUILD, _account(SELLER))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(InsufficientShares):
        await service.sell(SELLER, TARGET, 1)


async def test_cover_raises_no_position_when_coverer_has_none(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Cover on a target the coverer never shorted raises :class:`NoPosition`."""
    await fake_user_repo.upsert(GUILD, _account(COVERER, cash=Decimal("1000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(NoPosition):
        await service.cover(COVERER, TARGET, 1)


async def test_cover_raises_insufficient_shares_when_holding_less(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Cover for more shares than held raises :class:`InsufficientShares`."""
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=2,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("100.00"),
        locked_fund=Decimal("100.00"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("5000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(InsufficientShares):
        await service.cover(COVERER, TARGET, 5)


async def test_short_uses_initial_price_when_target_has_no_stock_row(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A first-time-traded target gets an ``initial_price`` stock row implicitly."""
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("5000.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("2000.00")))
    # NOTE: no target user nor stock row pre-seeded.

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.short(SHORTER, TARGET, 1)
    initial = Decimal(str(default_settings.initial_price))
    assert result.price_per_share == initial


async def test_market_closed_uses_settings_window(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A custom ``market_open``/``close`` window controls the gate (no Sunday)."""
    custom = default_settings.model_copy(
        update={"market_open": time(9, 0), "market_close": time(17, 0)}
    )
    # 18:00 UTC on a weekday — outside [09:00..17:00) → closed.
    closed_dt = datetime(2026, 5, 25, 18, 0, tzinfo=UTC)
    await fake_user_repo.upsert(GUILD, _account(BUYER, cash=Decimal("5000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=custom,
    )

    with freeze_time(closed_dt), pytest.raises(MarketClosed):
        await service.buy(BUYER, TARGET, 1)


# ---------------------------------------------------------------------------
# Phase 17a — Open-Q2/Q3 toggles wired into the trading service
# ---------------------------------------------------------------------------


async def test_buy_rejected_on_sunday_when_sunday_buy_allowed_is_false(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Open-Q2: with ``sunday_buy_allowed=False`` /buy is closed on Sunday too."""
    custom = default_settings.model_copy(update={"sunday_buy_allowed": False})
    await fake_user_repo.upsert(GUILD, _account(BUYER, cash=Decimal("5000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=custom,
    )

    with freeze_time(SUNDAY_OPEN), pytest.raises(MarketClosed):
        await service.buy(BUYER, TARGET, 1)


async def test_check_opt_in_is_noop_when_opt_out_blocks_trading_is_false(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Open-Q3: ``opt_out_blocks_trading=False`` disarms ``_check_opt_in``."""
    custom = default_settings.model_copy(update={"opt_out_blocks_trading": False})
    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=custom,
    )
    opted_out_target = _account(TARGET, opt_in=False)

    # Must NOT raise — the toggle turns the opt-in gate into a no-op.
    service._check_opt_in(opted_out_target)


async def test_check_opt_in_still_raises_when_toggle_default_true(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Default ``opt_out_blocks_trading=True`` keeps the historic ``OptedOut`` raise."""
    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )
    opted_out_target = _account(TARGET, opt_in=False)

    with pytest.raises(OptedOut):
        service._check_opt_in(opted_out_target)


# ---------------------------------------------------------------------------
# Issue #84 M (silent-failures branch): double-`get` stub-overwrite pattern
#
# The pre-fix code called ``repo.get(target_id)`` once via
# ``_get_or_create_user``, *then* called ``repo.get(target_id) is None`` again
# at upsert time before persisting the stub. The locking discipline made the
# race benign in practice (both calls run inside the per-target lock), but the
# extra read is redundant and the pattern *looks* like a TOCTOU window. The
# fix collapses the check into a single call using a ``(account, created)``
# tuple and persists the stub once if needed — and ONLY then.
#
# These tests pin the externally observable contracts:
#   1. A trade against a never-seen target persists the target stub (so the
#      next trade sees the opt-in flag).
#   2. The number of ``IUserRepo.get(target)`` calls inside the lock is at
#      most 1 — the second redundant call is gone.


class _CountingUserRepo:
    """Wrap a :class:`FakeUserRepo` and tally per-target ``get`` calls.

    Only the methods :class:`TradingService` uses are forwarded; anything
    else surfaces as :class:`AttributeError`. ``get`` keeps a per-user
    counter so tests can assert the redundant-second-get is gone (issue
    #84 M, silent-failures branch).
    """

    def __init__(self, inner: FakeUserRepo) -> None:
        self._inner = inner
        self.get_counts: dict[str, int] = {}

    async def get(self, guild_id: str, user_id: str) -> UserAccount | None:
        self.get_counts[user_id] = self.get_counts.get(user_id, 0) + 1
        return await self._inner.get(guild_id, user_id)

    async def upsert(self, guild_id: str, account: UserAccount) -> None:
        await self._inner.upsert(guild_id, account)

    async def delete(self, guild_id: str, user_id: str) -> None:
        await self._inner.delete(guild_id, user_id)

    async def list_all(self, guild_id: str) -> Sequence[UserAccount]:
        return await self._inner.list_all(guild_id)

    async def list_active_in_last(
        self, guild_id: str, seconds: float
    ) -> Sequence[UserAccount]:
        return await self._inner.list_active_in_last(guild_id, seconds)


async def test_buy_persists_target_stub_when_target_never_seen(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """First buy against a never-seen target stores the target stub."""
    await fake_user_repo.upsert(GUILD, _account(BUYER, cash=Decimal("5000.00")))
    # TARGET deliberately NOT pre-upserted — the trade must seed the stub.
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.buy(BUYER, TARGET, 1)

    # The target stub MUST be persisted so a subsequent trade sees its
    # opt-in flag (the "sticky" docstring contract preserved from pre-fix).
    target = await fake_user_repo.get(GUILD, TARGET)
    assert target is not None
    assert target.opt_in is True
    assert target.cash_balance == Decimal("10000.00")


async def test_buy_does_not_re_get_target_after_initial_resolution(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A buy issues at most ONE ``get(target)`` inside the lock (no redundant 2nd).

    Pre-fix the code called ``get(target)`` twice — once via
    ``_get_or_create_user`` and once at the stub-persist site. The fix folds
    those into a single resolution. The counter is on a wrapping repo so
    behaviour against the real ``FakeUserRepo`` is unchanged.
    """
    await fake_user_repo.upsert(GUILD, _account(BUYER, cash=Decimal("5000.00")))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    counting = _CountingUserRepo(fake_user_repo)

    service = _make_service(
        user_repo=counting,  # type: ignore[arg-type]
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.buy(BUYER, TARGET, 1)

    assert counting.get_counts.get(TARGET, 0) == 1


async def test_sell_does_not_re_get_target_after_initial_resolution(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Sell issues at most ONE ``get(target)`` inside the lock."""
    seller = _account(
        SELLER,
        cash=Decimal("1000.00"),
        long_positions={
            TARGET: LongPosition(
                target_user_id=TARGET, shares=10, avg_entry=Decimal("80.00")
            )
        },
    )
    await fake_user_repo.upsert(GUILD, seller)
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    counting = _CountingUserRepo(fake_user_repo)

    service = _make_service(
        user_repo=counting,  # type: ignore[arg-type]
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.sell(SELLER, TARGET, 1)

    assert counting.get_counts.get(TARGET, 0) == 1


async def test_short_does_not_re_get_target_after_initial_resolution(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Short issues at most ONE ``get(target)`` inside the lock."""
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("5000.00")))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    counting = _CountingUserRepo(fake_user_repo)

    service = _make_service(
        user_repo=counting,  # type: ignore[arg-type]
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.short(SHORTER, TARGET, 1)

    assert counting.get_counts.get(TARGET, 0) == 1


async def test_cover_does_not_re_get_target_after_initial_resolution(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Cover issues at most ONE ``get(target)`` inside the lock."""
    coverer = _account(
        COVERER,
        cash=Decimal("5000.00"),
        short_positions={
            TARGET: ShortPosition(
                target_user_id=TARGET,
                shares=5,
                entry_price=Decimal("120.00"),
                locked_cash=Decimal("600.00"),
                locked_fund=Decimal("0.00"),
                created_at=WEEKDAY_OPEN - timedelta(minutes=10),
                frozen=False,
            )
        },
    )
    await fake_user_repo.upsert(GUILD, coverer)
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))
    counting = _CountingUserRepo(fake_user_repo)

    service = _make_service(
        user_repo=counting,  # type: ignore[arg-type]
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.cover(COVERER, TARGET, 1)

    assert counting.get_counts.get(TARGET, 0) == 1
