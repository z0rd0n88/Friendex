"""Behavioural tests for :class:`PortfolioService` (Phase 8d).

The service is a thin orchestrator over
:func:`friendex.domain.fund_math.compute_net_worth` and the persistence ports.
It is **read-only** for ``calculate_net_worth`` and ``portfolio_snapshot`` (no
locks, no writes); ``capture_month_start_net_worth`` is the one write path —
a monthly sweep that, per the work-unit baton, takes a per-user
:meth:`LockManager.locked` lock so a concurrent trade or tick that touches
the same :class:`UserAccount` cannot race the snapshot ``upsert``.

Acceptance criteria pinned here:

* **D1** — ``calculate_net_worth`` with long-only positions returns
  ``cash + sum(shares * current_price)``.
* **D2** — ``calculate_net_worth`` with short-only positions returns
  ``cash + sum(locked_cash + locked_fund - shares * current_price)``.
* **D3** — ``calculate_net_worth`` with mixed long + short positions returns
  the correct combined value (asserted with concrete numbers).
* **D4** — ``calculate_net_worth`` with frozen-only shorts still includes
  them correctly (``frozen=True`` does not exclude a position from net worth).
* **D5** — ``capture_month_start_net_worth`` rolls over a snapshot for every
  account in the guild (assert the snapshot was written for the expected set
  of users and that both ``net_worth`` and ``month_start_net_worth`` were
  updated).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.portfolio_service import PortfolioService
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
    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import (
        FakeFundRepo,
        FakePriceRepo,
        FakeUserRepo,
    )


GUILD = "100000000000000001"
ACTOR = "actor-1"
TARGET_A = "target-a"
TARGET_B = "target-b"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _account(
    user_id: str,
    *,
    cash: Decimal = Decimal("1000.00"),
    long_positions: dict[str, LongPosition] | None = None,
    short_positions: dict[str, ShortPosition] | None = None,
    net_worth: Decimal | None = None,
    month_start_net_worth: Decimal | None = None,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the portfolio tests."""
    now = datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=net_worth if net_worth is not None else cash,
        month_start_net_worth=(
            month_start_net_worth if month_start_net_worth is not None else cash
        ),
        long_positions=long_positions or {},
        short_positions=short_positions or {},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
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


def _long(
    target: str, *, shares: int, avg_entry: Decimal = Decimal("100.00")
) -> LongPosition:
    return LongPosition(target_user_id=target, shares=shares, avg_entry=avg_entry)


def _short(
    target: str,
    *,
    shares: int,
    entry_price: Decimal,
    locked_cash: Decimal,
    locked_fund: Decimal,
    frozen: bool = False,
) -> ShortPosition:
    return ShortPosition(
        target_user_id=target,
        shares=shares,
        entry_price=entry_price,
        locked_cash=locked_cash,
        locked_fund=locked_fund,
        created_at=datetime.now(tz=UTC),
        frozen=frozen,
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    settings: Settings,
) -> PortfolioService:
    """Construct the service under test with explicit dependencies."""
    return PortfolioService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        lock_manager=lock_manager,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# D1 — long-only net worth
# ---------------------------------------------------------------------------


async def test_calculate_net_worth_long_only(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """D1: 500 cash + 5 shares @ 100 + 10 shares @ 50 → 500 + 500 + 500 = 1500."""
    longs = {
        TARGET_A: _long(TARGET_A, shares=5, avg_entry=Decimal("80.00")),
        TARGET_B: _long(TARGET_B, shares=10, avg_entry=Decimal("60.00")),
    }
    await fake_user_repo.upsert(
        GUILD, _account(ACTOR, cash=Decimal("500.00"), long_positions=longs)
    )
    await fake_price_repo.upsert(GUILD, _stock(TARGET_A, current=Decimal("100.00")))
    await fake_price_repo.upsert(GUILD, _stock(TARGET_B, current=Decimal("50.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    net_worth = await service.calculate_net_worth(ACTOR)

    assert net_worth == Decimal("1500.00")


# ---------------------------------------------------------------------------
# D2 — short-only net worth
# ---------------------------------------------------------------------------


async def test_calculate_net_worth_short_only(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """D2: cash 1000 + short(10@100, lock 600+400) at mark 90 → 1000+1000-900=1100."""
    shorts = {
        TARGET_A: _short(
            TARGET_A,
            shares=10,
            entry_price=Decimal("100.00"),
            locked_cash=Decimal("600.00"),
            locked_fund=Decimal("400.00"),
        ),
    }
    await fake_user_repo.upsert(
        GUILD, _account(ACTOR, cash=Decimal("1000.00"), short_positions=shorts)
    )
    await fake_price_repo.upsert(GUILD, _stock(TARGET_A, current=Decimal("90.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    net_worth = await service.calculate_net_worth(ACTOR)

    # collateral 1000 - buyback 10*90=900 = 100; plus cash 1000 → 1100
    assert net_worth == Decimal("1100.00")


# ---------------------------------------------------------------------------
# D3 — mixed long + short net worth (concrete numbers)
# ---------------------------------------------------------------------------


async def test_calculate_net_worth_mixed_long_and_short(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """D3: cash 500 + long 5@200=1000 + short(10@100, 1000 lock, mark 90)=100 → 1600."""
    longs = {
        TARGET_A: _long(TARGET_A, shares=5, avg_entry=Decimal("180.00")),
    }
    shorts = {
        TARGET_B: _short(
            TARGET_B,
            shares=10,
            entry_price=Decimal("100.00"),
            locked_cash=Decimal("600.00"),
            locked_fund=Decimal("400.00"),
        ),
    }
    await fake_user_repo.upsert(
        GUILD,
        _account(
            ACTOR,
            cash=Decimal("500.00"),
            long_positions=longs,
            short_positions=shorts,
        ),
    )
    await fake_price_repo.upsert(GUILD, _stock(TARGET_A, current=Decimal("200.00")))
    await fake_price_repo.upsert(GUILD, _stock(TARGET_B, current=Decimal("90.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    net_worth = await service.calculate_net_worth(ACTOR)

    # cash 500 + long 5*200=1000 + short collateral 1000 - buyback 10*90=900 → 1600
    assert net_worth == Decimal("1600.00")


# ---------------------------------------------------------------------------
# D4 — frozen-only shorts still contribute to net worth
# ---------------------------------------------------------------------------


async def test_calculate_net_worth_frozen_shorts_still_counted(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """D4: a frozen short is valued identically to an unfrozen one."""
    shorts = {
        TARGET_A: _short(
            TARGET_A,
            shares=10,
            entry_price=Decimal("100.00"),
            locked_cash=Decimal("700.00"),
            locked_fund=Decimal("300.00"),
            frozen=True,
        ),
    }
    await fake_user_repo.upsert(
        GUILD, _account(ACTOR, cash=Decimal("200.00"), short_positions=shorts)
    )
    await fake_price_repo.upsert(GUILD, _stock(TARGET_A, current=Decimal("80.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    net_worth = await service.calculate_net_worth(ACTOR)

    # cash 200 + collateral 1000 - buyback 10*80=800 → 400
    assert net_worth == Decimal("400.00")


# ---------------------------------------------------------------------------
# D5 — capture_month_start_net_worth rolls over for every user
# ---------------------------------------------------------------------------


async def test_capture_month_start_writes_snapshot_for_each_user(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """D5: every account in the guild gets net_worth + month_start_net_worth written."""
    # Three users: ACTOR (long-only), TARGET_A (cash only), TARGET_B (short-only).
    await fake_user_repo.upsert(
        GUILD,
        _account(
            ACTOR,
            cash=Decimal("500.00"),
            long_positions={
                TARGET_A: _long(TARGET_A, shares=5, avg_entry=Decimal("80.00")),
            },
            month_start_net_worth=Decimal("0.00"),
        ),
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            TARGET_A,
            cash=Decimal("2000.00"),
            month_start_net_worth=Decimal("0.00"),
        ),
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            TARGET_B,
            cash=Decimal("100.00"),
            short_positions={
                ACTOR: _short(
                    ACTOR,
                    shares=2,
                    entry_price=Decimal("100.00"),
                    locked_cash=Decimal("150.00"),
                    locked_fund=Decimal("50.00"),
                ),
            },
            month_start_net_worth=Decimal("0.00"),
        ),
    )
    await fake_price_repo.upsert(GUILD, _stock(TARGET_A, current=Decimal("100.00")))
    await fake_price_repo.upsert(GUILD, _stock(ACTOR, current=Decimal("90.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.capture_month_start_net_worth()

    # ACTOR: cash 500 + 5*100 = 1000
    after_actor = await fake_user_repo.get(GUILD, ACTOR)
    assert after_actor is not None
    assert after_actor.net_worth == Decimal("1000.00")
    assert after_actor.month_start_net_worth == Decimal("1000.00")

    # TARGET_A: 2000 cash, no positions → 2000
    after_a = await fake_user_repo.get(GUILD, TARGET_A)
    assert after_a is not None
    assert after_a.net_worth == Decimal("2000.00")
    assert after_a.month_start_net_worth == Decimal("2000.00")

    # TARGET_B: cash 100 + collateral 200 - 2*90=180 → 120
    after_b = await fake_user_repo.get(GUILD, TARGET_B)
    assert after_b is not None
    assert after_b.net_worth == Decimal("120.00")
    assert after_b.month_start_net_worth == Decimal("120.00")

    # And the set of users touched is exactly the three above.
    all_accounts = await fake_user_repo.list_all(GUILD)
    assert {a.user_id for a in all_accounts} == {ACTOR, TARGET_A, TARGET_B}


# ---------------------------------------------------------------------------
# PR #94 review M2 — capture_month_start does not crash on an underwater holder
# ---------------------------------------------------------------------------


async def test_capture_month_start_handles_deeply_underwater_short(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A holder whose shorts are underwater MUST roll over cleanly.

    Pre-fix (Wave 2 silent-failures branch) the dataclass invariant
    ``net_worth >= 0`` raised inside ``replace()`` mid-rollover when the
    holder's short term ``shares * (entry - current)`` drove the measurement
    negative — exactly the silent-failure class the PR is fighting. The fix
    (PR #94 review M2) relaxes the invariant on ``net_worth`` and
    ``month_start_net_worth`` while keeping ``cash_balance`` strict; this
    test pins that the rollover now writes the negative snapshot through
    without raising.

    Scenario: ACTOR shorts 10 shares of TARGET at entry $100 (collateral
    $1000), but TARGET rallies to $250 between liquidation sweeps. The
    buyback cost is $2500, so the short contributes $1000 - $2500 = -$1500
    to net worth. ACTOR's cash is $0, no longs — net_worth = -1500.
    """
    await fake_user_repo.upsert(
        GUILD,
        _account(
            ACTOR,
            cash=Decimal("0.00"),
            short_positions={
                TARGET_A: _short(
                    TARGET_A,
                    shares=10,
                    entry_price=Decimal("100.00"),
                    locked_cash=Decimal("750.00"),
                    locked_fund=Decimal("250.00"),
                ),
            },
            month_start_net_worth=Decimal("10000.00"),
        ),
    )
    # TARGET_A's price rallied to $250 — short is deeply underwater.
    await fake_price_repo.upsert(GUILD, _stock(TARGET_A, current=Decimal("250.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    # MUST NOT raise: pre-fix this crashed inside replace() because the
    # dataclass __post_init__ rejected the negative net_worth.
    await service.capture_month_start_net_worth()

    after = await fake_user_repo.get(GUILD, ACTOR)
    assert after is not None
    # cash 0 + collateral 1000 - 10*250 = -1500
    assert after.net_worth == Decimal("-1500.00")
    assert after.month_start_net_worth == Decimal("-1500.00")


# ---------------------------------------------------------------------------
# Portfolio snapshot bonus — sanity that the read-model wires together.
# ---------------------------------------------------------------------------


async def test_portfolio_snapshot_includes_fund_balance(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Smoke: portfolio_snapshot pulls the user's personal hedge-fund balance."""
    await fake_user_repo.upsert(GUILD, _account(ACTOR, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(
        GUILD,
        HedgeFund(
            fund_id=ACTOR,
            name="actor-fund",
            manager_id=ACTOR,
            cash_balance=Decimal("250.00"),
            investors={ACTOR: Decimal("250.00")},
        ),
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    snapshot = await service.portfolio_snapshot(ACTOR)

    assert snapshot is not None
    assert snapshot.user_id == ACTOR
    assert snapshot.cash_balance == Decimal("500.00")
    assert snapshot.fund_balance == Decimal("250.00")
    # net_worth = cash 500 + fund stake 250 → 750
    assert snapshot.net_worth == Decimal("750.00")
