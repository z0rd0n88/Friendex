"""Tests for :class:`SqlPenaltyRepository` — the early-withdrawal penalty port.

These exercise the SQLAlchemy-backed adapter end-to-end against an in-memory
SQLite engine (FK enforcement ON per ADR-0002, though ``fund_penalties`` has no
FK of its own), proving the unit's promises:

* **Structural conformance** — ``SqlPenaltyRepository`` satisfies the
  :class:`~friendex.application.interfaces.IPenaltyRepo` Protocol *by shape*,
  not by inheritance (mypy gates the typed assignment).
* **Insert + read round trip** — a ``FundPenalty`` persists and rebuilds with
  exact ``Decimal`` quantisation on ``penalty_apr`` and tz-aware UTC on
  ``penalty_until``.
* **Expired-penalty handling** — the repo is a plain store: ``get`` returns a
  penalty whose ``penalty_until`` is already in the past (it does NOT silently
  drop it), and ``list_all`` surfaces both live and expired penalties so the
  penalty-decay task can find the expired ones and ``delete`` them. Expiry
  *interpretation* lives in the domain (``compute_effective_apy``), not here.

The fixture pattern mirrors ``test_fund_repo.py`` so the tests read coherently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import func, select

from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.orm import FundPenaltyORM
from friendex.adapters.persistence.penalty_repo import SqlPenaltyRepository
from friendex.domain.models import FundPenalty

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from friendex.application.interfaces import IPenaltyRepo

GUILD_ID = "555000111222333444"


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite engine (FK enforcement ON) with tables created."""
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


@pytest_asyncio.fixture
async def repo(engine: AsyncEngine) -> SqlPenaltyRepository:
    """A repository bound to the in-memory engine's sessionmaker."""
    return SqlPenaltyRepository(build_sessionmaker(engine))


def _same_scale(actual: Decimal, expected: Decimal) -> bool:
    """True when ``actual`` has the same quantisation exponent as ``expected``."""
    return actual.as_tuple().exponent == expected.as_tuple().exponent


def _penalty(
    user_id: str = "111",
    *,
    penalty_apr: Decimal = Decimal("0.0500"),
    penalty_until: datetime | None = None,
) -> FundPenalty:
    """A fund penalty with a tz-aware UTC ``penalty_until`` (default: future)."""
    if penalty_until is None:
        penalty_until = datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
    return FundPenalty(
        user_id=user_id,
        penalty_apr=penalty_apr,
        penalty_until=penalty_until,
    )


# ---------------------------------------------------------------------------
# AC1 — structural conformance to the IPenaltyRepo Protocol
# ---------------------------------------------------------------------------


def test_satisfies_ipenaltyrepo_protocol(repo: SqlPenaltyRepository) -> None:
    """AC1 — ``SqlPenaltyRepository`` conforms to ``IPenaltyRepo`` by shape."""
    conforming: IPenaltyRepo = repo
    assert conforming is repo
    for method in ("get", "upsert", "delete", "list_all"):
        assert callable(getattr(repo, method))


# ---------------------------------------------------------------------------
# AC1 — insert + read round trip (Decimal scale + UTC datetime preserved)
# ---------------------------------------------------------------------------


async def test_upsert_then_get_round_trips(repo: SqlPenaltyRepository) -> None:
    """AC1 — persist a penalty and read it back equal, scale + tz preserved."""
    until = datetime(2030, 6, 1, 9, 30, tzinfo=UTC)
    penalty = _penalty("111", penalty_apr=Decimal("0.0500"), penalty_until=until)

    await repo.upsert(GUILD_ID, penalty)
    result = await repo.get(GUILD_ID, "111")

    assert result is not None
    assert result == penalty
    assert isinstance(result.penalty_apr, Decimal)
    assert _same_scale(result.penalty_apr, Decimal("0.0500"))
    assert result.penalty_until == until
    assert result.penalty_until.tzinfo is not None
    assert result.penalty_until.utcoffset() == timedelta(0)


async def test_get_missing_returns_none(repo: SqlPenaltyRepository) -> None:
    """AC1 — a missing ``(guild_id, user_id)`` maps to ``None``."""
    assert await repo.get(GUILD_ID, "nope") is None


async def test_upsert_replaces_existing(repo: SqlPenaltyRepository) -> None:
    """AC1 — re-``upsert`` on the same key updates in place (no duplicate row)."""
    await repo.upsert(GUILD_ID, _penalty("111", penalty_apr=Decimal("0.0500")))
    await repo.upsert(GUILD_ID, _penalty("111", penalty_apr=Decimal("0.0250")))

    result = await repo.get(GUILD_ID, "111")
    assert result is not None
    assert result.penalty_apr == Decimal("0.0250")
    assert len(await repo.list_all(GUILD_ID)) == 1


# ---------------------------------------------------------------------------
# AC1 — expired-penalty handling: the repo is a plain store, NOT a filter
# ---------------------------------------------------------------------------


async def test_get_returns_expired_penalty_unfiltered(
    repo: SqlPenaltyRepository,
) -> None:
    """AC1 — an expired penalty is still returned by ``get`` (no TTL filter).

    Unlike cooldowns, the repo does not hide expired penalties: the decay task
    needs to see them to delete them, and the domain decides whether an expired
    penalty applies. Expiry interpretation lives in the domain, not the repo.
    """
    past = datetime(2000, 1, 1, 0, 0, tzinfo=UTC)
    await repo.upsert(GUILD_ID, _penalty("111", penalty_until=past))

    result = await repo.get(GUILD_ID, "111")

    assert result is not None
    assert result.penalty_until == past


async def test_list_all_includes_live_and_expired(
    repo: SqlPenaltyRepository,
) -> None:
    """AC1 — ``list_all`` surfaces both live and expired penalties for the guild."""
    past = datetime(2000, 1, 1, 0, 0, tzinfo=UTC)
    future = datetime(2030, 1, 1, 0, 0, tzinfo=UTC)
    await repo.upsert(GUILD_ID, _penalty("expired", penalty_until=past))
    await repo.upsert(GUILD_ID, _penalty("live", penalty_until=future))
    await repo.upsert("other-guild", _penalty("111", penalty_until=future))

    penalties = await repo.list_all(GUILD_ID)

    assert {p.user_id for p in penalties} == {"expired", "live"}


async def test_delete_removes_penalty(
    repo: SqlPenaltyRepository, session: AsyncSession
) -> None:
    """AC1 — ``delete`` removes the penalty row for the key."""
    await repo.upsert(GUILD_ID, _penalty("111"))

    await repo.delete(GUILD_ID, "111")

    assert await repo.get(GUILD_ID, "111") is None
    stmt = (
        select(func.count())
        .select_from(FundPenaltyORM)
        .where(
            FundPenaltyORM.guild_id == GUILD_ID,
            FundPenaltyORM.user_id == "111",
        )
    )
    assert int((await session.execute(stmt)).scalar_one()) == 0


async def test_delete_missing_penalty_is_noop(repo: SqlPenaltyRepository) -> None:
    """AC1 — deleting an absent penalty does not raise."""
    await repo.delete(GUILD_ID, "ghost")
    assert await repo.get(GUILD_ID, "ghost") is None


# ---------------------------------------------------------------------------
# UoW participation — regression for the silent-withdrawal bug (e2e find)
# ---------------------------------------------------------------------------
#
# ``FundService.withdraw`` calls ``penalty_repo.get`` + ``upsert`` INSIDE its
# ``SqlUnitOfWork`` transaction. Before the fix, every penalty-repo method
# unconditionally opened its own session; on the StaticPool in-memory engine
# that second session's BEGIN/COMMIT on the shared connection silently rolled
# back the outer UoW's pending fund + account writes — so an early withdrawal
# confirmed success while persisting nothing. The repo must join the shared
# ``current_session()`` exactly like the fund/user/price/cooldown repos.


async def test_upsert_joins_active_uow_and_commits_with_it(
    engine: AsyncEngine,
) -> None:
    """A penalty write inside a UoW must not clobber sibling writes."""
    from friendex.adapters.persistence.unit_of_work import SqlUnitOfWork
    from friendex.adapters.persistence.user_repo import SqlUserRepository
    from friendex.domain.models import (
        ActivityBucket,
        DailyProgress,
        UserAccount,
    )

    maker = build_sessionmaker(engine)
    repo = SqlPenaltyRepository(maker)
    user_repo = SqlUserRepository(maker)
    uow = SqlUnitOfWork(maker)
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)

    account = UserAccount(
        user_id="111",
        cash_balance=Decimal("10100.00"),
        net_worth=Decimal("10100.00"),
        month_start_net_worth=Decimal("10100.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
        opt_in=True,
        intro_shown=False,
    )

    async with uow.transaction():
        # Mirror the withdraw flow: a sibling write, a penalty read, then a
        # penalty write — all inside one transaction.
        await user_repo.upsert(GUILD_ID, account)
        assert await repo.get(GUILD_ID, "111") is None
        await repo.upsert(GUILD_ID, _penalty("111"))

    # Both the sibling write and the penalty must have committed together.
    persisted_account = await user_repo.get(GUILD_ID, "111")
    assert persisted_account is not None
    assert persisted_account.cash_balance == Decimal("10100.00")
    persisted_penalty = await repo.get(GUILD_ID, "111")
    assert persisted_penalty is not None
    assert persisted_penalty.penalty_apr == Decimal("0.0500")


async def test_upsert_rolls_back_with_failed_uow(engine: AsyncEngine) -> None:
    """A penalty written inside a failing UoW must not survive the rollback."""
    from friendex.adapters.persistence.unit_of_work import SqlUnitOfWork

    maker = build_sessionmaker(engine)
    repo = SqlPenaltyRepository(maker)
    uow = SqlUnitOfWork(maker)

    class _Boom(Exception):
        pass

    try:
        async with uow.transaction():
            await repo.upsert(GUILD_ID, _penalty("111"))
            raise _Boom
    except _Boom:
        pass

    assert await repo.get(GUILD_ID, "111") is None
