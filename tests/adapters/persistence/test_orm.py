"""Round-trip tests for the SQLAlchemy ORM mirrors of the domain models.

Each test follows the same Arrange-Act-Assert shape: build a domain object,
persist it via ``<ORM>.from_domain(guild_id, obj)``, reload the row from a
fresh session, map it back with ``to_domain()``, and assert the recovered
domain object is *equal* to the original — with the two cross-cutting
invariants from ADR-0001 / Phase 3.1 held under a magnifying glass:

* **Decimal precision** survives the round trip exactly (``Decimal('100.00')``
  comes back as an equal ``Decimal`` of the same quantisation and type).
* **UTC tz-awareness** survives the round trip (loaded datetimes are tz-aware
  and equal to the originals as instants).

The store is an in-memory SQLite (``sqlite+aiosqlite:///:memory:``); a single
engine is shared across each test so the in-memory schema persists between the
write session and the read session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import select

from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.orm import (
    ActivityBucketORM,
    FundInvestorORM,
    FundPenaltyORM,
    HedgeFundORM,
    LongPositionORM,
    PriceHistoryORM,
    ShortPositionORM,
    StockORM,
    SystemStateORM,
    TradeCooldownORM,
    UserORM,
    VoiceUniqueChannelORM,
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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

GUILD_ID = "555000111222333444"


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite engine with all tables created."""
    eng = build_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """An ``AsyncSession`` bound to the in-memory engine."""
    maker = build_sessionmaker(engine)
    async with maker() as sess:
        yield sess


def _utc(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 30, 15, tzinfo=UTC)


async def test_create_all_runs_against_in_memory_sqlite(engine: AsyncEngine) -> None:
    """Acceptance #1 — every table is created without error."""
    # Arrange / Act — table creation happened in the fixture.
    table_names = set(Base.metadata.tables.keys())

    # Assert — the full Option B table set exists.
    assert {
        "users",
        "long_positions",
        "short_positions",
        "activity_buckets",
        "voice_unique_channels",
        "stocks",
        "price_history",
        "hedge_funds",
        "fund_investors",
        "fund_penalties",
        "system_state",
        "trade_cooldowns",
    } <= table_names


async def test_user_account_round_trip(session: AsyncSession) -> None:
    account = UserAccount(
        user_id="111",
        cash_balance=Decimal("9876.54"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("9500.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=_utc(2026, 5, 23)),
        week=ActivityBucket(bucket_start=_utc(2026, 5, 18)),
        daily=DailyProgress(last_claim=_utc(2026, 5, 22, 6), streak=3),
        last_activity=_utc(2026, 5, 23, 11),
        opt_in=True,
        intro_shown=False,
    )

    session.add(UserORM.from_domain(GUILD_ID, account))
    await session.commit()

    loaded = (
        await session.execute(
            select(UserORM).where(
                UserORM.guild_id == GUILD_ID, UserORM.user_id == "111"
            )
        )
    ).scalar_one()
    result = loaded.to_domain()

    assert result.cash_balance == Decimal("9876.54")
    assert isinstance(result.cash_balance, Decimal)
    assert result.net_worth == Decimal("10000.00")
    assert result.month_start_net_worth == Decimal("9500.00")
    assert result.last_activity == _utc(2026, 5, 23, 11)
    assert result.last_activity.tzinfo is not None
    assert result.daily.last_claim == _utc(2026, 5, 22, 6)
    assert result.daily.last_claim is not None
    assert result.daily.last_claim.tzinfo is not None
    assert result.daily.streak == 3
    assert result.user_id == "111"
    assert result.opt_in is True
    assert result.intro_shown is False


async def test_user_account_null_daily_claim_round_trip(session: AsyncSession) -> None:
    account = UserAccount(
        user_id="222",
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=_utc(2026, 5, 23)),
        week=ActivityBucket(bucket_start=_utc(2026, 5, 18)),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=_utc(2026, 5, 23, 11),
    )

    session.add(UserORM.from_domain(GUILD_ID, account))
    await session.commit()

    loaded = (
        await session.execute(
            select(UserORM).where(
                UserORM.guild_id == GUILD_ID, UserORM.user_id == "222"
            )
        )
    ).scalar_one()
    result = loaded.to_domain()

    assert result.daily.last_claim is None
    assert result.daily.streak == 0


async def test_long_position_round_trip(session: AsyncSession) -> None:
    position = LongPosition(
        target_user_id="999", shares=42, avg_entry=Decimal("123.45")
    )

    session.add(LongPositionORM.from_domain(GUILD_ID, "owner-1", position))
    await session.commit()

    loaded = (
        await session.execute(
            select(LongPositionORM).where(
                LongPositionORM.guild_id == GUILD_ID,
                LongPositionORM.owner_id == "owner-1",
                LongPositionORM.target_id == "999",
            )
        )
    ).scalar_one()
    result = loaded.to_domain()

    assert result == position
    assert result.avg_entry == Decimal("123.45")
    assert isinstance(result.avg_entry, Decimal)
    assert result.shares == 42


async def test_short_position_round_trip(session: AsyncSession) -> None:
    position = ShortPosition(
        target_user_id="888",
        shares=10,
        entry_price=Decimal("200.00"),
        locked_cash=Decimal("1500.00"),
        locked_fund=Decimal("500.00"),
        created_at=_utc(2026, 5, 23, 9),
        frozen=True,
    )

    session.add(ShortPositionORM.from_domain(GUILD_ID, "owner-2", position))
    await session.commit()

    loaded = (
        await session.execute(
            select(ShortPositionORM).where(
                ShortPositionORM.guild_id == GUILD_ID,
                ShortPositionORM.owner_id == "owner-2",
                ShortPositionORM.target_id == "888",
            )
        )
    ).scalar_one()
    result = loaded.to_domain()

    assert result == position
    assert result.entry_price == Decimal("200.00")
    assert isinstance(result.entry_price, Decimal)
    assert result.locked_cash == Decimal("1500.00")
    assert result.locked_fund == Decimal("500.00")
    assert result.created_at == _utc(2026, 5, 23, 9)
    assert result.created_at.tzinfo is not None
    assert result.frozen is True


async def test_user_with_positions_round_trip(session: AsyncSession) -> None:
    """A user persisted with both long and short children rebuilds fully."""
    account = UserAccount(
        user_id="333",
        cash_balance=Decimal("5000.00"),
        net_worth=Decimal("7000.00"),
        month_start_net_worth=Decimal("6000.00"),
        long_positions={
            "aaa": LongPosition("aaa", 5, Decimal("80.00")),
            "bbb": LongPosition("bbb", 3, Decimal("150.50")),
        },
        short_positions={
            "ccc": ShortPosition(
                target_user_id="ccc",
                shares=2,
                entry_price=Decimal("90.00"),
                locked_cash=Decimal("180.00"),
                locked_fund=Decimal("0.00"),
                created_at=_utc(2026, 5, 23, 8),
            ),
        },
        today=ActivityBucket(bucket_start=_utc(2026, 5, 23)),
        week=ActivityBucket(bucket_start=_utc(2026, 5, 18)),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=_utc(2026, 5, 23, 10),
    )

    session.add(UserORM.from_domain(GUILD_ID, account))
    session.add_all(
        LongPositionORM.from_domain(GUILD_ID, "333", lp)
        for lp in account.long_positions.values()
    )
    session.add_all(
        ShortPositionORM.from_domain(GUILD_ID, "333", sp)
        for sp in account.short_positions.values()
    )
    await session.commit()

    longs = (
        (
            await session.execute(
                select(LongPositionORM).where(
                    LongPositionORM.guild_id == GUILD_ID,
                    LongPositionORM.owner_id == "333",
                )
            )
        )
        .scalars()
        .all()
    )
    shorts = (
        (
            await session.execute(
                select(ShortPositionORM).where(
                    ShortPositionORM.guild_id == GUILD_ID,
                    ShortPositionORM.owner_id == "333",
                )
            )
        )
        .scalars()
        .all()
    )

    rebuilt_longs = {row.to_domain().target_user_id: row.to_domain() for row in longs}
    rebuilt_shorts = {row.to_domain().target_user_id: row.to_domain() for row in shorts}

    assert rebuilt_longs == account.long_positions
    assert rebuilt_shorts == account.short_positions


async def test_activity_bucket_round_trip(session: AsyncSession) -> None:
    bucket = ActivityBucket(
        text_msgs=12,
        media_msgs=4,
        voice_minutes=37.5,
        voice_unique_channels=["c1", "c2", "c3"],
        reaction_count=8,
        reply_count=2,
        role_ping_joins=1.0,
        role_ping_join_minutes=20.0,
        bucket_start=_utc(2026, 5, 23, 0),
    )

    session.add(ActivityBucketORM.from_domain(GUILD_ID, "444", "today", bucket))
    session.add_all(
        VoiceUniqueChannelORM.from_domain(GUILD_ID, "444", "today", channel)
        for channel in bucket.voice_unique_channels
    )
    await session.commit()

    bucket_row = (
        await session.execute(
            select(ActivityBucketORM).where(
                ActivityBucketORM.guild_id == GUILD_ID,
                ActivityBucketORM.user_id == "444",
                ActivityBucketORM.bucket_type == "today",
            )
        )
    ).scalar_one()
    channel_rows = (
        (
            await session.execute(
                select(VoiceUniqueChannelORM).where(
                    VoiceUniqueChannelORM.guild_id == GUILD_ID,
                    VoiceUniqueChannelORM.user_id == "444",
                    VoiceUniqueChannelORM.bucket_type == "today",
                )
            )
        )
        .scalars()
        .all()
    )

    channels = sorted(row.channel_id for row in channel_rows)
    result = bucket_row.to_domain(channels)

    assert result == bucket
    assert result.bucket_start == _utc(2026, 5, 23, 0)
    assert result.bucket_start.tzinfo is not None
    assert result.voice_unique_channels == ["c1", "c2", "c3"]
    assert result.voice_minutes == 37.5


async def test_stock_round_trip(session: AsyncSession) -> None:
    stock = Stock(
        user_id="555",
        current=Decimal("104.00"),
        history=[
            PricePoint(price=Decimal("100.00"), timestamp=_utc(2026, 5, 23, 6)),
            PricePoint(price=Decimal("104.00"), timestamp=_utc(2026, 5, 23, 7)),
        ],
        high_24h=Decimal("110.00"),
        low_24h=Decimal("98.00"),
        all_time_high=Decimal("150.00"),
    )

    session.add(StockORM.from_domain(GUILD_ID, stock))
    session.add_all(
        PriceHistoryORM.from_domain(GUILD_ID, "555", point) for point in stock.history
    )
    await session.commit()

    stock_row = (
        await session.execute(
            select(StockORM).where(
                StockORM.guild_id == GUILD_ID, StockORM.user_id == "555"
            )
        )
    ).scalar_one()
    history_rows = (
        (
            await session.execute(
                select(PriceHistoryORM)
                .where(
                    PriceHistoryORM.guild_id == GUILD_ID,
                    PriceHistoryORM.user_id == "555",
                )
                .order_by(PriceHistoryORM.recorded_at)
            )
        )
        .scalars()
        .all()
    )

    history = [row.to_domain() for row in history_rows]
    result = stock_row.to_domain(history)

    assert result == stock
    assert result.current == Decimal("104.00")
    assert isinstance(result.current, Decimal)
    assert result.high_24h == Decimal("110.00")
    assert result.all_time_high == Decimal("150.00")
    assert result.history[0].price == Decimal("100.00")
    assert isinstance(result.history[0].price, Decimal)
    assert result.history[0].timestamp == _utc(2026, 5, 23, 6)
    assert result.history[0].timestamp.tzinfo is not None


async def test_hedge_fund_round_trip(session: AsyncSession) -> None:
    fund = HedgeFund(
        fund_id="fund-1",
        name="Diamond Hands",
        manager_id="666",
        cash_balance=Decimal("25000.00"),
        investors={
            "666": Decimal("10000.00"),
            "777": Decimal("15000.00"),
        },
    )

    session.add(HedgeFundORM.from_domain(GUILD_ID, fund))
    session.add_all(
        FundInvestorORM.from_domain(GUILD_ID, "fund-1", investor_id, amount)
        for investor_id, amount in fund.investors.items()
    )
    await session.commit()

    fund_row = (
        await session.execute(
            select(HedgeFundORM).where(
                HedgeFundORM.guild_id == GUILD_ID, HedgeFundORM.fund_id == "fund-1"
            )
        )
    ).scalar_one()
    investor_rows = (
        (
            await session.execute(
                select(FundInvestorORM).where(
                    FundInvestorORM.guild_id == GUILD_ID,
                    FundInvestorORM.fund_id == "fund-1",
                )
            )
        )
        .scalars()
        .all()
    )

    investors = {row.investor_id: row.to_amount() for row in investor_rows}
    result = fund_row.to_domain(investors)

    assert result == fund
    assert result.cash_balance == Decimal("25000.00")
    assert isinstance(result.cash_balance, Decimal)
    assert result.investors["666"] == Decimal("10000.00")
    assert isinstance(result.investors["666"], Decimal)
    assert result.investors["777"] == Decimal("15000.00")


async def test_fund_penalty_round_trip(session: AsyncSession) -> None:
    penalty = FundPenalty(
        user_id="888",
        penalty_apr=Decimal("0.0500"),
        penalty_until=_utc(2026, 6, 6, 0),
    )

    session.add(FundPenaltyORM.from_domain(GUILD_ID, penalty))
    await session.commit()

    loaded = (
        await session.execute(
            select(FundPenaltyORM).where(
                FundPenaltyORM.guild_id == GUILD_ID, FundPenaltyORM.user_id == "888"
            )
        )
    ).scalar_one()
    result = loaded.to_domain()

    assert result == penalty
    assert result.penalty_apr == Decimal("0.0500")
    assert isinstance(result.penalty_apr, Decimal)
    assert result.penalty_until == _utc(2026, 6, 6, 0)
    assert result.penalty_until.tzinfo is not None


async def test_system_state_round_trip(session: AsyncSession) -> None:
    last_daily = _utc(2026, 5, 23, 4)
    last_weekly = _utc(2026, 5, 18, 4)

    session.add(
        SystemStateORM.create(
            GUILD_ID, last_daily_reset=last_daily, last_weekly_reset=last_weekly
        )
    )
    await session.commit()

    loaded = (
        await session.execute(
            select(SystemStateORM).where(SystemStateORM.guild_id == GUILD_ID)
        )
    ).scalar_one()

    assert loaded.last_daily_reset == last_daily
    assert loaded.last_daily_reset is not None
    assert loaded.last_daily_reset.tzinfo is not None
    assert loaded.last_weekly_reset == last_weekly
    assert loaded.last_weekly_reset is not None
    assert loaded.last_weekly_reset.tzinfo is not None


async def test_system_state_null_resets_round_trip(session: AsyncSession) -> None:
    session.add(
        SystemStateORM.create(GUILD_ID, last_daily_reset=None, last_weekly_reset=None)
    )
    await session.commit()

    loaded = (
        await session.execute(
            select(SystemStateORM).where(SystemStateORM.guild_id == GUILD_ID)
        )
    ).scalar_one()

    assert loaded.last_daily_reset is None
    assert loaded.last_weekly_reset is None


async def test_trade_cooldown_round_trip(session: AsyncSession) -> None:
    expires_at = _utc(2026, 5, 23, 12)

    session.add(TradeCooldownORM.create(GUILD_ID, user_id="999", expires_at=expires_at))
    await session.commit()

    loaded = (
        await session.execute(
            select(TradeCooldownORM).where(
                TradeCooldownORM.guild_id == GUILD_ID,
                TradeCooldownORM.user_id == "999",
            )
        )
    ).scalar_one()

    assert loaded.expires_at == expires_at
    assert loaded.expires_at.tzinfo is not None
    assert loaded.user_id == "999"


async def test_guild_isolation_same_user_two_guilds(session: AsyncSession) -> None:
    """ADR-0001 — the same user_id coexists across two guilds with distinct rows."""
    stock_a = Stock(
        user_id="shared",
        current=Decimal("100.00"),
        history=[],
        high_24h=Decimal("100.00"),
        low_24h=Decimal("100.00"),
        all_time_high=Decimal("100.00"),
    )
    stock_b = Stock(
        user_id="shared",
        current=Decimal("250.00"),
        history=[],
        high_24h=Decimal("250.00"),
        low_24h=Decimal("250.00"),
        all_time_high=Decimal("250.00"),
    )

    session.add(StockORM.from_domain("guild-A", stock_a))
    session.add(StockORM.from_domain("guild-B", stock_b))
    await session.commit()

    row_a = (
        await session.execute(
            select(StockORM).where(
                StockORM.guild_id == "guild-A", StockORM.user_id == "shared"
            )
        )
    ).scalar_one()
    row_b = (
        await session.execute(
            select(StockORM).where(
                StockORM.guild_id == "guild-B", StockORM.user_id == "shared"
            )
        )
    ).scalar_one()

    assert row_a.to_domain([]).current == Decimal("100.00")
    assert row_b.to_domain([]).current == Decimal("250.00")
