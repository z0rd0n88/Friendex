"""Tests for :class:`SqlPriceRepository` — the stock + price-history port.

These exercise the SQLAlchemy-backed adapter end-to-end against an in-memory
SQLite engine that has FK enforcement ON (ADR-0002), proving the unit's
promises:

* **Structural conformance** — ``SqlPriceRepository`` satisfies the
  :class:`~friendex.application.interfaces.IPriceRepo` Protocol *by shape*, not
  by inheritance (mypy gates the typed assignment).
* **Scalar round trip** — a ``Stock`` persists and rebuilds with exact Decimal
  quantisation (checked via ``as_tuple().exponent``) and tz-aware UTC datetimes
  on its history points.
* **Append-only history** — ``append_history`` then ``get_history`` returns the
  appended points oldest-first; ``since`` restricts the window.
* **Bulk pruning** — ``prune_history_older_than`` retains ONLY records inside
  the window (older than the cutoff are gone, newer are kept) and reports the
  deleted count; it spans every guild.
* **Deletion cascade** — ``delete`` removes the stock *and* its history via the
  DB-level ``ON DELETE CASCADE``.

The fixture pattern mirrors ``test_user_repo.py`` so the two read coherently.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import func, select

from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.orm import PriceHistoryORM, StockORM
from friendex.adapters.persistence.price_repo import SqlPriceRepository
from friendex.domain.models import PricePoint, Stock

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from friendex.application.interfaces import IPriceRepo

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
async def repo(engine: AsyncEngine) -> SqlPriceRepository:
    """A repository bound to the in-memory engine's sessionmaker."""
    return SqlPriceRepository(build_sessionmaker(engine))


def _utc(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 30, 15, tzinfo=UTC)


def _same_scale(actual: Decimal, expected: Decimal) -> bool:
    """True when ``actual`` has the same quantisation exponent as ``expected``."""
    return actual.as_tuple().exponent == expected.as_tuple().exponent


def _stock(user_id: str = "111", *, history: list[PricePoint] | None = None) -> Stock:
    """A stock with sensible scalars and optional seeded history."""
    return Stock(
        user_id=user_id,
        current=Decimal("123.45"),
        history=history if history is not None else [],
        high_24h=Decimal("130.00"),
        low_24h=Decimal("110.00"),
        all_time_high=Decimal("200.00"),
    )


# ---------------------------------------------------------------------------
# AC1 — structural conformance to the IPriceRepo Protocol
# ---------------------------------------------------------------------------


def test_satisfies_ipricerepo_protocol(repo: SqlPriceRepository) -> None:
    """AC1 — ``SqlPriceRepository`` conforms to ``IPriceRepo`` by shape (no ABC)."""
    conforming: IPriceRepo = repo
    assert conforming is repo
    for method in (
        "get",
        "upsert",
        "delete",
        "list_all",
        "append_history",
        "get_history",
        "prune_history_older_than",
    ):
        assert callable(getattr(repo, method))


# ---------------------------------------------------------------------------
# AC1 — scalar CRUD round trip
# ---------------------------------------------------------------------------


async def test_upsert_then_get_round_trips_scalars(repo: SqlPriceRepository) -> None:
    """AC1 — persist a stock's scalar row and read it back equal."""
    stock = _stock("111")

    await repo.upsert(GUILD_ID, stock)
    result = await repo.get(GUILD_ID, "111")

    assert result is not None
    assert result == stock
    assert isinstance(result.current, Decimal)
    assert _same_scale(result.current, Decimal("123.45"))
    assert _same_scale(result.high_24h, Decimal("130.00"))
    assert _same_scale(result.low_24h, Decimal("110.00"))
    assert _same_scale(result.all_time_high, Decimal("200.00"))


async def test_get_missing_returns_none(repo: SqlPriceRepository) -> None:
    """AC1 — a missing ``(guild_id, user_id)`` maps to ``None``."""
    assert await repo.get(GUILD_ID, "nope") is None


async def test_upsert_replaces_scalars(repo: SqlPriceRepository) -> None:
    """AC1 — re-``upsert`` overwrites the scalar row."""
    await repo.upsert(GUILD_ID, _stock("111"))

    replacement = Stock(
        user_id="111",
        current=Decimal("70.00"),
        history=[],
        high_24h=Decimal("70.00"),
        low_24h=Decimal("70.00"),
        all_time_high=Decimal("200.00"),
    )
    await repo.upsert(GUILD_ID, replacement)

    result = await repo.get(GUILD_ID, "111")
    assert result is not None
    assert result.current == Decimal("70.00")


async def test_list_all_returns_every_stock_in_guild(repo: SqlPriceRepository) -> None:
    """AC1 — ``list_all`` scopes to one guild and rebuilds each stock."""
    p1 = PricePoint(price=Decimal("100.00"), timestamp=_utc(2026, 5, 23, 8))
    await repo.upsert(GUILD_ID, _stock("111"))
    await repo.upsert(GUILD_ID, _stock("222"))
    await repo.upsert("other-guild", _stock("111"))
    await repo.append_history(GUILD_ID, "111", p1)

    stocks = await repo.list_all(GUILD_ID)

    assert {s.user_id for s in stocks} == {"111", "222"}
    # History is rebuilt for listed stocks (eager-loaded, not just scalars).
    listed = next(s for s in stocks if s.user_id == "111")
    assert listed.history == [p1]


# ---------------------------------------------------------------------------
# AC3 — append_history then get_history
# ---------------------------------------------------------------------------


async def test_append_history_then_get_history_returns_rows(
    repo: SqlPriceRepository,
) -> None:
    """AC3 — appended points come back oldest-first with Decimal + UTC intact."""
    await repo.upsert(GUILD_ID, _stock("111"))
    p_old = PricePoint(price=Decimal("90.00"), timestamp=_utc(2026, 5, 20, 8))
    p_mid = PricePoint(price=Decimal("100.50"), timestamp=_utc(2026, 5, 22, 8))
    p_new = PricePoint(price=Decimal("110.25"), timestamp=_utc(2026, 5, 24, 8))

    # Append out of order to prove ordering is by recorded_at, not insertion.
    await repo.append_history(GUILD_ID, "111", p_mid)
    await repo.append_history(GUILD_ID, "111", p_new)
    await repo.append_history(GUILD_ID, "111", p_old)

    history = await repo.get_history(GUILD_ID, "111")

    assert history == [p_old, p_mid, p_new]
    # Decimal exactness + quantisation and tz-aware UTC survive the round trip.
    assert _same_scale(history[1].price, Decimal("100.50"))
    assert all(p.timestamp.tzinfo is not None for p in history)


async def test_get_history_since_restricts_window(repo: SqlPriceRepository) -> None:
    """AC3 — ``since`` returns only points at or after the given instant."""
    await repo.upsert(GUILD_ID, _stock("111"))
    p_old = PricePoint(price=Decimal("90.00"), timestamp=_utc(2026, 5, 20, 8))
    p_mid = PricePoint(price=Decimal("100.00"), timestamp=_utc(2026, 5, 22, 8))
    p_new = PricePoint(price=Decimal("110.00"), timestamp=_utc(2026, 5, 24, 8))
    for point in (p_old, p_mid, p_new):
        await repo.append_history(GUILD_ID, "111", point)

    since = _utc(2026, 5, 22, 8)
    window = await repo.get_history(GUILD_ID, "111", since=since)

    assert window == [p_mid, p_new]


async def test_get_history_empty_for_unknown_stock(repo: SqlPriceRepository) -> None:
    """AC3 — history of a stock with no points (or no row) is an empty list."""
    assert await repo.get_history(GUILD_ID, "ghost") == []


# ---------------------------------------------------------------------------
# AC3 — prune_history_older_than (bulk DELETE, window retention)
# ---------------------------------------------------------------------------


async def test_prune_history_retains_only_window(repo: SqlPriceRepository) -> None:
    """AC3 — rows older than the cutoff are deleted; newer ones are retained."""
    await repo.upsert(GUILD_ID, _stock("111"))
    older = PricePoint(price=Decimal("90.00"), timestamp=_utc(2026, 5, 1, 8))
    on_cutoff = PricePoint(price=Decimal("95.00"), timestamp=_utc(2026, 5, 10, 0))
    newer = PricePoint(price=Decimal("110.00"), timestamp=_utc(2026, 5, 20, 8))
    for point in (older, on_cutoff, newer):
        await repo.append_history(GUILD_ID, "111", point)

    cutoff = _utc(2026, 5, 10, 0)
    deleted = await repo.prune_history_older_than(cutoff)

    # Strictly-older `older` (May 1) is gone; the on-cutoff and newer rows stay
    # (DELETE WHERE recorded_at < cutoff — boundary is inclusive of cutoff).
    assert deleted == 1
    remaining = await repo.get_history(GUILD_ID, "111")
    assert remaining == [on_cutoff, newer]


async def test_prune_history_spans_all_guilds(repo: SqlPriceRepository) -> None:
    """AC3 — pruning is cross-guild: a single sweep over every guild's history."""
    await repo.upsert(GUILD_ID, _stock("111"))
    await repo.upsert("other-guild", _stock("111"))
    old = PricePoint(price=Decimal("90.00"), timestamp=_utc(2026, 5, 1, 8))
    new = PricePoint(price=Decimal("110.00"), timestamp=_utc(2026, 5, 20, 8))
    await repo.append_history(GUILD_ID, "111", old)
    await repo.append_history(GUILD_ID, "111", new)
    await repo.append_history("other-guild", "111", old)

    deleted = await repo.prune_history_older_than(_utc(2026, 5, 10, 0))

    assert deleted == 2  # one old row in each guild
    assert await repo.get_history(GUILD_ID, "111") == [new]
    assert await repo.get_history("other-guild", "111") == []


async def test_prune_history_returns_zero_when_nothing_old(
    repo: SqlPriceRepository,
) -> None:
    """AC3 — pruning with no old rows deletes nothing and returns 0."""
    await repo.upsert(GUILD_ID, _stock("111"))
    await repo.append_history(
        GUILD_ID,
        "111",
        PricePoint(price=Decimal("110.00"), timestamp=_utc(2026, 5, 20, 8)),
    )

    assert await repo.prune_history_older_than(_utc(2026, 5, 1, 0)) == 0


# ---------------------------------------------------------------------------
# Deletion cascade (history is a child of the stock)
# ---------------------------------------------------------------------------


async def _history_count(session: AsyncSession, guild_id: str, user_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(PriceHistoryORM)
        .where(
            PriceHistoryORM.guild_id == guild_id,
            PriceHistoryORM.user_id == user_id,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def _stock_count(session: AsyncSession, guild_id: str, user_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(StockORM)
        .where(StockORM.guild_id == guild_id, StockORM.user_id == user_id)
    )
    return int((await session.execute(stmt)).scalar_one())


async def test_delete_cascades_to_history(
    repo: SqlPriceRepository, session: AsyncSession
) -> None:
    """AC1 — ``delete`` removes the stock and cascades to its price history."""
    await repo.upsert(GUILD_ID, _stock("victim"))
    await repo.append_history(
        GUILD_ID,
        "victim",
        PricePoint(price=Decimal("100.00"), timestamp=_utc(2026, 5, 20, 8)),
    )
    await repo.append_history(
        GUILD_ID,
        "victim",
        PricePoint(price=Decimal("101.00"), timestamp=_utc(2026, 5, 21, 8)),
    )

    assert await _stock_count(session, GUILD_ID, "victim") == 1
    assert await _history_count(session, GUILD_ID, "victim") == 2

    await repo.delete(GUILD_ID, "victim")

    assert await _stock_count(session, GUILD_ID, "victim") == 0
    assert await _history_count(session, GUILD_ID, "victim") == 0
    assert await repo.get(GUILD_ID, "victim") is None


async def test_delete_missing_stock_is_noop(repo: SqlPriceRepository) -> None:
    """AC1 — deleting an absent stock does not raise."""
    await repo.delete(GUILD_ID, "ghost")
    assert await repo.get(GUILD_ID, "ghost") is None
