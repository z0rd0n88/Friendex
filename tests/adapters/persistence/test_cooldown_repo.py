"""Tests for :class:`SqlTradeCooldownRepository` — short/cover cooldown TTL port.

These exercise the SQLAlchemy-backed adapter end-to-end against an in-memory
SQLite engine, proving the unit's promises:

* **Structural conformance** — ``SqlTradeCooldownRepository`` satisfies the
  :class:`~friendex.application.interfaces.ITradeCooldownRepo` Protocol *by
  shape*, not by inheritance (mypy gates the typed assignment).
* **TTL via ``expires_at``** — ``get`` returns a live cooldown but excludes one
  whose ``expires_at`` has passed (cutoff = now, UTC-aware). The boundary is
  load-bearing and pinned with a non-tautological test: a row expiring exactly
  *at* now is treated as expired (inclusive ``<=``), and one a hair *after* now
  is live.
* **Bulk purge** — :meth:`purge_expired` deletes every row with
  ``expires_at <= now`` in one statement and returns the count, leaving live
  rows untouched.
* **Scope-in-DTO** — ``upsert`` takes a :class:`TradeCooldown` whose ``guild_id``
  is carried inside the DTO (no separate ``guild_id`` arg).

The fixture pattern mirrors ``test_fund_repo.py`` so the tests read coherently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy import func, select

from friendex.adapters.persistence.cooldown_repo import SqlTradeCooldownRepository
from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.orm import TradeCooldownORM
from friendex.application.interfaces import TradeCooldown

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from friendex.application.interfaces import ITradeCooldownRepo

GUILD_ID = "555000111222333444"
NOW = datetime(2026, 5, 24, 12, 0, 0, tzinfo=UTC)


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
async def repo(engine: AsyncEngine) -> SqlTradeCooldownRepository:
    """A repository bound to the in-memory engine's sessionmaker."""
    return SqlTradeCooldownRepository(build_sessionmaker(engine))


def _cooldown(
    user_id: str = "111",
    *,
    guild_id: str = GUILD_ID,
    expires_at: datetime | None = None,
) -> TradeCooldown:
    """A cooldown DTO; default expiry is 15 minutes after ``NOW`` (live)."""
    if expires_at is None:
        expires_at = NOW + timedelta(minutes=15)
    return TradeCooldown(guild_id=guild_id, user_id=user_id, expires_at=expires_at)


# ---------------------------------------------------------------------------
# AC2 — structural conformance to the ITradeCooldownRepo Protocol
# ---------------------------------------------------------------------------


def test_satisfies_itradecooldownrepo_protocol(
    repo: SqlTradeCooldownRepository,
) -> None:
    """AC2 — ``SqlTradeCooldownRepository`` conforms to the Protocol by shape."""
    conforming: ITradeCooldownRepo = repo
    assert conforming is repo
    for method in ("get", "upsert", "delete", "list_all", "purge_expired"):
        assert callable(getattr(repo, method))


# ---------------------------------------------------------------------------
# AC2 — round trip + scope-in-DTO + UTC datetime preserved
# ---------------------------------------------------------------------------


async def test_upsert_then_get_round_trips(repo: SqlTradeCooldownRepository) -> None:
    """AC2 — a live cooldown persists and reads back equal, tz preserved."""
    expires = NOW + timedelta(minutes=15)
    cooldown = _cooldown("111", expires_at=expires)

    await repo.upsert(cooldown)
    result = await repo.get(GUILD_ID, "111", now=NOW)

    assert result is not None
    assert result == cooldown
    assert result.expires_at == expires
    assert result.expires_at.tzinfo is not None
    assert result.expires_at.utcoffset() == timedelta(0)


async def test_upsert_replaces_existing(repo: SqlTradeCooldownRepository) -> None:
    """AC2 — re-``upsert`` on the same key updates expiry (no duplicate row)."""
    await repo.upsert(_cooldown("111", expires_at=NOW + timedelta(minutes=5)))
    await repo.upsert(_cooldown("111", expires_at=NOW + timedelta(minutes=30)))

    result = await repo.get(GUILD_ID, "111", now=NOW)
    assert result is not None
    assert result.expires_at == NOW + timedelta(minutes=30)
    assert len(await repo.list_all(GUILD_ID)) == 1


# ---------------------------------------------------------------------------
# AC2 — TTL semantics: get excludes expired (cutoff = now), boundary pinned
# ---------------------------------------------------------------------------


async def test_get_returns_live_cooldown(repo: SqlTradeCooldownRepository) -> None:
    """AC2 — a cooldown expiring after now is returned by ``get``."""
    await repo.upsert(_cooldown("111", expires_at=NOW + timedelta(seconds=1)))

    result = await repo.get(GUILD_ID, "111", now=NOW)

    assert result is not None
    assert result.user_id == "111"


async def test_get_excludes_expired_cooldown(
    repo: SqlTradeCooldownRepository,
) -> None:
    """AC2 — a cooldown that already expired is excluded from ``get``."""
    await repo.upsert(_cooldown("111", expires_at=NOW - timedelta(seconds=1)))

    assert await repo.get(GUILD_ID, "111", now=NOW) is None


async def test_get_treats_exact_boundary_as_expired(
    repo: SqlTradeCooldownRepository,
) -> None:
    """AC2 — a row expiring *exactly at* now is expired (inclusive ``<=``).

    Non-tautological: with the same row, ``now`` one microsecond earlier still
    sees it as live, so flipping the comparison flips the outcome.
    """
    await repo.upsert(_cooldown("111", expires_at=NOW))

    # Exactly at the boundary: expired (TTL elapsed at ``now``).
    assert await repo.get(GUILD_ID, "111", now=NOW) is None
    # A hair before the boundary: still live.
    just_before = NOW - timedelta(microseconds=1)
    live = await repo.get(GUILD_ID, "111", now=just_before)
    assert live is not None
    assert live.expires_at == NOW


async def test_get_missing_returns_none(repo: SqlTradeCooldownRepository) -> None:
    """AC2 — a missing ``(guild_id, user_id)`` maps to ``None``."""
    assert await repo.get(GUILD_ID, "nope", now=NOW) is None


async def test_list_all_includes_expired(repo: SqlTradeCooldownRepository) -> None:
    """AC2 — ``list_all`` surfaces every row in the guild, expired included."""
    await repo.upsert(_cooldown("live", expires_at=NOW + timedelta(minutes=10)))
    await repo.upsert(_cooldown("dead", expires_at=NOW - timedelta(minutes=10)))
    await repo.upsert(
        _cooldown("111", guild_id="other-guild", expires_at=NOW + timedelta(minutes=1))
    )

    rows = await repo.list_all(GUILD_ID)

    assert {c.user_id for c in rows} == {"live", "dead"}


# ---------------------------------------------------------------------------
# AC2 — purge_expired: bulk delete of expired rows, count returned
# ---------------------------------------------------------------------------


async def test_purge_expired_removes_only_expired(
    repo: SqlTradeCooldownRepository, session: AsyncSession
) -> None:
    """AC2 — ``purge_expired`` deletes ``expires_at <= now`` rows, keeps live ones."""
    await repo.upsert(_cooldown("live", expires_at=NOW + timedelta(minutes=10)))
    await repo.upsert(_cooldown("dead1", expires_at=NOW - timedelta(minutes=1)))
    await repo.upsert(_cooldown("dead2", expires_at=NOW))  # exactly at boundary
    await repo.upsert(
        _cooldown("cross", guild_id="other-guild", expires_at=NOW - timedelta(hours=1))
    )

    removed = await repo.purge_expired(NOW)

    # Both this-guild expired rows plus the cross-guild expired row (unscoped).
    assert removed == 3
    remaining = (
        await session.execute(select(func.count()).select_from(TradeCooldownORM))
    ).scalar_one()
    assert int(remaining) == 1
    assert await repo.get(GUILD_ID, "live", now=NOW) is not None


async def test_purge_expired_boundary_is_inclusive(
    repo: SqlTradeCooldownRepository,
) -> None:
    """AC2 — a row expiring exactly at ``now`` is purged (``<=``), pinned.

    Non-tautological: a row a hair after ``now`` survives the same purge.
    """
    await repo.upsert(_cooldown("at_now", expires_at=NOW))
    await repo.upsert(_cooldown("after", expires_at=NOW + timedelta(microseconds=1)))

    removed = await repo.purge_expired(NOW)

    assert removed == 1
    assert await repo.get(GUILD_ID, "at_now", now=NOW) is None
    survivor = await repo.list_all(GUILD_ID)
    assert {c.user_id for c in survivor} == {"after"}


# ---------------------------------------------------------------------------
# AC2 — delete
# ---------------------------------------------------------------------------


async def test_delete_removes_cooldown(repo: SqlTradeCooldownRepository) -> None:
    """AC2 — ``delete`` removes the row regardless of expiry."""
    await repo.upsert(_cooldown("111"))

    await repo.delete(GUILD_ID, "111")

    assert await repo.list_all(GUILD_ID) == []


async def test_delete_missing_cooldown_is_noop(
    repo: SqlTradeCooldownRepository,
) -> None:
    """AC2 — deleting an absent cooldown does not raise."""
    await repo.delete(GUILD_ID, "ghost")
    assert await repo.list_all(GUILD_ID) == []
