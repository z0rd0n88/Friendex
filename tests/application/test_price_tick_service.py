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

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.lock_manager import LockManager
from friendex.application.price_tick_service import PriceTickService
from friendex.application.voice_session_store import VoiceSessionStore
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    PricePoint,
    Stock,
    UserAccount,
    VcExtraBoost,
    VoiceSession,
)
from tests.application.fakes.fake_repos import FakePriceRepo

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from tests.application.fakes.fake_repos import FakeUserRepo

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


# ---------------------------------------------------------------------------
# H1 — Read-modify-write atomicity: a concurrent upsert landing between the
# tick's pre-lock read and its in-lock write must NOT be clobbered.
# ---------------------------------------------------------------------------


class _BarrierPriceRepo:
    """A :class:`FakePriceRepo`-shaped wrapper that parks ``upsert`` on a barrier.

    Used by the H1 interleaving test to deterministically race a tick against a
    simulated concurrent trade-style ``upsert`` on the same ``(guild, user)``.

    The barrier strategy:

    * ``get`` calls are counted; the second ``get`` (the in-lock re-read the
      RMW fix introduces) blocks the tick until the test releases it, giving
      the concurrent ``upsert`` a deterministic window to land first.
    * The concurrent ``upsert`` is performed directly against the underlying
      :class:`FakePriceRepo`, bypassing the lock — this models a writer that
      already committed under the same lock and released it in real life.
    """

    def __init__(self, inner: FakePriceRepo) -> None:
        self._inner = inner
        self.get_calls = 0
        self.second_get_arrived = asyncio.Event()
        self.release_second_get = asyncio.Event()

    async def get(self, guild_id: str, user_id: str) -> Stock | None:
        self.get_calls += 1
        if self.get_calls == 2:
            self.second_get_arrived.set()
            await self.release_second_get.wait()
        return await self._inner.get(guild_id, user_id)

    async def upsert(self, guild_id: str, stock: Stock) -> None:
        await self._inner.upsert(guild_id, stock)

    async def delete(self, guild_id: str, user_id: str) -> None:
        await self._inner.delete(guild_id, user_id)

    async def list_all(self, guild_id: str) -> list[Stock]:
        return await self._inner.list_all(guild_id)

    async def append_history(
        self, guild_id: str, user_id: str, point: PricePoint
    ) -> None:
        await self._inner.append_history(guild_id, user_id, point)

    async def get_history(
        self,
        guild_id: str,
        user_id: str,
        *,
        since: datetime | None = None,
    ) -> list[PricePoint]:
        return await self._inner.get_history(guild_id, user_id, since=since)

    async def prune_history_older_than(self, cutoff: datetime) -> int:
        return await self._inner.prune_history_older_than(cutoff)


async def test_activity_tick_does_not_clobber_concurrent_upsert(
    fake_user_repo: FakeUserRepo,
    default_settings: Settings,
) -> None:
    """A concurrent trade-style upsert between tick read and write is not clobbered.

    The tick must take the lock FIRST, then re-read ``stock`` inside the lock,
    then compute ``new_price`` from the fresh snapshot. With the broken
    pre-lock read, the tick's stale-derived price clobbers any value a
    concurrent mutator wrote between the pre-lock get and the lock acquire.

    Construction: a barrier-instrumented price repo blocks the tick's second
    ``get`` (the in-lock re-read) until the test directly writes a
    concurrent-trade-style stock with a marker price. Releasing the gate lets
    the tick re-read; an atomic RMW recomputes from the fresh value, so the
    final stored price is *derived from the marker* (not the original) — the
    concurrent write is honoured, not clobbered.
    """
    now = datetime.now(tz=UTC)
    user_id = "rmw_user"
    engaged = ActivityBucket(
        bucket_start=now,
        text_msgs=50,
        media_msgs=10,
        voice_minutes=30.0,
        reaction_count=20,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(user_id, last_activity=now, today=engaged),
    )
    starting = Decimal("100.00")
    inner = FakePriceRepo()
    await inner.upsert(GUILD, _stock(user_id, current=starting))
    barrier_repo = _BarrierPriceRepo(inner)

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=barrier_repo,  # type: ignore[arg-type]
        lock_manager=LockManager(),
        settings=default_settings,
    )

    tick_task = asyncio.create_task(service.activity_price_tick())

    # Wait for the tick's in-lock re-read to park on the barrier, then land a
    # concurrent upsert with a deliberately divergent marker price.
    await asyncio.wait_for(barrier_repo.second_get_arrived.wait(), timeout=1.0)
    marker = Decimal("200.00")
    await inner.upsert(GUILD, _stock(user_id, current=marker))

    # Release the in-lock re-read; the RMW recomputes from the marker.
    barrier_repo.release_second_get.set()
    await asyncio.wait_for(tick_task, timeout=1.0)

    after = await inner.get(GUILD, user_id)
    assert after is not None
    # Atomicity proof: the final price was derived from the marker (not 100.00).
    # An active engaged bucket yields a positive return for any positive
    # ``activity_tick_k`` (default K=0.3), so the post-tick price is at least
    # the marker. Critically, it must NOT equal a value derived from the stale
    # 100.00 snapshot (which would be less than the marker).
    assert after.current >= marker, (
        f"Concurrent upsert at {marker} was clobbered — tick wrote {after.current}, "
        "indicating new_price was computed from the stale pre-lock read."
    )


# ---------------------------------------------------------------------------
# M2 — Ticks must append PricePoint history and bump all_time_high.
# ---------------------------------------------------------------------------


async def test_activity_tick_appends_price_history(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Every price-changing activity tick appends a PricePoint to history."""
    now = datetime.now(tz=UTC)
    engaged = ActivityBucket(
        bucket_start=now,
        text_msgs=50,
        media_msgs=10,
        voice_minutes=30.0,
        reaction_count=20,
    )
    user_id = "history_user"
    await fake_user_repo.upsert(
        GUILD,
        _account(user_id, last_activity=now, today=engaged),
    )
    await fake_price_repo.upsert(GUILD, _stock(user_id, current=Decimal("100.00")))

    history_before = await fake_price_repo.get_history(GUILD, user_id)
    assert history_before == []

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )
    await service.activity_price_tick()

    history_after = await fake_price_repo.get_history(GUILD, user_id)
    assert len(history_after) == 1
    after = await fake_price_repo.get(GUILD, user_id)
    assert after is not None
    assert history_after[0].price == after.current


async def test_inactivity_decay_tick_appends_price_history(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Every price-changing inactivity-decay tick appends a PricePoint."""
    now = datetime.now(tz=UTC)
    threshold = default_settings.inactivity_threshold_seconds
    well_past = now - timedelta(seconds=threshold + 600)
    user_id = "decay_history_user"
    await fake_user_repo.upsert(
        GUILD,
        _account(user_id, last_activity=well_past),
    )
    await fake_price_repo.upsert(GUILD, _stock(user_id, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )
    await service.inactivity_decay_tick()

    history = await fake_price_repo.get_history(GUILD, user_id)
    assert len(history) == 1
    after = await fake_price_repo.get(GUILD, user_id)
    assert after is not None
    assert history[0].price == after.current


async def test_vc_boost_tick_appends_price_history(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A successful VC boost appends a PricePoint to history."""
    now = datetime.now(tz=UTC)
    ping_time = now - timedelta(minutes=20)
    end_time = ping_time + timedelta(seconds=default_settings.voice_ping_window_seconds)
    last_boost_past = now - timedelta(
        seconds=default_settings.vc_extra_boost_interval_seconds + 60
    )
    user_id = "boost_history_user"
    boost = VcExtraBoost(
        user_id=user_id,
        ping_time=ping_time,
        last_boost=last_boost_past,
        end_time=end_time,
    )

    voice_sessions = VoiceSessionStore()
    await voice_sessions.set(
        VoiceSession(
            user_id=user_id,
            channel_id=42,
            start=ping_time,
            from_ping_message_ids=set(),
        )
    )

    await fake_price_repo.upsert(GUILD, _stock(user_id, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
        voice_sessions=voice_sessions,
    )
    await service.vc_boost_tick(extra_boosts=[boost], now=now)

    history = await fake_price_repo.get_history(GUILD, user_id)
    assert len(history) == 1
    after = await fake_price_repo.get(GUILD, user_id)
    assert after is not None
    assert history[0].price == after.current


async def test_activity_tick_advances_all_time_high(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A positive tick that breaks the prior all_time_high advances it."""
    now = datetime.now(tz=UTC)
    engaged = ActivityBucket(
        bucket_start=now,
        text_msgs=50,
        media_msgs=10,
        voice_minutes=30.0,
        reaction_count=20,
    )
    user_id = "ath_user"
    starting = Decimal("100.00")
    await fake_user_repo.upsert(
        GUILD,
        _account(user_id, last_activity=now, today=engaged),
    )
    # Stock starts at $100 with all_time_high also $100 (via _stock helper).
    await fake_price_repo.upsert(GUILD, _stock(user_id, current=starting))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )
    await service.activity_price_tick()

    after = await fake_price_repo.get(GUILD, user_id)
    assert after is not None
    assert after.current > starting
    assert after.all_time_high == after.current


async def test_inactivity_decay_does_not_lower_all_time_high(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A downward tick must NOT lower all_time_high (only ratchets up)."""
    now = datetime.now(tz=UTC)
    threshold = default_settings.inactivity_threshold_seconds
    well_past = now - timedelta(seconds=threshold + 600)
    user_id = "ath_stays_user"
    starting = Decimal("100.00")
    prior_ath = Decimal("150.00")
    await fake_user_repo.upsert(
        GUILD,
        _account(user_id, last_activity=well_past),
    )
    # Seed an existing all_time_high that's above current — a downward tick
    # must not touch it.
    await fake_price_repo.upsert(
        GUILD,
        Stock(
            user_id=user_id,
            current=starting,
            history=[],
            high_24h=starting,
            low_24h=starting,
            all_time_high=prior_ath,
        ),
    )

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )
    await service.inactivity_decay_tick()

    after = await fake_price_repo.get(GUILD, user_id)
    assert after is not None
    assert after.current < starting
    assert after.all_time_high == prior_ath
