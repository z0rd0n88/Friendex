"""SQLAlchemy 2.0 declarative ORM mirrors of the domain models.

One ORM class per table in the §Persistence Strategy "Option B" schema
(``docs/02-target-architecture.md``), extended with the ``guild_id`` dimension
mandated by ADR-0001 (per-guild markets): every per-guild table carries a
``guild_id`` first column in its composite primary key, so the same
``user_id`` coexists across guilds as distinct rows.

**Mapper placement.** Each class carries its ``from_domain(...)`` /
``to_domain(...)`` helpers right next to the columns it maps, so repositories
(Phase 6) stay thin. Per ADR-0001 the domain dataclasses are guild-agnostic:
``from_domain(guild_id, obj)`` *attaches* the guild scope and ``to_domain()``
*drops* it.

**Round-trip invariants** (enforced via the custom column types in
``types.py``):

* Money / price columns use :class:`DecimalText` — ``Decimal`` in, equal
  ``Decimal`` out, quantisation preserved.
* Datetime columns use :class:`UtcDateTime` — tz-aware UTC in, tz-aware UTC
  out.
* Collections (``ActivityBucket.voice_unique_channels``,
  ``HedgeFund.investors``, ``Stock.history``) live in normalised child tables,
  never as serialised blobs; their owning aggregate's ``to_domain`` takes the
  loaded children as an argument so the mapper stays a pure function.
"""

from __future__ import annotations

# NB: `datetime` and `Decimal` are imported at runtime (not under
# TYPE_CHECKING) because SQLAlchemy resolves the `Mapped[...]` annotations at
# class-construction time — deferring them breaks mapping with a
# MappedAnnotationError. The TC003 lint is therefore a false positive here.
from datetime import datetime  # noqa: TC003
from decimal import Decimal  # noqa: TC003

from sqlalchemy import ForeignKeyConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from friendex.adapters.persistence.db import Base
from friendex.adapters.persistence.types import DecimalText, UtcDateTime
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


class UserORM(Base):
    """A user's account scalars; positions and activity live in child tables."""

    __tablename__ = "users"

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    cash_balance: Mapped[Decimal] = mapped_column(DecimalText)
    net_worth: Mapped[Decimal] = mapped_column(DecimalText)
    month_start_net_worth: Mapped[Decimal] = mapped_column(DecimalText)
    last_activity: Mapped[datetime] = mapped_column(UtcDateTime)
    opt_in: Mapped[bool] = mapped_column(default=True)
    intro_shown: Mapped[bool] = mapped_column(default=False)
    daily_last_claim: Mapped[datetime | None] = mapped_column(UtcDateTime)
    daily_streak: Mapped[int] = mapped_column(default=0)

    @classmethod
    def from_domain(cls, guild_id: str, account: UserAccount) -> UserORM:
        return cls(
            guild_id=guild_id,
            user_id=account.user_id,
            cash_balance=account.cash_balance,
            net_worth=account.net_worth,
            month_start_net_worth=account.month_start_net_worth,
            last_activity=account.last_activity,
            opt_in=account.opt_in,
            intro_shown=account.intro_shown,
            daily_last_claim=account.daily.last_claim,
            daily_streak=account.daily.streak,
        )

    def to_domain(
        self,
        *,
        long_positions: dict[str, LongPosition] | None = None,
        short_positions: dict[str, ShortPosition] | None = None,
        today: ActivityBucket | None = None,
        week: ActivityBucket | None = None,
    ) -> UserAccount:
        """Rebuild a :class:`UserAccount`.

        Child collections default to empty / fresh buckets so a bare row maps
        cleanly; the repository passes the loaded children when present.
        """
        return UserAccount(
            user_id=self.user_id,
            cash_balance=self.cash_balance,
            net_worth=self.net_worth,
            month_start_net_worth=self.month_start_net_worth,
            long_positions=long_positions if long_positions is not None else {},
            short_positions=short_positions if short_positions is not None else {},
            today=today if today is not None else ActivityBucket(),
            week=week if week is not None else ActivityBucket(),
            daily=DailyProgress(
                last_claim=self.daily_last_claim,
                streak=self.daily_streak,
            ),
            last_activity=self.last_activity,
            opt_in=self.opt_in,
            intro_shown=self.intro_shown,
        )


class LongPositionORM(Base):
    """A single long position, keyed by ``(guild_id, owner_id, target_id)``."""

    __tablename__ = "long_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["guild_id", "owner_id"],
            ["users.guild_id", "users.user_id"],
            ondelete="CASCADE",
        ),
    )

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(primary_key=True)
    target_id: Mapped[str] = mapped_column(primary_key=True)
    shares: Mapped[int] = mapped_column()
    avg_entry: Mapped[Decimal] = mapped_column(DecimalText)

    @classmethod
    def from_domain(
        cls, guild_id: str, owner_id: str, position: LongPosition
    ) -> LongPositionORM:
        return cls(
            guild_id=guild_id,
            owner_id=owner_id,
            target_id=position.target_user_id,
            shares=position.shares,
            avg_entry=position.avg_entry,
        )

    def to_domain(self) -> LongPosition:
        return LongPosition(
            target_user_id=self.target_id,
            shares=self.shares,
            avg_entry=self.avg_entry,
        )


class ShortPositionORM(Base):
    """A single short position, keyed by ``(guild_id, owner_id, target_id)``."""

    __tablename__ = "short_positions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["guild_id", "owner_id"],
            ["users.guild_id", "users.user_id"],
            ondelete="CASCADE",
        ),
    )

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(primary_key=True)
    target_id: Mapped[str] = mapped_column(primary_key=True)
    shares: Mapped[int] = mapped_column()
    entry_price: Mapped[Decimal] = mapped_column(DecimalText)
    locked_cash: Mapped[Decimal] = mapped_column(DecimalText)
    locked_fund: Mapped[Decimal] = mapped_column(DecimalText)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    frozen: Mapped[bool] = mapped_column(default=False)

    @classmethod
    def from_domain(
        cls, guild_id: str, owner_id: str, position: ShortPosition
    ) -> ShortPositionORM:
        return cls(
            guild_id=guild_id,
            owner_id=owner_id,
            target_id=position.target_user_id,
            shares=position.shares,
            entry_price=position.entry_price,
            locked_cash=position.locked_cash,
            locked_fund=position.locked_fund,
            created_at=position.created_at,
            frozen=position.frozen,
        )

    def to_domain(self) -> ShortPosition:
        return ShortPosition(
            target_user_id=self.target_id,
            shares=self.shares,
            entry_price=self.entry_price,
            locked_cash=self.locked_cash,
            locked_fund=self.locked_fund,
            created_at=self.created_at,
            frozen=self.frozen,
        )


class ActivityBucketORM(Base):
    """One activity bucket (``today`` or ``week``) per user per guild.

    ``voice_unique_channels`` is *not* stored here — it lives in
    :class:`VoiceUniqueChannelORM` child rows and is passed into
    :meth:`to_domain`.
    """

    __tablename__ = "activity_buckets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["guild_id", "user_id"],
            ["users.guild_id", "users.user_id"],
            ondelete="CASCADE",
        ),
    )

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    bucket_type: Mapped[str] = mapped_column(primary_key=True)
    text_msgs: Mapped[int] = mapped_column(default=0)
    media_msgs: Mapped[int] = mapped_column(default=0)
    voice_minutes: Mapped[float] = mapped_column(default=0.0)
    reaction_count: Mapped[int] = mapped_column(default=0)
    reply_count: Mapped[int] = mapped_column(default=0)
    role_ping_joins: Mapped[float] = mapped_column(default=0.0)
    role_ping_join_minutes: Mapped[float] = mapped_column(default=0.0)
    bucket_start: Mapped[datetime] = mapped_column(UtcDateTime)

    @classmethod
    def from_domain(
        cls,
        guild_id: str,
        user_id: str,
        bucket_type: str,
        bucket: ActivityBucket,
    ) -> ActivityBucketORM:
        return cls(
            guild_id=guild_id,
            user_id=user_id,
            bucket_type=bucket_type,
            text_msgs=bucket.text_msgs,
            media_msgs=bucket.media_msgs,
            voice_minutes=bucket.voice_minutes,
            reaction_count=bucket.reaction_count,
            reply_count=bucket.reply_count,
            role_ping_joins=bucket.role_ping_joins,
            role_ping_join_minutes=bucket.role_ping_join_minutes,
            bucket_start=bucket.bucket_start,
        )

    def to_domain(self, voice_unique_channels: list[str]) -> ActivityBucket:
        return ActivityBucket(
            text_msgs=self.text_msgs,
            media_msgs=self.media_msgs,
            voice_minutes=self.voice_minutes,
            voice_unique_channels=list(voice_unique_channels),
            reaction_count=self.reaction_count,
            reply_count=self.reply_count,
            role_ping_joins=self.role_ping_joins,
            role_ping_join_minutes=self.role_ping_join_minutes,
            bucket_start=self.bucket_start,
        )


class VoiceUniqueChannelORM(Base):
    """One row per unique voice channel a user visited within a bucket."""

    __tablename__ = "voice_unique_channels"
    __table_args__ = (
        ForeignKeyConstraint(
            ["guild_id", "user_id", "bucket_type"],
            [
                "activity_buckets.guild_id",
                "activity_buckets.user_id",
                "activity_buckets.bucket_type",
            ],
            ondelete="CASCADE",
        ),
    )

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    bucket_type: Mapped[str] = mapped_column(primary_key=True)
    channel_id: Mapped[str] = mapped_column(primary_key=True)

    @classmethod
    def from_domain(
        cls, guild_id: str, user_id: str, bucket_type: str, channel_id: str
    ) -> VoiceUniqueChannelORM:
        return cls(
            guild_id=guild_id,
            user_id=user_id,
            bucket_type=bucket_type,
            channel_id=channel_id,
        )


class StockORM(Base):
    """A user's stock scalars; price history lives in :class:`PriceHistoryORM`."""

    __tablename__ = "stocks"

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    current: Mapped[Decimal] = mapped_column(DecimalText)
    high_24h: Mapped[Decimal] = mapped_column(DecimalText)
    low_24h: Mapped[Decimal] = mapped_column(DecimalText)
    all_time_high: Mapped[Decimal] = mapped_column(DecimalText)

    @classmethod
    def from_domain(cls, guild_id: str, stock: Stock) -> StockORM:
        return cls(
            guild_id=guild_id,
            user_id=stock.user_id,
            current=stock.current,
            high_24h=stock.high_24h,
            low_24h=stock.low_24h,
            all_time_high=stock.all_time_high,
        )

    def to_domain(self, history: list[PricePoint]) -> Stock:
        return Stock(
            user_id=self.user_id,
            current=self.current,
            history=list(history),
            high_24h=self.high_24h,
            low_24h=self.low_24h,
            all_time_high=self.all_time_high,
        )


class PriceHistoryORM(Base):
    """A single recorded price point for a stock.

    No natural unique key beyond ``(guild_id, user_id, recorded_at)``; a
    surrogate autoincrement ``id`` keeps inserts cheap and history append-only.
    """

    __tablename__ = "price_history"
    __table_args__ = (
        ForeignKeyConstraint(
            ["guild_id", "user_id"],
            ["stocks.guild_id", "stocks.user_id"],
            ondelete="CASCADE",
        ),
        Index("ix_price_history_lookup", "guild_id", "user_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column()
    user_id: Mapped[str] = mapped_column()
    price: Mapped[Decimal] = mapped_column(DecimalText)
    recorded_at: Mapped[datetime] = mapped_column(UtcDateTime)

    @classmethod
    def from_domain(
        cls, guild_id: str, user_id: str, point: PricePoint
    ) -> PriceHistoryORM:
        return cls(
            guild_id=guild_id,
            user_id=user_id,
            price=point.price,
            recorded_at=point.timestamp,
        )

    def to_domain(self) -> PricePoint:
        return PricePoint(price=self.price, timestamp=self.recorded_at)


class HedgeFundORM(Base):
    """A hedge fund's scalars; investors live in :class:`FundInvestorORM`."""

    __tablename__ = "hedge_funds"

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    fund_id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column()
    manager_id: Mapped[str] = mapped_column()
    cash_balance: Mapped[Decimal] = mapped_column(DecimalText)

    @classmethod
    def from_domain(cls, guild_id: str, fund: HedgeFund) -> HedgeFundORM:
        return cls(
            guild_id=guild_id,
            fund_id=fund.fund_id,
            name=fund.name,
            manager_id=fund.manager_id,
            cash_balance=fund.cash_balance,
        )

    def to_domain(self, investors: dict[str, Decimal]) -> HedgeFund:
        return HedgeFund(
            fund_id=self.fund_id,
            name=self.name,
            manager_id=self.manager_id,
            cash_balance=self.cash_balance,
            investors=dict(investors),
        )


class FundInvestorORM(Base):
    """One investor's stake in a fund, keyed by ``(guild_id, fund_id, investor_id)``."""

    __tablename__ = "fund_investors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["guild_id", "fund_id"],
            ["hedge_funds.guild_id", "hedge_funds.fund_id"],
            ondelete="CASCADE",
        ),
    )

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    fund_id: Mapped[str] = mapped_column(primary_key=True)
    investor_id: Mapped[str] = mapped_column(primary_key=True)
    invested_amount: Mapped[Decimal] = mapped_column(DecimalText)

    @classmethod
    def from_domain(
        cls, guild_id: str, fund_id: str, investor_id: str, amount: Decimal
    ) -> FundInvestorORM:
        return cls(
            guild_id=guild_id,
            fund_id=fund_id,
            investor_id=investor_id,
            invested_amount=amount,
        )

    def to_amount(self) -> Decimal:
        """Return this investor's stake; the dict is rebuilt in ``HedgeFundORM``."""
        return self.invested_amount


class FundPenaltyORM(Base):
    """An early-withdrawal APY penalty, keyed by ``(guild_id, user_id)``."""

    __tablename__ = "fund_penalties"

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    penalty_apr: Mapped[Decimal] = mapped_column(DecimalText)
    penalty_until: Mapped[datetime] = mapped_column(UtcDateTime)

    @classmethod
    def from_domain(cls, guild_id: str, penalty: FundPenalty) -> FundPenaltyORM:
        return cls(
            guild_id=guild_id,
            user_id=penalty.user_id,
            penalty_apr=penalty.penalty_apr,
            penalty_until=penalty.penalty_until,
        )

    def to_domain(self) -> FundPenalty:
        return FundPenalty(
            user_id=self.user_id,
            penalty_apr=self.penalty_apr,
            penalty_until=self.penalty_until,
        )


class SystemStateORM(Base):
    """Single-row per-guild background-task state (last reset timestamps).

    Per ADR-0001 background tasks iterate per guild with per-guild reset flags,
    so this is one row *per guild* — ``guild_id`` is the whole primary key.
    There is no domain dataclass mirror; this is pure adapter bookkeeping.
    """

    __tablename__ = "system_state"

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    last_daily_reset: Mapped[datetime | None] = mapped_column(UtcDateTime)
    last_weekly_reset: Mapped[datetime | None] = mapped_column(UtcDateTime)

    @classmethod
    def create(
        cls,
        guild_id: str,
        *,
        last_daily_reset: datetime | None,
        last_weekly_reset: datetime | None,
    ) -> SystemStateORM:
        return cls(
            guild_id=guild_id,
            last_daily_reset=last_daily_reset,
            last_weekly_reset=last_weekly_reset,
        )


class TradeCooldownORM(Base):
    """A short/cover cooldown with TTL via ``expires_at``.

    Replaces Redis-native TTL (see §Persistence Recommendation): a sweep task
    deletes rows where ``expires_at < now``. No domain dataclass mirror.
    """

    __tablename__ = "trade_cooldowns"

    guild_id: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)

    @classmethod
    def create(
        cls, guild_id: str, *, user_id: str, expires_at: datetime
    ) -> TradeCooldownORM:
        return cls(guild_id=guild_id, user_id=user_id, expires_at=expires_at)
