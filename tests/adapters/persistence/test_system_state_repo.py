"""Tests for :class:`SqlSystemStateRepository` — the per-guild reset-state port.

These exercise the SQLAlchemy-backed adapter end-to-end against an in-memory
SQLite engine, proving the unit's promises:

* **Structural conformance** — ``SqlSystemStateRepository`` satisfies the
  :class:`~friendex.application.interfaces.ISystemStateRepo` Protocol *by
  shape*, not by inheritance (mypy gates the typed assignment).
* **Single-row-per-guild** — there is at most one row per ``guild_id``; two
  ``upsert`` calls update in place and never create a duplicate row.
* **Sensible default** — ``get`` on an absent guild returns ``None`` (callers
  treat that as "never reset").
* **UTC datetimes** — ``last_daily_reset`` / ``last_weekly_reset`` round-trip as
  tz-aware UTC (or ``None``).
* **Unscoped ``list_all``** — iterates every guild's state row.

The fixture pattern mirrors ``test_fund_repo.py`` so the tests read coherently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import func, select

from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.orm import SystemStateORM
from friendex.adapters.persistence.system_state_repo import SqlSystemStateRepository
from friendex.application.interfaces import SystemState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from friendex.application.interfaces import ISystemStateRepo

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
async def repo(engine: AsyncEngine) -> SqlSystemStateRepository:
    """A repository bound to the in-memory engine's sessionmaker."""
    return SqlSystemStateRepository(build_sessionmaker(engine))


def _state(
    guild_id: str = GUILD_ID,
    *,
    last_daily_reset: datetime | None = None,
    last_weekly_reset: datetime | None = None,
) -> SystemState:
    """A system-state DTO with tz-aware UTC reset timestamps."""
    if last_daily_reset is None:
        last_daily_reset = datetime(2026, 5, 24, 6, 30, tzinfo=UTC)
    if last_weekly_reset is None:
        last_weekly_reset = datetime(2026, 5, 18, 6, 30, tzinfo=UTC)
    return SystemState(
        guild_id=guild_id,
        last_daily_reset=last_daily_reset,
        last_weekly_reset=last_weekly_reset,
    )


# ---------------------------------------------------------------------------
# AC3 — structural conformance to the ISystemStateRepo Protocol
# ---------------------------------------------------------------------------


def test_satisfies_isystemstaterepo_protocol(
    repo: SqlSystemStateRepository,
) -> None:
    """AC3 — ``SqlSystemStateRepository`` conforms to the Protocol by shape."""
    conforming: ISystemStateRepo = repo
    assert conforming is repo
    for method in ("get", "upsert", "delete", "list_all"):
        assert callable(getattr(repo, method))


# ---------------------------------------------------------------------------
# AC3 — round trip + UTC datetimes
# ---------------------------------------------------------------------------


async def test_upsert_then_get_round_trips(repo: SqlSystemStateRepository) -> None:
    """AC3 — persist a state row and read it back equal, tz preserved."""
    daily = datetime(2026, 5, 24, 6, 30, tzinfo=UTC)
    weekly = datetime(2026, 5, 18, 6, 30, tzinfo=UTC)
    state = _state(last_daily_reset=daily, last_weekly_reset=weekly)

    await repo.upsert(state)
    result = await repo.get(GUILD_ID)

    assert result is not None
    assert result == state
    assert result.last_daily_reset == daily
    assert result.last_daily_reset is not None
    assert result.last_daily_reset.utcoffset() == timedelta(0)
    assert result.last_weekly_reset == weekly


async def test_get_missing_returns_none(repo: SqlSystemStateRepository) -> None:
    """AC3 — ``get`` on a guild with no state row returns ``None`` (never reset)."""
    assert await repo.get(GUILD_ID) is None


async def test_state_allows_null_reset_timestamps(
    repo: SqlSystemStateRepository,
) -> None:
    """AC3 — a fresh guild may have ``None`` reset timestamps that round-trip."""
    state = SystemState(
        guild_id=GUILD_ID,
        last_daily_reset=None,
        last_weekly_reset=None,
    )

    await repo.upsert(state)
    result = await repo.get(GUILD_ID)

    assert result is not None
    assert result.last_daily_reset is None
    assert result.last_weekly_reset is None


# ---------------------------------------------------------------------------
# AC3 — single-row upsert idempotency (two upserts -> one row)
# ---------------------------------------------------------------------------


async def test_upsert_is_idempotent_single_row(
    repo: SqlSystemStateRepository, session: AsyncSession
) -> None:
    """AC3 — two ``upsert`` calls update in place; exactly one row remains."""
    first = _state(last_daily_reset=datetime(2026, 5, 24, 6, 30, tzinfo=UTC))
    await repo.upsert(first)

    later = datetime(2026, 5, 25, 6, 30, tzinfo=UTC)
    second = _state(last_daily_reset=later)
    await repo.upsert(second)

    result = await repo.get(GUILD_ID)
    assert result is not None
    assert result.last_daily_reset == later

    stmt = (
        select(func.count())
        .select_from(SystemStateORM)
        .where(SystemStateORM.guild_id == GUILD_ID)
    )
    assert int((await session.execute(stmt)).scalar_one()) == 1


# ---------------------------------------------------------------------------
# AC3 — list_all is unscoped (every guild)
# ---------------------------------------------------------------------------


async def test_list_all_returns_every_guild(repo: SqlSystemStateRepository) -> None:
    """AC3 — ``list_all`` (unscoped) returns the state row for every guild."""
    await repo.upsert(_state(GUILD_ID))
    await repo.upsert(_state("guild-b"))
    await repo.upsert(_state("guild-c"))

    states = await repo.list_all()

    assert {s.guild_id for s in states} == {GUILD_ID, "guild-b", "guild-c"}


# ---------------------------------------------------------------------------
# AC3 — delete
# ---------------------------------------------------------------------------


async def test_delete_removes_state(repo: SqlSystemStateRepository) -> None:
    """AC3 — ``delete`` removes the guild's state row."""
    await repo.upsert(_state(GUILD_ID))

    await repo.delete(GUILD_ID)

    assert await repo.get(GUILD_ID) is None


async def test_delete_missing_state_is_noop(repo: SqlSystemStateRepository) -> None:
    """AC3 — deleting an absent state row does not raise."""
    await repo.delete(GUILD_ID)
    assert await repo.get(GUILD_ID) is None
