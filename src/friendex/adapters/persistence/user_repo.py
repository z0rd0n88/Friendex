"""SQLAlchemy-backed :class:`IUserRepo` adapter for the user aggregate.

``SqlUserRepository`` persists and rebuilds a whole :class:`UserAccount` — its
scalar row (``users``) plus the four child tables it owns: long positions, short
positions, the ``today`` / ``week`` activity buckets, and the per-bucket voice
channels. It conforms to
:class:`~friendex.application.interfaces.IUserRepo` *structurally* (Protocol
duck-typing); it deliberately does **not** inherit from it, keeping the
dependency arrow pointing inward (``adapters -> application -> domain``).

**Aggregate persistence.** :meth:`upsert` is an idempotent
delete-then-insert of the whole aggregate inside one transaction: the scalar row
is ``merge``d, then all owned children are deleted and re-inserted from the
domain object. This keeps the mapping a pure function of the aggregate (no diff
logic) and guarantees stale children never linger.

**Deletion.** :meth:`delete` issues a single ``DELETE`` of the parent row and
relies on the DB-level ``ON DELETE CASCADE`` (ADR-0002, enforced by the
``PRAGMA foreign_keys=ON`` listener in :mod:`db`) to remove every child — no
hand-rolled child cleanup.

**Invariants preserved.** Money stays :class:`~decimal.Decimal` (exact value and
quantisation, via ``DecimalText``) and datetimes stay tz-aware UTC (via
``UtcDateTime``) across the boundary; the mapper builds fresh domain objects and
never mutates the loaded rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TypeVar

from sqlalchemy import delete, select

from friendex.adapters.persistence.orm import (
    ActivityBucketORM,
    LongPositionORM,
    ShortPositionORM,
    UserORM,
    VoiceUniqueChannelORM,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.sql._typing import ColumnExpressionArgument

    from friendex.domain.models import ActivityBucket, UserAccount

_ChildT = TypeVar("_ChildT")

# The two activity buckets an account owns, by their ``bucket_type`` discriminator.
_TODAY = "today"
_WEEK = "week"


class SqlUserRepository:
    """Persist :class:`UserAccount` aggregates via async SQLAlchemy.

    Constructed with an :class:`async_sessionmaker`; each public method opens a
    short-lived session so callers never share session state across operations
    (matching the per-call transaction boundary the repository contract implies).
    """

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def get(self, guild_id: str, user_id: str) -> UserAccount | None:
        """Return the account for ``(guild_id, user_id)`` or ``None``."""
        async with self._sessionmaker() as session:
            row = await self._load_user_row(session, guild_id, user_id)
            if row is None:
                return None
            return await self._rebuild(session, row)

    async def upsert(self, guild_id: str, account: UserAccount) -> None:
        """Insert or replace ``account`` (and all its children) under ``guild_id``."""
        async with self._sessionmaker() as session:
            await session.merge(UserORM.from_domain(guild_id, account))
            await self._delete_children(session, guild_id, account.user_id)
            self._insert_children(session, guild_id, account)
            await session.commit()

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the account; children cascade at the DB level (ADR-0002)."""
        async with self._sessionmaker() as session:
            await session.execute(
                delete(UserORM).where(
                    UserORM.guild_id == guild_id, UserORM.user_id == user_id
                )
            )
            await session.commit()

    async def list_all(self, guild_id: str) -> list[UserAccount]:
        """Return every account in ``guild_id``, each fully rebuilt."""
        async with self._sessionmaker() as session:
            rows = (
                (
                    await session.execute(
                        select(UserORM).where(UserORM.guild_id == guild_id)
                    )
                )
                .scalars()
                .all()
            )
            return [await self._rebuild(session, row) for row in rows]

    async def list_active_in_last(
        self, guild_id: str, seconds: float
    ) -> list[UserAccount]:
        """Return accounts whose ``last_activity`` is within ``seconds`` of now."""
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=seconds)
        async with self._sessionmaker() as session:
            rows = (
                (
                    await session.execute(
                        select(UserORM).where(
                            UserORM.guild_id == guild_id,
                            UserORM.last_activity >= cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
            return [await self._rebuild(session, row) for row in rows]

    # -- internal helpers ---------------------------------------------------

    @staticmethod
    async def _load_user_row(
        session: AsyncSession, guild_id: str, user_id: str
    ) -> UserORM | None:
        return (
            await session.execute(
                select(UserORM).where(
                    UserORM.guild_id == guild_id, UserORM.user_id == user_id
                )
            )
        ).scalar_one_or_none()

    async def _rebuild(self, session: AsyncSession, row: UserORM) -> UserAccount:
        """Build a :class:`UserAccount` from a user row plus its loaded children."""
        guild_id, user_id = row.guild_id, row.user_id

        long_rows = await self._children(
            session,
            LongPositionORM,
            LongPositionORM.guild_id == guild_id,
            LongPositionORM.owner_id == user_id,
        )
        short_rows = await self._children(
            session,
            ShortPositionORM,
            ShortPositionORM.guild_id == guild_id,
            ShortPositionORM.owner_id == user_id,
        )
        bucket_rows = await self._children(
            session,
            ActivityBucketORM,
            ActivityBucketORM.guild_id == guild_id,
            ActivityBucketORM.user_id == user_id,
        )

        longs = {r.to_domain().target_user_id: r.to_domain() for r in long_rows}
        shorts = {r.to_domain().target_user_id: r.to_domain() for r in short_rows}
        buckets = {r.bucket_type: r for r in bucket_rows}

        today = await self._rebuild_bucket(session, guild_id, user_id, buckets, _TODAY)
        week = await self._rebuild_bucket(session, guild_id, user_id, buckets, _WEEK)

        return row.to_domain(
            long_positions=longs,
            short_positions=shorts,
            today=today,
            week=week,
        )

    async def _rebuild_bucket(
        self,
        session: AsyncSession,
        guild_id: str,
        user_id: str,
        buckets: dict[str, ActivityBucketORM],
        bucket_type: str,
    ) -> ActivityBucket | None:
        """Map one bucket row + its voice channels, or ``None`` if absent."""
        bucket_row = buckets.get(bucket_type)
        if bucket_row is None:
            return None
        channel_rows = await self._children(
            session,
            VoiceUniqueChannelORM,
            VoiceUniqueChannelORM.guild_id == guild_id,
            VoiceUniqueChannelORM.user_id == user_id,
            VoiceUniqueChannelORM.bucket_type == bucket_type,
        )
        channels = [r.channel_id for r in channel_rows]
        return bucket_row.to_domain(channels)

    @staticmethod
    def _insert_children(
        session: AsyncSession, guild_id: str, account: UserAccount
    ) -> None:
        """Stage every owned child row for ``account`` for insertion."""
        user_id = account.user_id
        session.add_all(
            LongPositionORM.from_domain(guild_id, user_id, position)
            for position in account.long_positions.values()
        )
        session.add_all(
            ShortPositionORM.from_domain(guild_id, user_id, position)
            for position in account.short_positions.values()
        )
        for bucket_type, bucket in ((_TODAY, account.today), (_WEEK, account.week)):
            session.add(
                ActivityBucketORM.from_domain(guild_id, user_id, bucket_type, bucket)
            )
            session.add_all(
                VoiceUniqueChannelORM.from_domain(
                    guild_id, user_id, bucket_type, channel
                )
                for channel in bucket.voice_unique_channels
            )

    @staticmethod
    async def _delete_children(
        session: AsyncSession, guild_id: str, user_id: str
    ) -> None:
        """Delete all owned child rows for a user ahead of a re-insert.

        Voice channels cascade from their bucket, but they are deleted
        explicitly first so the ordering is correct even were CASCADE absent,
        and to keep the operation independent of FK timing within one flush.
        """
        await session.execute(
            delete(VoiceUniqueChannelORM).where(
                VoiceUniqueChannelORM.guild_id == guild_id,
                VoiceUniqueChannelORM.user_id == user_id,
            )
        )
        await session.execute(
            delete(ActivityBucketORM).where(
                ActivityBucketORM.guild_id == guild_id,
                ActivityBucketORM.user_id == user_id,
            )
        )
        await session.execute(
            delete(LongPositionORM).where(
                LongPositionORM.guild_id == guild_id,
                LongPositionORM.owner_id == user_id,
            )
        )
        await session.execute(
            delete(ShortPositionORM).where(
                ShortPositionORM.guild_id == guild_id,
                ShortPositionORM.owner_id == user_id,
            )
        )

    @staticmethod
    async def _children(
        session: AsyncSession,
        model: type[_ChildT],
        *wheres: ColumnExpressionArgument[bool],
    ) -> list[_ChildT]:
        """Load and return every child row of ``model`` matching ``wheres``."""
        return list(
            (await session.execute(select(model).where(*wheres))).scalars().all()
        )
