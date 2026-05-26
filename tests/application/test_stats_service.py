"""Behavioural tests for :class:`StatsService` (Phase 8d).

The service is a thin **read-only** orchestrator over
:func:`friendex.domain.activity.calculate_trending_score` /
:func:`~friendex.domain.activity.get_engagement_tier` plus the price-history
window from :meth:`IPriceRepo.get_history`. No locks, no writes.

Acceptance criteria pinned here:

* **D6** — :meth:`trending_snapshot` sorts users DESCENDING by trending score.
* **D7** — :meth:`trending_snapshot` filters out users with a zero score.
* **D8** — :meth:`trending_snapshot` slices to 15 by default, or to the
  explicit ``limit`` kwarg.
* **D9** — :meth:`get_price_stats` computes 24 h high/low from history within
  the 24-hour window (concrete points inside *and* outside the window).
* **D10** — :meth:`user_stats` engagement tier coverage across the boundaries
  from :func:`get_engagement_tier`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from freezegun import freeze_time

from friendex.application.stats_service import StatsService
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    PricePoint,
    Stock,
    UserAccount,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from tests.application.fakes.fake_repos import FakePriceRepo, FakeUserRepo


GUILD = "100000000000000001"
ACTOR = "actor-1"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _account(
    user_id: str,
    *,
    today: ActivityBucket | None = None,
    last_activity: datetime | None = None,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the stats tests."""
    now = last_activity if last_activity is not None else datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("1000.00"),
        net_worth=Decimal("1000.00"),
        month_start_net_worth=Decimal("1000.00"),
        long_positions={},
        short_positions={},
        today=today if today is not None else ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
    )


def _stock(user_id: str, *, current: Decimal = Decimal("100.00")) -> Stock:
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _bucket(
    *,
    text: int = 0,
    media: int = 0,
    voice_minutes: float = 0.0,
    voice_channels: list[str] | None = None,
    reactions: int = 0,
    replies: int = 0,
    role_ping_joins: float = 0.0,
    role_ping_join_minutes: float = 0.0,
) -> ActivityBucket:
    return ActivityBucket(
        text_msgs=text,
        media_msgs=media,
        voice_minutes=voice_minutes,
        voice_unique_channels=voice_channels or [],
        reaction_count=reactions,
        reply_count=replies,
        role_ping_joins=role_ping_joins,
        role_ping_join_minutes=role_ping_join_minutes,
        bucket_start=datetime.now(tz=UTC),
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    settings: Settings,
) -> StatsService:
    """Construct the service under test with explicit dependencies."""
    return StatsService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# D6 — trending sorts descending
# ---------------------------------------------------------------------------


async def test_trending_snapshot_sorts_descending_by_score(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D6: highest score appears first; lowest non-zero last."""
    # Three users with distinct, deliberately-unsorted insertion order.
    await fake_user_repo.upsert(GUILD, _account("low", today=_bucket(text=1)))
    await fake_user_repo.upsert(
        GUILD, _account("high", today=_bucket(text=100, media=50, replies=50))
    )
    await fake_user_repo.upsert(GUILD, _account("mid", today=_bucket(text=10)))
    for uid in ("low", "high", "mid"):
        await fake_price_repo.upsert(GUILD, _stock(uid))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    snapshot = await service.trending_snapshot()

    user_ids = [entry.user_id for entry in snapshot]
    assert user_ids == ["high", "mid", "low"]
    # Ranks are 1-indexed from the top.
    assert [entry.rank for entry in snapshot] == [1, 2, 3]
    # And scores are monotonically non-increasing.
    scores = [entry.score for entry in snapshot]
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# D7 — trending filters zero scores
# ---------------------------------------------------------------------------


async def test_trending_snapshot_filters_zero_scores(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D7: users with an empty activity bucket (score == 0) are dropped."""
    await fake_user_repo.upsert(GUILD, _account("zero1"))  # empty bucket → 0
    await fake_user_repo.upsert(GUILD, _account("zero2"))  # empty bucket → 0
    await fake_user_repo.upsert(GUILD, _account("active", today=_bucket(text=5)))
    for uid in ("zero1", "zero2", "active"):
        await fake_price_repo.upsert(GUILD, _stock(uid))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    snapshot = await service.trending_snapshot()

    assert [entry.user_id for entry in snapshot] == ["active"]
    assert snapshot[0].score > 0.0


# ---------------------------------------------------------------------------
# D8 — trending slices to limit (default 15)
# ---------------------------------------------------------------------------


async def test_trending_snapshot_default_limit_is_15(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D8 (default): with 20 active users, snapshot returns the top 15."""
    # 20 users with strictly-decreasing trending scores (text count drives it).
    for i in range(20):
        uid = f"u-{i:02d}"
        # Higher i → fewer text messages → lower score; reverse so u-00 is top.
        await fake_user_repo.upsert(GUILD, _account(uid, today=_bucket(text=(20 - i))))
        await fake_price_repo.upsert(GUILD, _stock(uid))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    snapshot = await service.trending_snapshot()

    assert len(snapshot) == 15
    assert snapshot[0].user_id == "u-00"
    # The 15th-ranked user is u-14 (top-15 of 20).
    assert snapshot[-1].user_id == "u-14"


async def test_trending_snapshot_honours_explicit_limit_kwarg(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D8 (kwarg): explicit limit=3 trims to the top three."""
    for i in range(10):
        uid = f"u-{i:02d}"
        await fake_user_repo.upsert(GUILD, _account(uid, today=_bucket(text=(10 - i))))
        await fake_price_repo.upsert(GUILD, _stock(uid))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    snapshot = await service.trending_snapshot(limit=3)

    assert len(snapshot) == 3
    assert [entry.user_id for entry in snapshot] == ["u-00", "u-01", "u-02"]


# ---------------------------------------------------------------------------
# D9 — get_price_stats 24h window
# ---------------------------------------------------------------------------


async def test_get_price_stats_computes_24h_high_low_from_history_window(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D9: points inside the 24h window are considered; older points are excluded."""
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    # Two points outside the 24h window: a 999 (high) and a 1 (low) — both
    # should be IGNORED because they are older than now - 24h.
    outside_old_high = PricePoint(
        price=Decimal("999.00"), timestamp=now - timedelta(hours=48)
    )
    outside_old_low = PricePoint(
        price=Decimal("1.00"), timestamp=now - timedelta(hours=25)
    )
    # Three points INSIDE the 24h window: 110 (high), 90 (low), 100 (between).
    inside_high = PricePoint(
        price=Decimal("110.00"), timestamp=now - timedelta(hours=6)
    )
    inside_low = PricePoint(price=Decimal("90.00"), timestamp=now - timedelta(hours=12))
    inside_mid = PricePoint(price=Decimal("100.00"), timestamp=now - timedelta(hours=1))

    await fake_price_repo.upsert(GUILD, _stock(ACTOR, current=Decimal("105.00")))
    for point in (
        outside_old_high,
        outside_old_low,
        inside_low,
        inside_high,
        inside_mid,
    ):
        await fake_price_repo.append_history(GUILD, ACTOR, point)

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    with freeze_time(now):
        stats = await service.get_price_stats(ACTOR)

    assert stats is not None
    assert stats.user_id == ACTOR
    assert stats.current == Decimal("105.00")
    # 110 inside window, 999 outside → high is 110.
    assert stats.high_24h == Decimal("110.00")
    # 90 inside window, 1 outside → low is 90.
    assert stats.low_24h == Decimal("90.00")


async def test_get_price_stats_empty_history_falls_back_to_current(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D9 (boundary): empty 24h window → high == low == current."""
    await fake_price_repo.upsert(GUILD, _stock(ACTOR, current=Decimal("123.45")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    stats = await service.get_price_stats(ACTOR)

    assert stats is not None
    assert stats.high_24h == Decimal("123.45")
    assert stats.low_24h == Decimal("123.45")
    assert stats.current == Decimal("123.45")


# ---------------------------------------------------------------------------
# D10 — user_stats engagement-tier coverage across boundaries
# ---------------------------------------------------------------------------


async def test_user_stats_engagement_tier_covers_boundaries(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D10: across a 20-user population, the top scorer is Elite, the bottom is Low.

    Tier cuts from :func:`get_engagement_tier`:
        <=5% Elite, <=30% High, <=70% Medium, else Low.
    With 20 users (1-indexed percentile = rank / 20):
      rank 1 → 5% → Elite
      rank 6 → 30% → High
      rank 14 → 70% → Medium
      rank 15+ → Low

    We assert the top and bottom; the middle two are covered as a sanity
    range so a wrong boundary in one tier surfaces immediately.
    """
    # 20 users with strictly-decreasing scores (text count drives it).
    for i in range(20):
        uid = f"u-{i:02d}"
        await fake_user_repo.upsert(GUILD, _account(uid, today=_bucket(text=(40 - i))))
        await fake_price_repo.upsert(GUILD, _stock(uid))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    top = await service.user_stats("u-00")
    high = await service.user_stats("u-05")
    medium = await service.user_stats("u-10")
    low = await service.user_stats("u-19")

    assert top is not None and top.engagement_tier == "Elite"
    assert high is not None and high.engagement_tier == "High"
    assert medium is not None and medium.engagement_tier == "Medium"
    assert low is not None and low.engagement_tier == "Low"


async def test_user_stats_empty_population_returns_low(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """D10 (boundary): a single-user guild yields the only tier the function defines.

    With one user, rank-1 percentile = 1/1 = 1.0 → falls past every cut →
    "Low".
    """
    await fake_user_repo.upsert(GUILD, _account(ACTOR, today=_bucket(text=10)))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        settings=default_settings,
    )

    stats = await service.user_stats(ACTOR)

    assert stats is not None
    assert stats.engagement_tier == "Low"
    assert stats.trending_score > 0.0
