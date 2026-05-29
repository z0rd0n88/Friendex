"""SQL-level rollback integration test for :class:`SqlUnitOfWork`.

The application-layer fakes pin the trading service's mid-sequence rollback
contract (``tests/application/test_trading_service_atomicity.py``) — this
module is the parallel pin against a **real** SQLAlchemy engine. It proves
that:

* :class:`SqlUnitOfWork` opens one shared ``AsyncSession`` for the scope.
* Repositories migrated to consume :func:`current_session` enrol every
  write into that session, so the whole transaction is one logical unit.
* A mid-sequence persistence failure inside ``/short`` rolls back EVERY
  write — the user row, the fund row, and the cooldown row are all
  restored to their pre-``short`` state when an exception escapes the UoW
  block.

This is the test the PR-88 review (issuecomment-4578074161 C1) flagged as
missing: until a real engine exercises the seam, the production wiring of
``Container``-supplied ``SqlUnitOfWork`` cannot claim #82 C2 fixed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from freezegun import freeze_time

from friendex.adapters.config import Settings
from friendex.adapters.persistence.cooldown_repo import SqlTradeCooldownRepository
from friendex.adapters.persistence.db import (
    Base,
    build_engine,
    build_sessionmaker,
)
from friendex.adapters.persistence.fund_repo import SqlFundRepository
from friendex.adapters.persistence.price_repo import SqlPriceRepository
from friendex.adapters.persistence.unit_of_work import SqlUnitOfWork
from friendex.adapters.persistence.user_repo import SqlUserRepository
from friendex.application.lock_manager import LockManager
from friendex.application.trading_service import TradingService
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    HedgeFund,
    Stock,
    UserAccount,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )


_VALID_TOKEN = "x" * 32
_GUILD = "100000000000000001"
_SHORTER = "shorter-1"
_TARGET = "target-1"
# A weekday inside market hours so ``short`` is not blocked by the
# market-hours guard.
_MARKET_OPEN = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures — real in-memory SQLite engine + sessionmaker.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite engine with the full schema created."""
    eng = build_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return build_sessionmaker(engine)


@pytest.fixture
def settings() -> Settings:
    return Settings(discord_token=_VALID_TOKEN)


def _account(user_id: str, *, cash: Decimal) -> UserAccount:
    now = datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=cash,
        month_start_net_worth=cash,
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
        opt_in=True,
    )


def _stock(user_id: str, *, current: Decimal) -> Stock:
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _fund(user_id: str, *, cash: Decimal) -> HedgeFund:
    return HedgeFund(
        fund_id=user_id,
        name=user_id,
        manager_id=user_id,
        cash_balance=cash,
        investors={},
    )


class _ExplodingPriceRepo:
    """SQL price repo wrapper that raises after the first write.

    Wraps the real :class:`SqlPriceRepository` so reads still work, but the
    second persistence call (``append_history`` or the second ``upsert``)
    raises. Forces a mid-sequence failure inside ``short`` AFTER the user
    and fund have been written via the shared UoW session, so the rollback
    must un-do real SQLite rows.
    """

    def __init__(self, inner: SqlPriceRepository, fail_after: int = 1) -> None:
        self._inner = inner
        self._writes = 0
        self._fail_after = fail_after

    async def get(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.get(*args, **kwargs)

    async def list_all(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.list_all(*args, **kwargs)

    async def upsert(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._writes += 1
        if self._writes > self._fail_after:
            raise RuntimeError("simulated SQL persistence failure")
        return await self._inner.upsert(*args, **kwargs)

    async def append_history(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._writes += 1
        if self._writes > self._fail_after:
            raise RuntimeError("simulated SQL persistence failure")
        return await self._inner.append_history(*args, **kwargs)

    async def get_history(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.get_history(*args, **kwargs)

    async def prune_history_older_than(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.prune_history_older_than(*args, **kwargs)

    async def delete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.delete(*args, **kwargs)


async def test_short_rolls_back_user_fund_and_cooldown_against_real_sqlite(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """SQL-level pin: a mid-``short`` write failure rolls EVERY row back.

    Setup against a real in-memory SQLite engine:

    1. Seed the shorter's user account ($400) and their hedge fund ($2000).
    2. Seed the target user (opted in) and the target's stock (current $100).
    3. Wrap the price repo so its second write raises mid-``short``.
    4. Drive ``short(shorter, target, 10)`` inside a UoW transaction.

    Expectations after the call raises:

    * Shorter's cash is exactly the seeded $400 (no debit committed).
    * Shorter has NO short position on the target.
    * Hedge fund cash is exactly the seeded $2000 (no collateral lock
      committed).
    * The cooldown row is absent (no TTL written).

    Pre-fix (C2 half-fixed) the container handed services a
    :class:`NullUnitOfWork`, so the UoW transaction is a no-op and the
    user / fund writes would commit independently before the price
    failure — the assertions below would fail with partially-debited cash.
    Post-fix the SqlUnitOfWork wires through ``Container`` and the repos
    consume ``current_session()``, so the whole sequence rolls back.
    """
    user_repo = SqlUserRepository(sessionmaker)
    fund_repo = SqlFundRepository(sessionmaker)
    price_repo = SqlPriceRepository(sessionmaker)
    cooldown_repo = SqlTradeCooldownRepository(sessionmaker)
    uow = SqlUnitOfWork(sessionmaker)

    starting_cash = Decimal("400.00")
    starting_fund = Decimal("2000.00")
    await user_repo.upsert(_GUILD, _account(_SHORTER, cash=starting_cash))
    await fund_repo.upsert(_GUILD, _fund(_SHORTER, cash=starting_fund))
    await user_repo.upsert(_GUILD, _account(_TARGET, cash=Decimal("10000.00")))
    await price_repo.upsert(_GUILD, _stock(_TARGET, current=Decimal("100.00")))

    exploding_price_repo = _ExplodingPriceRepo(price_repo, fail_after=1)

    service = TradingService(
        guild_id=_GUILD,
        user_repo=user_repo,
        price_repo=exploding_price_repo,  # type: ignore[arg-type]
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=LockManager(),
        settings=settings,
        unit_of_work=uow,
    )

    with freeze_time(_MARKET_OPEN), pytest.raises(RuntimeError):
        await service.short(_SHORTER, _TARGET, 10)

    after_shorter = await user_repo.get(_GUILD, _SHORTER)
    after_fund = await fund_repo.get(_GUILD, _SHORTER)
    after_cooldown = await cooldown_repo.get(_GUILD, _SHORTER, now=_MARKET_OPEN)

    assert after_shorter is not None
    assert after_shorter.cash_balance == starting_cash, (
        "shorter's cash must be unchanged after rollback"
    )
    assert _TARGET not in after_shorter.short_positions, (
        "no short position must persist after rollback"
    )
    assert after_fund is not None
    assert after_fund.cash_balance == starting_fund, (
        "fund cash must be unchanged after rollback"
    )
    assert after_cooldown is None, "cooldown row must not survive rollback"


async def test_sql_uow_commits_on_clean_exit(
    sessionmaker: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Smoke-test the happy path: a successful ``short`` commits every write.

    The complement to the rollback test — proves the UoW envelope does NOT
    accidentally swallow writes on clean exit. Without this we could ship
    a regression where the SqlUnitOfWork rolls back unconditionally.
    """
    user_repo = SqlUserRepository(sessionmaker)
    fund_repo = SqlFundRepository(sessionmaker)
    price_repo = SqlPriceRepository(sessionmaker)
    cooldown_repo = SqlTradeCooldownRepository(sessionmaker)
    uow = SqlUnitOfWork(sessionmaker)

    starting_cash = Decimal("10000.00")
    starting_fund = Decimal("2000.00")
    await user_repo.upsert(_GUILD, _account(_SHORTER, cash=starting_cash))
    await fund_repo.upsert(_GUILD, _fund(_SHORTER, cash=starting_fund))
    await user_repo.upsert(_GUILD, _account(_TARGET, cash=Decimal("10000.00")))
    await price_repo.upsert(_GUILD, _stock(_TARGET, current=Decimal("100.00")))

    service = TradingService(
        guild_id=_GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=LockManager(),
        settings=settings,
        unit_of_work=uow,
    )

    with freeze_time(_MARKET_OPEN):
        result = await service.short(_SHORTER, _TARGET, 1)

    assert result.shares == 1
    assert result.locked_cash + result.locked_fund == Decimal("100.00")

    after_shorter = await user_repo.get(_GUILD, _SHORTER)
    after_cooldown = await cooldown_repo.get(_GUILD, _SHORTER, now=_MARKET_OPEN)

    assert after_shorter is not None
    assert _TARGET in after_shorter.short_positions
    assert after_shorter.cash_balance < starting_cash
    assert after_cooldown is not None
