"""Tests for :class:`SqlFundRepository` — the hedge-fund aggregate port.

These exercise the SQLAlchemy-backed adapter end-to-end against an in-memory
SQLite engine that has FK enforcement ON (ADR-0002), proving the unit's
promises:

* **Structural conformance** — ``SqlFundRepository`` satisfies the
  :class:`~friendex.application.interfaces.IFundRepo` Protocol *by shape*, not
  by inheritance (mypy gates the typed assignment).
* **Full-aggregate round trip** — a ``HedgeFund`` carrying investor stakes
  persists and rebuilds with exact Decimal quantisation on every money field.
* **Investor add / remove round-trips** — re-``upsert`` with a changed
  ``investors`` dict replaces stakes wholesale (add then remove).
* **Idempotent events wallet** — ``ensure_events_wallet`` called twice yields a
  single wallet without mutating its balance.
* **Deletion cascade** — ``delete`` removes the fund *and* its investor rows via
  the DB-level ``ON DELETE CASCADE``.

The fixture pattern mirrors ``test_user_repo.py`` so the tests read coherently.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import func, select

from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.fund_repo import SqlFundRepository
from friendex.adapters.persistence.orm import FundInvestorORM, HedgeFundORM
from friendex.domain.models import HedgeFund

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from friendex.application.interfaces import IFundRepo

GUILD_ID = "555000111222333444"
EVENTS_WALLET_ID = "events_wallet"


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
async def repo(engine: AsyncEngine) -> SqlFundRepository:
    """A repository bound to the in-memory engine's sessionmaker."""
    return SqlFundRepository(build_sessionmaker(engine))


def _same_scale(actual: Decimal, expected: Decimal) -> bool:
    """True when ``actual`` has the same quantisation exponent as ``expected``."""
    return actual.as_tuple().exponent == expected.as_tuple().exponent


def _fund(
    fund_id: str = "fund-1",
    *,
    investors: dict[str, Decimal] | None = None,
) -> HedgeFund:
    """A hedge fund with sensible scalars and optional investor stakes."""
    return HedgeFund(
        fund_id=fund_id,
        name="Alpha Capital",
        manager_id="999",
        cash_balance=Decimal("50000.00"),
        investors=investors
        if investors is not None
        else {
            "111": Decimal("1000.00"),
            "222": Decimal("2500.50"),
        },
    )


# ---------------------------------------------------------------------------
# AC2 — structural conformance to the IFundRepo Protocol
# ---------------------------------------------------------------------------


def test_satisfies_ifundrepo_protocol(repo: SqlFundRepository) -> None:
    """AC2 — ``SqlFundRepository`` conforms to ``IFundRepo`` by shape (no ABC)."""
    conforming: IFundRepo = repo
    assert conforming is repo
    for method in ("get", "upsert", "delete", "list_all", "ensure_events_wallet"):
        assert callable(getattr(repo, method))


# ---------------------------------------------------------------------------
# AC2 — full-aggregate round trip including investors
# ---------------------------------------------------------------------------


async def test_upsert_then_get_round_trips_with_investors(
    repo: SqlFundRepository,
) -> None:
    """AC2 — persist a fund with investors and read it back equal."""
    fund = _fund("fund-1")

    await repo.upsert(GUILD_ID, fund)
    result = await repo.get(GUILD_ID, "fund-1")

    assert result is not None
    assert result == fund
    assert isinstance(result.cash_balance, Decimal)
    assert _same_scale(result.cash_balance, Decimal("50000.00"))
    assert _same_scale(result.investors["111"], Decimal("1000.00"))
    assert _same_scale(result.investors["222"], Decimal("2500.50"))


async def test_get_missing_returns_none(repo: SqlFundRepository) -> None:
    """AC2 — a missing ``(guild_id, fund_id)`` maps to ``None``."""
    assert await repo.get(GUILD_ID, "nope") is None


async def test_list_all_returns_every_fund_in_guild(repo: SqlFundRepository) -> None:
    """AC2 — ``list_all`` scopes to one guild and rebuilds each fund's investors."""
    await repo.upsert(GUILD_ID, _fund("fund-1"))
    await repo.upsert(GUILD_ID, _fund("fund-2"))
    await repo.upsert("other-guild", _fund("fund-1"))

    funds = await repo.list_all(GUILD_ID)

    assert {f.fund_id for f in funds} == {"fund-1", "fund-2"}
    # Investors rebuilt for a listed fund (eager-loaded), not just scalars.
    listed = next(f for f in funds if f.fund_id == "fund-1")
    assert listed.investors == _fund("fund-1").investors


# ---------------------------------------------------------------------------
# AC3 — investor add then remove round-trips via re-upsert
# ---------------------------------------------------------------------------


async def test_investor_add_then_remove_round_trips(repo: SqlFundRepository) -> None:
    """AC3 — re-``upsert`` adds a new investor, then a later one removes one."""
    await repo.upsert(GUILD_ID, _fund("fund-1", investors={"111": Decimal("1000.00")}))

    # Add investor "222".
    added = _fund(
        "fund-1",
        investors={"111": Decimal("1000.00"), "222": Decimal("3000.00")},
    )
    await repo.upsert(GUILD_ID, added)
    after_add = await repo.get(GUILD_ID, "fund-1")
    assert after_add is not None
    assert after_add.investors == {
        "111": Decimal("1000.00"),
        "222": Decimal("3000.00"),
    }

    # Remove investor "111" (wholesale replacement of the investor set).
    removed = _fund("fund-1", investors={"222": Decimal("3000.00")})
    await repo.upsert(GUILD_ID, removed)
    after_remove = await repo.get(GUILD_ID, "fund-1")
    assert after_remove is not None
    assert after_remove.investors == {"222": Decimal("3000.00")}
    assert "111" not in after_remove.investors


async def test_upsert_with_no_investors(repo: SqlFundRepository) -> None:
    """AC3 — a fund can persist and rebuild with an empty investor set."""
    await repo.upsert(GUILD_ID, _fund("fund-1", investors={}))

    result = await repo.get(GUILD_ID, "fund-1")
    assert result is not None
    assert result.investors == {}


# ---------------------------------------------------------------------------
# AC2 — ensure_events_wallet idempotency
# ---------------------------------------------------------------------------


async def test_ensure_events_wallet_creates_when_absent(
    repo: SqlFundRepository,
) -> None:
    """AC2 — first call creates the events-wallet pseudo-fund."""
    wallet = await repo.ensure_events_wallet(GUILD_ID)

    assert wallet.fund_id == EVENTS_WALLET_ID
    assert wallet.investors == {}
    assert isinstance(wallet.cash_balance, Decimal)
    # It is now retrievable as an ordinary fund row.
    persisted = await repo.get(GUILD_ID, EVENTS_WALLET_ID)
    assert persisted is not None
    assert persisted.fund_id == EVENTS_WALLET_ID


async def test_ensure_events_wallet_is_idempotent(
    repo: SqlFundRepository, session: AsyncSession
) -> None:
    """AC2 — two calls yield exactly one wallet and do not mutate its balance."""
    first = await repo.ensure_events_wallet(GUILD_ID)
    second = await repo.ensure_events_wallet(GUILD_ID)

    assert first.fund_id == second.fund_id == EVENTS_WALLET_ID
    # Balance is preserved, not reset/double-credited, on the repeat call.
    assert second.cash_balance == first.cash_balance

    # Exactly one wallet row exists for the guild.
    stmt = (
        select(func.count())
        .select_from(HedgeFundORM)
        .where(
            HedgeFundORM.guild_id == GUILD_ID,
            HedgeFundORM.fund_id == EVENTS_WALLET_ID,
        )
    )
    assert int((await session.execute(stmt)).scalar_one()) == 1


async def test_ensure_events_wallet_preserves_existing_balance(
    repo: SqlFundRepository,
) -> None:
    """AC2 — an existing wallet with a balance is returned unchanged."""
    seeded = HedgeFund(
        fund_id=EVENTS_WALLET_ID,
        name="Events Wallet",
        manager_id="0",
        cash_balance=Decimal("777.00"),
        investors={},
    )
    await repo.upsert(GUILD_ID, seeded)

    wallet = await repo.ensure_events_wallet(GUILD_ID)

    assert wallet.cash_balance == Decimal("777.00")


# ---------------------------------------------------------------------------
# Deletion cascade (investors are children of the fund)
# ---------------------------------------------------------------------------


async def _investor_count(session: AsyncSession, guild_id: str, fund_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(FundInvestorORM)
        .where(
            FundInvestorORM.guild_id == guild_id,
            FundInvestorORM.fund_id == fund_id,
        )
    )
    return int((await session.execute(stmt)).scalar_one())


async def test_delete_cascades_to_investors(
    repo: SqlFundRepository, session: AsyncSession
) -> None:
    """AC2 — ``delete`` removes the fund and cascades to its investor rows."""
    await repo.upsert(GUILD_ID, _fund("victim"))

    assert await _investor_count(session, GUILD_ID, "victim") == 2

    await repo.delete(GUILD_ID, "victim")

    assert await _investor_count(session, GUILD_ID, "victim") == 0
    assert await repo.get(GUILD_ID, "victim") is None


async def test_delete_missing_fund_is_noop(repo: SqlFundRepository) -> None:
    """AC2 — deleting an absent fund does not raise."""
    await repo.delete(GUILD_ID, "ghost")
    assert await repo.get(GUILD_ID, "ghost") is None
