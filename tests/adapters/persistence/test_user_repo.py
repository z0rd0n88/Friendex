"""Tests for :class:`SqlUserRepository` — the user-aggregate persistence port.

These exercise the SQLAlchemy-backed adapter end-to-end against an in-memory
SQLite engine that has FK enforcement ON (ADR-0002), proving three things the
unit promises:

* **Structural conformance** — ``SqlUserRepository`` satisfies the
  :class:`~friendex.application.interfaces.IUserRepo` Protocol *by shape*, not by
  inheritance (mypy gates the typed assignment in
  :func:`test_satisfies_iuserrepo_protocol`).
* **Full-aggregate round trip** — a ``UserAccount`` carrying long positions,
  short positions, *both* activity buckets, and voice channels persists and
  rebuilds with exact Decimal quantisation (checked via ``as_tuple().exponent``
  on the money fields, per the 6a convention) and tz-aware UTC datetimes.
* **Deletion cascade (the keystone)** — ``delete`` removes the user *and* every
  child row via the DB-level ``ON DELETE CASCADE``, leaving no orphans. This
  genuinely exercises the PRAGMA + CASCADE wiring: with FK enforcement off it
  would leave dangling children.

The fixture pattern (shared in-memory engine, ``AsyncSession``) mirrors
``test_orm.py`` so the two read coherently.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from friendex.adapters.persistence.db import Base, build_engine, build_sessionmaker
from friendex.adapters.persistence.orm import (
    ActivityBucketORM,
    LongPositionORM,
    ShortPositionORM,
    UserORM,
    VoiceUniqueChannelORM,
)
from friendex.adapters.persistence.user_repo import SqlUserRepository
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    LongPosition,
    ShortPosition,
    UserAccount,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from sqlalchemy.ext.asyncio import AsyncEngine

    from friendex.application.interfaces import IUserRepo

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
async def repo(engine: AsyncEngine) -> SqlUserRepository:
    """A repository bound to the in-memory engine's sessionmaker."""
    return SqlUserRepository(build_sessionmaker(engine))


def _utc(year: int, month: int, day: int, hour: int = 12) -> datetime:
    return datetime(year, month, day, hour, 30, 15, tzinfo=UTC)


def _same_scale(actual: Decimal, expected: Decimal) -> bool:
    """True when ``actual`` has the same quantisation exponent as ``expected``."""
    return actual.as_tuple().exponent == expected.as_tuple().exponent


def _rich_account(user_id: str = "111") -> UserAccount:
    """A user aggregate populated across every child table."""
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("9876.54"),
        net_worth=Decimal("12500.00"),
        month_start_net_worth=Decimal("9500.00"),
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
                frozen=True,
            ),
        },
        today=ActivityBucket(
            text_msgs=12,
            media_msgs=4,
            voice_minutes=37.5,
            voice_unique_channels=["c1", "c2"],
            reaction_count=8,
            reply_count=2,
            role_ping_joins=1.0,
            role_ping_join_minutes=20.0,
            bucket_start=_utc(2026, 5, 23, 0),
        ),
        week=ActivityBucket(
            text_msgs=80,
            media_msgs=20,
            voice_minutes=300.0,
            voice_unique_channels=["c3"],
            reaction_count=40,
            reply_count=15,
            role_ping_joins=4.0,
            role_ping_join_minutes=120.0,
            bucket_start=_utc(2026, 5, 18, 0),
        ),
        daily=DailyProgress(last_claim=_utc(2026, 5, 22, 6), streak=3),
        last_activity=_utc(2026, 5, 23, 11),
        opt_in=True,
        intro_shown=False,
    )


@contextlib.contextmanager
def _count_selects(engine: AsyncEngine) -> Iterator[list[int]]:
    """Count ``SELECT`` statements emitted on ``engine`` within the block.

    Yields a one-element list whose value is updated as cursors execute, so the
    caller reads the final tally *after* exiting the block. Only ``SELECT``s are
    counted (the read fan-out we are bounding); ``PRAGMA``/``INSERT``/``DELETE``
    are ignored.
    """
    tally = [0]

    def _on_execute(
        _conn: object,
        _cursor: object,
        statement: str,
        _params: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            tally[0] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _on_execute)
    try:
        yield tally
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _on_execute)


# Upper bound on SELECTs per list call, independent of the number of users:
# 1 parent + long + short + bucket + voice = 5. The guard keeps headroom at 6
# so the assertion catches the old ~5N+1 fan-out, not incidental +1 drift.
_MAX_LIST_SELECTS = 6


# ---------------------------------------------------------------------------
# AC1 — structural conformance to the IUserRepo Protocol
# ---------------------------------------------------------------------------


def test_satisfies_iuserrepo_protocol(repo: SqlUserRepository) -> None:
    """AC1 — ``SqlUserRepository`` conforms to ``IUserRepo`` by shape (no ABC).

    The typed assignment is what mypy checks; the runtime ``hasattr`` sweep keeps
    the test meaningful even when run without the type checker.
    """
    conforming: IUserRepo = repo
    assert conforming is repo
    for method in ("get", "upsert", "delete", "list_all", "list_active_in_last"):
        assert callable(getattr(repo, method))


# ---------------------------------------------------------------------------
# AC2 — full-aggregate round trip
# ---------------------------------------------------------------------------


async def test_upsert_then_get_round_trips_full_aggregate(
    repo: SqlUserRepository,
) -> None:
    """AC2 — persist a fully-populated aggregate and read it back equal."""
    account = _rich_account("111")

    await repo.upsert(GUILD_ID, account)
    result = await repo.get(GUILD_ID, "111")

    assert result is not None
    # Whole-aggregate equality covers scalars, both position dicts, and both
    # buckets (with their voice-channel lists).
    assert result == account

    # Decimal exactness + quantisation on the money fields (6a convention).
    assert result.cash_balance == Decimal("9876.54")
    assert isinstance(result.cash_balance, Decimal)
    assert _same_scale(result.cash_balance, Decimal("9876.54"))
    assert _same_scale(result.net_worth, Decimal("12500.00"))
    assert _same_scale(result.month_start_net_worth, Decimal("9500.00"))
    assert _same_scale(result.long_positions["aaa"].avg_entry, Decimal("80.00"))
    assert _same_scale(result.long_positions["bbb"].avg_entry, Decimal("150.50"))
    short = result.short_positions["ccc"]
    assert _same_scale(short.entry_price, Decimal("90.00"))
    assert _same_scale(short.locked_cash, Decimal("180.00"))
    assert _same_scale(short.locked_fund, Decimal("0.00"))

    # Datetimes survive as tz-aware UTC.
    assert result.last_activity == _utc(2026, 5, 23, 11)
    assert result.last_activity.tzinfo is not None
    assert result.daily.last_claim is not None
    assert result.daily.last_claim.tzinfo is not None
    assert short.created_at.tzinfo is not None
    assert result.today.bucket_start.tzinfo is not None
    assert result.week.bucket_start.tzinfo is not None

    # Both buckets, including voice channels, came back intact.
    assert result.today.voice_unique_channels == ["c1", "c2"]
    assert result.week.voice_unique_channels == ["c3"]
    assert result.today.text_msgs == 12
    assert result.week.text_msgs == 80


async def test_get_missing_returns_none(repo: SqlUserRepository) -> None:
    """AC2 — a missing ``(guild_id, user_id)`` maps to ``None``."""
    assert await repo.get(GUILD_ID, "nope") is None


async def test_upsert_replaces_existing_aggregate(repo: SqlUserRepository) -> None:
    """AC2 — re-``upsert`` overwrites scalars and replaces children wholesale."""
    await repo.upsert(GUILD_ID, _rich_account("111"))

    replacement = UserAccount(
        user_id="111",
        cash_balance=Decimal("100.00"),
        net_worth=Decimal("100.00"),
        month_start_net_worth=Decimal("100.00"),
        long_positions={"zzz": LongPosition("zzz", 1, Decimal("70.00"))},
        short_positions={},
        today=ActivityBucket(bucket_start=_utc(2026, 5, 24, 0)),
        week=ActivityBucket(bucket_start=_utc(2026, 5, 18, 0)),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=_utc(2026, 5, 24, 9),
    )
    await repo.upsert(GUILD_ID, replacement)

    result = await repo.get(GUILD_ID, "111")
    assert result == replacement
    # Stale children from the first aggregate are gone.
    assert list(result.long_positions) == ["zzz"]  # type: ignore[union-attr]
    assert result.short_positions == {}  # type: ignore[union-attr]
    assert result.today.voice_unique_channels == []  # type: ignore[union-attr]


async def test_list_all_returns_every_account_in_guild(repo: SqlUserRepository) -> None:
    """AC2 — ``list_all`` scopes to one guild and rebuilds each aggregate."""
    await repo.upsert(GUILD_ID, _rich_account("111"))
    await repo.upsert(GUILD_ID, _rich_account("222"))
    await repo.upsert("other-guild", _rich_account("111"))

    accounts = await repo.list_all(GUILD_ID)

    assert {a.user_id for a in accounts} == {"111", "222"}
    # Children rebuilt for a listed account, not just scalars.
    listed = next(a for a in accounts if a.user_id == "111")
    assert listed.long_positions == _rich_account("111").long_positions


async def test_list_active_in_last_filters_by_recency(repo: SqlUserRepository) -> None:
    """AC2 — ``list_active_in_last`` returns only recently-active accounts."""
    now = datetime.now(tz=UTC)
    recent = _rich_account("recent")
    recent.last_activity = now - timedelta(minutes=5)
    stale = _rich_account("stale")
    stale.last_activity = now - timedelta(hours=10)

    await repo.upsert(GUILD_ID, recent)
    await repo.upsert(GUILD_ID, stale)

    active = await repo.list_active_in_last(GUILD_ID, seconds=3600)

    assert {a.user_id for a in active} == {"recent"}


# ---------------------------------------------------------------------------
# AC3 — deletion cascade (the keystone)
# ---------------------------------------------------------------------------


async def _child_counts(session: AsyncSession, user_id: str) -> dict[str, int]:
    """Count remaining child rows for ``user_id`` across every child table."""

    async def _count(model: type, *wheres: object) -> int:
        stmt = select(func.count()).select_from(model).where(*wheres)
        return int((await session.execute(stmt)).scalar_one())

    return {
        "users": await _count(
            UserORM, UserORM.guild_id == GUILD_ID, UserORM.user_id == user_id
        ),
        "long_positions": await _count(
            LongPositionORM,
            LongPositionORM.guild_id == GUILD_ID,
            LongPositionORM.owner_id == user_id,
        ),
        "short_positions": await _count(
            ShortPositionORM,
            ShortPositionORM.guild_id == GUILD_ID,
            ShortPositionORM.owner_id == user_id,
        ),
        "activity_buckets": await _count(
            ActivityBucketORM,
            ActivityBucketORM.guild_id == GUILD_ID,
            ActivityBucketORM.user_id == user_id,
        ),
        "voice_unique_channels": await _count(
            VoiceUniqueChannelORM,
            VoiceUniqueChannelORM.guild_id == GUILD_ID,
            VoiceUniqueChannelORM.user_id == user_id,
        ),
    }


async def test_delete_cascades_to_all_children(
    repo: SqlUserRepository, session: AsyncSession
) -> None:
    """AC3 — ``delete`` removes the user and cascades to every child table.

    With FK enforcement off this would leave orphaned position / bucket / voice
    rows; asserting zero across all five tables proves the PRAGMA + ON DELETE
    CASCADE wiring fires through the repository.
    """
    account = _rich_account("victim")
    await repo.upsert(GUILD_ID, account)

    # Sanity — children genuinely exist before the delete (else the test is
    # vacuously green).
    before = await _child_counts(session, "victim")
    assert before["users"] == 1
    assert before["long_positions"] == 2
    assert before["short_positions"] == 1
    assert before["activity_buckets"] == 2
    assert before["voice_unique_channels"] == 3  # c1, c2 (today) + c3 (week)

    await repo.delete(GUILD_ID, "victim")

    after = await _child_counts(session, "victim")
    assert after == {
        "users": 0,
        "long_positions": 0,
        "short_positions": 0,
        "activity_buckets": 0,
        "voice_unique_channels": 0,
    }
    assert await repo.get(GUILD_ID, "victim") is None


async def test_delete_only_affects_target_user(repo: SqlUserRepository) -> None:
    """AC3 — deleting one user leaves another user's aggregate untouched."""
    await repo.upsert(GUILD_ID, _rich_account("keep"))
    await repo.upsert(GUILD_ID, _rich_account("drop"))

    await repo.delete(GUILD_ID, "drop")

    survivor = await repo.get(GUILD_ID, "keep")
    assert survivor is not None
    assert survivor == _rich_account("keep")
    assert await repo.get(GUILD_ID, "drop") is None


async def test_delete_missing_user_is_noop(repo: SqlUserRepository) -> None:
    """AC3 — deleting an absent user does not raise."""
    await repo.delete(GUILD_ID, "ghost")
    assert await repo.get(GUILD_ID, "ghost") is None


# ---------------------------------------------------------------------------
# H1 — list_all / list_active_in_last issue a bounded number of SELECTs
# ---------------------------------------------------------------------------


async def test_list_all_query_count_is_bounded(
    repo: SqlUserRepository, engine: AsyncEngine
) -> None:
    """H1 — ``list_all`` issues O(1) SELECTs, not O(N) per-user fan-out.

    The pre-fix implementation rebuilt each row with 3-5 child SELECTs, so N
    users cost ~5N+1 queries. Batching the children into one ``IN`` query per
    table must keep the count flat as N grows.
    """
    for n in range(4):
        await repo.upsert(GUILD_ID, _rich_account(f"u{n}"))

    with _count_selects(engine) as tally:
        accounts = await repo.list_all(GUILD_ID)

    assert len(accounts) == 4
    assert tally[0] <= _MAX_LIST_SELECTS, (
        f"list_all over 4 users issued {tally[0]} SELECTs; "
        f"expected <= {_MAX_LIST_SELECTS} (constant, not per-user)"
    )


async def test_list_active_in_last_query_count_is_bounded(
    repo: SqlUserRepository, engine: AsyncEngine
) -> None:
    """H1 — ``list_active_in_last`` (activity-tick hot path) is O(1) in SELECTs."""
    now = datetime.now(tz=UTC)
    for n in range(4):
        acct = _rich_account(f"u{n}")
        acct.last_activity = now - timedelta(minutes=5)
        await repo.upsert(GUILD_ID, acct)

    with _count_selects(engine) as tally:
        accounts = await repo.list_active_in_last(GUILD_ID, seconds=3600)

    assert len(accounts) == 4
    assert tally[0] <= _MAX_LIST_SELECTS, (
        f"list_active_in_last over 4 users issued {tally[0]} SELECTs; "
        f"expected <= {_MAX_LIST_SELECTS} (constant, not per-user)"
    )


async def test_list_all_empty_guild_returns_empty(repo: SqlUserRepository) -> None:
    """H1 — an empty guild lists nothing without an ``IN ()`` error."""
    assert await repo.list_all(GUILD_ID) == []


async def test_list_all_voice_channels_have_deterministic_order(
    repo: SqlUserRepository,
) -> None:
    """LOW — batched voice-channel load returns channels in a stable order.

    Inserted out of natural sort order to prove the ``ORDER BY channel_id`` is
    what fixes the order, not insertion/rowid luck.
    """
    account = _rich_account("ordered")
    account.today = ActivityBucket(
        voice_unique_channels=["c3", "c1", "c2"],
        bucket_start=_utc(2026, 5, 23, 0),
    )
    await repo.upsert(GUILD_ID, account)

    listed = next(a for a in await repo.list_all(GUILD_ID) if a.user_id == "ordered")

    assert listed.today.voice_unique_channels == ["c1", "c2", "c3"]


# ---------------------------------------------------------------------------
# H9 — SQLite IN-clause chunking for large guilds (≥999 opted-in users)
# ---------------------------------------------------------------------------


def _minimal_account(user_id: str) -> UserAccount:
    """A cheap, child-free aggregate so we can fan out to thousands quickly.

    The chunking test pivots on the number of bound variables in the child
    ``IN (...)`` queries (capped at SQLite's 999 per statement); the populated
    aggregate state is incidental, so empty positions/buckets keep insert cost
    low and the failure mode obvious.
    """
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=_utc(2026, 5, 23, 0)),
        week=ActivityBucket(bucket_start=_utc(2026, 5, 18, 0)),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=_utc(2026, 5, 23, 11),
    )


async def test_list_all_handles_exactly_1000_users(repo: SqlUserRepository) -> None:
    """H9 — ``list_all`` over 1000 users returns the full set.

    Smoke-level correctness on a large guild: a thousand users round-trips
    through ``_rebuild_many`` and every aggregate comes back. (The 999-bound
    cap is enforced per-connection by SQLite ≤3.31 via
    ``SQLITE_MAX_VARIABLE_NUMBER``; newer SQLite raised the default to 32766,
    so this test alone does not exercise the chunking — see
    ``test_list_all_chunks_in_clause_below_bind_cap`` for the chunk-boundary
    proof.)
    """
    user_ids = [f"u{n:04d}" for n in range(1000)]
    for uid in user_ids:
        await repo.upsert(GUILD_ID, _minimal_account(uid))

    accounts = await repo.list_all(GUILD_ID)

    assert len(accounts) == 1000
    assert {a.user_id for a in accounts} == set(user_ids)


async def test_list_active_in_last_handles_1500_users(repo: SqlUserRepository) -> None:
    """H9 — well past the 999 cap, the hot path still returns every active user.

    The activity-tick loop hits ``list_active_in_last`` every 15 minutes, so a
    silent crash here freezes price evolution. 1500 users exercise the same
    scale-out path as ``test_list_all_handles_exactly_1000_users``.
    """
    now = datetime.now(tz=UTC)
    user_ids = [f"u{n:04d}" for n in range(1500)]
    for uid in user_ids:
        account = _minimal_account(uid)
        account.last_activity = now - timedelta(minutes=5)
        await repo.upsert(GUILD_ID, account)

    accounts = await repo.list_active_in_last(GUILD_ID, seconds=3600)

    assert len(accounts) == 1500
    assert {a.user_id for a in accounts} == set(user_ids)


async def test_list_all_chunks_in_clause_below_bind_cap(
    repo: SqlUserRepository,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H9 — child ``IN (...)`` queries are split into chunks of ≤chunk-size.

    SQLite ≥3.32 raised ``SQLITE_MAX_VARIABLE_NUMBER`` to 32766, so a test
    against the in-memory engine cannot trigger the pre-fix
    ``OperationalError: too many SQL variables`` directly. Instead, we shrink
    the repository's chunk size to a number well below the user count and
    verify two observable consequences: the result is still correct (every
    user comes back, no chunk dropped on the seam) and the child-table reads
    issue *multiple* parameterised statements per child table — the very
    behaviour that keeps the call working on older SQLite where the cap is
    still 999.
    """
    user_ids = [f"u{n:04d}" for n in range(25)]
    for uid in user_ids:
        await repo.upsert(GUILD_ID, _minimal_account(uid))

    # Force chunking even at a tiny user count. After the fix this attribute
    # is the public knob; before the fix it does not exist, and the test
    # surfaces that gap.
    import friendex.adapters.persistence.user_repo as user_repo_module

    if not hasattr(user_repo_module, "_IN_CLAUSE_CHUNK_SIZE"):
        pytest.fail(
            "user_repo._IN_CLAUSE_CHUNK_SIZE is missing; H9 chunking is not in place"
        )
    monkeypatch.setattr(user_repo_module, "_IN_CLAUSE_CHUNK_SIZE", 10)

    with _count_selects(engine) as tally:
        accounts = await repo.list_all(GUILD_ID)

    # Correctness: every user comes back, including the tail that crosses the
    # final chunk boundary (25 = 10 + 10 + 5).
    assert len(accounts) == 25
    assert {a.user_id for a in accounts} == set(user_ids)
    # Behaviour: with a chunk size of 10 over 25 ids, each of the 4 child
    # tables (long, short, bucket, voice) is queried 3 times — plus the
    # single parent query. The exact tally proves chunks are *being* issued
    # and never accidentally short-circuited into one big IN.
    expected_minimum = 1 + 3 * 4
    assert tally[0] >= expected_minimum, (
        f"list_all over 25 users with chunk size 10 issued {tally[0]} SELECTs; "
        f"expected at least {expected_minimum} (parent + chunks per child table)"
    )


# ---------------------------------------------------------------------------
# H10 — explicit flush after merge() pins the merge→delete→insert ordering
# ---------------------------------------------------------------------------


async def test_upsert_replace_survives_autoflush_disabled(
    engine: AsyncEngine,
) -> None:
    """H10 — ``upsert`` must work even when the session's ``autoflush=False``.

    The implementation calls ``session.merge(parent)`` then ``DELETE`` children
    then re-inserts. With autoflush on, the merged parent is implicitly flushed
    before the ``DELETE`` so the FK target exists. Flipping autoflush off
    (a future hardening sweep, or any session-config drift) silently breaks
    that ordering — the explicit ``await session.flush()`` after ``merge()``
    keeps the contract independent of the default.
    """
    autoflush_off = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    repo = SqlUserRepository(autoflush_off)

    # First insert (creates the parent + children).
    await repo.upsert(GUILD_ID, _rich_account("alice"))

    # Replace the same aggregate; the second upsert is where the ordering bites:
    # ``merge`` stages the parent, ``DELETE`` must see it, then the re-insert
    # of child rows must satisfy the FK back to the (now-merged) parent.
    replacement = UserAccount(
        user_id="alice",
        cash_balance=Decimal("250.00"),
        net_worth=Decimal("250.00"),
        month_start_net_worth=Decimal("250.00"),
        long_positions={"zzz": LongPosition("zzz", 1, Decimal("70.00"))},
        short_positions={},
        today=ActivityBucket(bucket_start=_utc(2026, 5, 24, 0)),
        week=ActivityBucket(bucket_start=_utc(2026, 5, 18, 0)),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=_utc(2026, 5, 24, 9),
    )

    await repo.upsert(GUILD_ID, replacement)

    loaded = await repo.get(GUILD_ID, "alice")
    assert loaded == replacement
