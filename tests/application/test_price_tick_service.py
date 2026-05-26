"""Behavioural tests for :class:`PriceTickService` (Phase 8b).

The service is a pure orchestrator: the background-task layer calls its three
``*_tick`` methods, which in turn read stocks/users via the repositories,
compute new prices via the *pure* :mod:`friendex.domain.price_engine`
functions, and write the results back. The service holds no math of its own.

Acceptance criteria pinned here, each named on its test:

* **B1** — ``activity_price_tick`` raises an active user's price.
* **B2** — ``activity_price_tick`` lowers an under-engaged user's price
  (a negative-return path mediated by ``apply_floor_stall`` stalling).
* **B3** — ``inactivity_decay_tick`` applies decay ONLY after the
  configured idle threshold (no decay before; decay after).
* **B4** — ``vc_boost_tick`` boosts ONLY users still in voice (uses
  :class:`VoiceSessionStore` to filter), respecting the per-user
  ``last_boost``/``end_time`` window from :class:`VcExtraBoost`.
* **B5** — the ``min_price`` floor is enforced through every path: a heavy
  negative tick on a near-floor stock clamps to ``settings.min_price``,
  never below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.price_tick_service import PriceTickService
from friendex.application.voice_session_store import VoiceSessionStore
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    Stock,
    UserAccount,
    VcExtraBoost,
    VoiceSession,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import FakePriceRepo, FakeUserRepo

GUILD = "100000000000000001"
USER_ACTIVE = "5001"
USER_QUIET = "5002"
USER_IDLE = "5003"
USER_ACTIVE_RECENTLY = "5004"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _account(
    user_id: str,
    *,
    last_activity: datetime,
    today: ActivityBucket | None = None,
    week: ActivityBucket | None = None,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` with given activity state."""
    now = datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=today if today is not None else ActivityBucket(bucket_start=now),
        week=week if week is not None else ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=last_activity,
    )


def _stock(user_id: str, *, current: Decimal = Decimal("100.00")) -> Stock:
    """Build a minimal valid :class:`Stock` with empty history."""
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    lock_manager: LockManager,
    settings: Settings,
    voice_sessions: VoiceSessionStore | None = None,
) -> PriceTickService:
    """Construct the service under test with explicit dependencies."""
    return PriceTickService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        lock_manager=lock_manager,
        settings=settings,
        voice_sessions=voice_sessions or VoiceSessionStore(),
    )


# ---------------------------------------------------------------------------
# B1 — activity_price_tick raises an active user's price
# ---------------------------------------------------------------------------


async def test_activity_tick_raises_price_for_active_user(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """An engaged user (many text/voice msgs) gets a positive price tick."""
    now = datetime.now(tz=UTC)
    engaged = ActivityBucket(
        bucket_start=now,
        text_msgs=50,
        media_msgs=10,
        voice_minutes=30.0,
        reaction_count=20,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(USER_ACTIVE, last_activity=now, today=engaged),
    )
    starting = Decimal("100.00")
    await fake_price_repo.upsert(GUILD, _stock(USER_ACTIVE, current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.activity_price_tick()

    after = await fake_price_repo.get(GUILD, USER_ACTIVE)
    assert after is not None
    assert after.current > starting


# ---------------------------------------------------------------------------
# B2 — activity_price_tick lowers an under-engaged user's price
# ---------------------------------------------------------------------------


async def test_activity_tick_lowers_price_for_under_engaged_user(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A bucket producing a negative return drops the price (stalled by floor)."""
    now = datetime.now(tz=UTC)
    # A negative activity_tick_k turns even a small positive score into a
    # negative return — verifying the service routes a negative delta through
    # apply_floor_stall (and therefore can drop the price).
    drop_settings = default_settings.model_copy(update={"activity_tick_k": -2.0})

    engaged = ActivityBucket(
        bucket_start=now,
        text_msgs=20,
        reaction_count=10,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(USER_QUIET, last_activity=now, today=engaged),
    )
    starting = Decimal("150.00")
    await fake_price_repo.upsert(GUILD, _stock(USER_QUIET, current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=drop_settings,
    )

    await service.activity_price_tick()

    after = await fake_price_repo.get(GUILD, USER_QUIET)
    assert after is not None
    assert after.current < starting
    assert after.current >= Decimal(str(drop_settings.min_price))


# ---------------------------------------------------------------------------
# B3 — inactivity_decay_tick respects the threshold boundary
# ---------------------------------------------------------------------------


async def test_inactivity_decay_skipped_before_threshold(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A user idle for *less* than the threshold gets NO decay."""
    now = datetime.now(tz=UTC)
    threshold = default_settings.inactivity_threshold_seconds
    just_under = now - timedelta(seconds=threshold - 60)

    await fake_user_repo.upsert(
        GUILD,
        _account(USER_ACTIVE_RECENTLY, last_activity=just_under),
    )
    starting = Decimal("100.00")
    await fake_price_repo.upsert(GUILD, _stock(USER_ACTIVE_RECENTLY, current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.inactivity_decay_tick()

    after = await fake_price_repo.get(GUILD, USER_ACTIVE_RECENTLY)
    assert after is not None
    assert after.current == starting


async def test_inactivity_decay_applied_past_threshold(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A user idle past the threshold has their price decayed."""
    now = datetime.now(tz=UTC)
    threshold = default_settings.inactivity_threshold_seconds
    well_past = now - timedelta(seconds=threshold + 600)

    await fake_user_repo.upsert(
        GUILD,
        _account(USER_IDLE, last_activity=well_past),
    )
    starting = Decimal("100.00")
    await fake_price_repo.upsert(GUILD, _stock(USER_IDLE, current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.inactivity_decay_tick()

    after = await fake_price_repo.get(GUILD, USER_IDLE)
    assert after is not None
    assert after.current < starting
    # Exact: 100 * (1 - 0.04) == 96.00 (with default inactivity_decay=0.04).
    assert after.current == Decimal("96.00")


# ---------------------------------------------------------------------------
# B4 — vc_boost_tick only boosts users still in voice
# ---------------------------------------------------------------------------


async def test_vc_boost_tick_boosts_only_in_voice_users(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Of two extra-boost recipients, only the one still in voice is boosted."""
    now = datetime.now(tz=UTC)
    ping_time = now - timedelta(minutes=20)
    end_time = ping_time + timedelta(seconds=default_settings.voice_ping_window_seconds)
    last_boost_past = now - timedelta(
        seconds=default_settings.vc_extra_boost_interval_seconds + 60
    )

    # Both users have an active boost window.
    in_voice_user = "boost_in_voice"
    away_user = "boost_away"
    boosts = [
        VcExtraBoost(
            user_id=in_voice_user,
            ping_time=ping_time,
            last_boost=last_boost_past,
            end_time=end_time,
        ),
        VcExtraBoost(
            user_id=away_user,
            ping_time=ping_time,
            last_boost=last_boost_past,
            end_time=end_time,
        ),
    ]

    # Only `in_voice_user` has a live VoiceSession.
    voice_sessions = VoiceSessionStore()
    await voice_sessions.set(
        VoiceSession(
            user_id=in_voice_user,
            channel_id=42,
            start=ping_time,
            from_ping_message_ids=set(),
        )
    )

    starting = Decimal("100.00")
    await fake_price_repo.upsert(GUILD, _stock(in_voice_user, current=starting))
    await fake_price_repo.upsert(GUILD, _stock(away_user, current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
        voice_sessions=voice_sessions,
    )

    survivors = await service.vc_boost_tick(extra_boosts=boosts, now=now)

    in_voice_stock = await fake_price_repo.get(GUILD, in_voice_user)
    away_stock = await fake_price_repo.get(GUILD, away_user)
    assert in_voice_stock is not None
    assert away_stock is not None
    # In-voice user boosted; away user untouched.
    assert in_voice_stock.current > starting
    assert away_stock.current == starting

    # Survivor list reflects the new last_boost timestamp for the in-voice user
    # and leaves the away user's record unchanged (boost window still open).
    survivors_by_id = {b.user_id: b for b in survivors}
    assert in_voice_user in survivors_by_id
    assert away_user in survivors_by_id
    assert survivors_by_id[in_voice_user].last_boost > last_boost_past
    assert survivors_by_id[away_user].last_boost == last_boost_past


async def test_vc_boost_tick_drops_expired_window(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A boost whose ``end_time`` has passed is dropped from the survivor list."""
    now = datetime.now(tz=UTC)
    expired_user = "boost_expired"
    boost = VcExtraBoost(
        user_id=expired_user,
        ping_time=now - timedelta(hours=3),
        last_boost=now - timedelta(hours=2),
        end_time=now - timedelta(minutes=5),
    )

    voice_sessions = VoiceSessionStore()
    await voice_sessions.set(
        VoiceSession(
            user_id=expired_user,
            channel_id=42,
            start=now - timedelta(hours=1),
            from_ping_message_ids=set(),
        )
    )

    starting = Decimal("100.00")
    await fake_price_repo.upsert(GUILD, _stock(expired_user, current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
        voice_sessions=voice_sessions,
    )

    survivors = await service.vc_boost_tick(extra_boosts=[boost], now=now)

    # Expired entry is purged; no boost applied (even though user is in voice).
    assert survivors == []
    stock = await fake_price_repo.get(GUILD, expired_user)
    assert stock is not None
    assert stock.current == starting


# ---------------------------------------------------------------------------
# B5 — min_price floor enforced through every path
# ---------------------------------------------------------------------------


async def test_activity_tick_floor_enforced_for_near_floor_stock(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A heavy negative activity tick clamps to ``min_price``."""
    now = datetime.now(tz=UTC)
    # Strongly negative K and a generous score: proposed delta would push
    # well below the floor — apply_floor_stall must clamp.
    crash_settings = default_settings.model_copy(update={"activity_tick_k": -1000.0})

    engaged = ActivityBucket(
        bucket_start=now,
        text_msgs=500,
        media_msgs=200,
        reaction_count=200,
        voice_minutes=300.0,
    )
    starting = Decimal(str(default_settings.min_price + 1.0))  # near floor
    await fake_user_repo.upsert(
        GUILD,
        _account("nearfloor", last_activity=now, today=engaged),
    )
    await fake_price_repo.upsert(GUILD, _stock("nearfloor", current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=crash_settings,
    )

    await service.activity_price_tick()

    after = await fake_price_repo.get(GUILD, "nearfloor")
    assert after is not None
    assert after.current >= Decimal(str(default_settings.min_price))


async def test_inactivity_decay_floor_enforced_for_at_floor_stock(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """An at-floor stock that goes inactive does NOT slip below the floor."""
    now = datetime.now(tz=UTC)
    threshold = default_settings.inactivity_threshold_seconds
    well_past = now - timedelta(seconds=threshold + 600)
    floor = Decimal(str(default_settings.min_price))

    await fake_user_repo.upsert(
        GUILD,
        _account("at_floor", last_activity=well_past),
    )
    await fake_price_repo.upsert(GUILD, _stock("at_floor", current=floor))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.inactivity_decay_tick()

    after = await fake_price_repo.get(GUILD, "at_floor")
    assert after is not None
    assert after.current == floor


# ---------------------------------------------------------------------------
# Bonus — no-stock user is skipped (no crash)
# ---------------------------------------------------------------------------


async def test_activity_tick_skips_user_without_stock(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A user with an account but no Stock row is silently skipped."""
    now = datetime.now(tz=UTC)
    await fake_user_repo.upsert(
        GUILD,
        _account("stockless", last_activity=now),
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    # Must not raise; repo stays empty.
    await service.activity_price_tick()
    assert await fake_price_repo.get(GUILD, "stockless") is None
