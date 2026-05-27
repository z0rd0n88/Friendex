"""Behavioural tests for :class:`VoicePingService` (Phase 8a).

A "voice ping" is a host pinging a VC role; responders who join the same channel
within the response window earn engagement credit (scaled by how fast they
responded) and — for the first N unique joiners — a one-time price boost. Later
joiners are tracked as ``extra_joiners`` for the periodic boost task (Phase 9)
instead.

Acceptance criteria pinned here:

* A7 — the first N joiners get fast / medium / slow tier bonuses by speed;
* A8 — the (N+1)th joiner is added to ``extra_joiners`` (no tier price boost);
* A9 — ``cleanup_expired_pings`` evicts ping sessions past the window;
* A10 — ``reward_voice_ping_response`` is idempotent per (ping, responder).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.lock_manager import LockManager
from friendex.application.voice_ping_service import VoicePingService
from friendex.application.voice_session_store import VoicePingSessionStore
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    Stock,
    UserAccount,
    VoicePingSession,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from friendex.adapters.config import Settings
    from tests.application.fakes.fake_repos import FakePriceRepo, FakeUserRepo

GUILD = "100000000000000001"
HOST = "9000"
CHANNEL = 5050
OTHER_CHANNEL = 6060


def _account(user_id: str) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` with fresh empty buckets."""
    now = datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
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
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    settings: Settings,
    store: VoicePingSessionStore,
) -> VoicePingService:
    """Construct a :class:`VoicePingService` scoped to ``GUILD``."""
    return VoicePingService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        lock_manager=LockManager(),
        settings=settings,
        ping_sessions=store,
    )


async def test_first_joiners_get_speed_tier_bonuses(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A7: fast / medium / slow responders earn their respective tier bonuses."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)

    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    for uid in ("fast", "medium", "slow"):
        await fake_user_repo.upsert(GUILD, _account(uid))
        await fake_price_repo.upsert(GUILD, _stock(uid))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    base = default_settings.voice_ping_base_points
    # Fast: within fast window.
    await service.reward_voice_ping_response(
        responder_id="fast",
        channel_id=CHANNEL,
        now=ping_time + timedelta(seconds=default_settings.fast_response_seconds - 1),
    )
    # Medium: between fast and medium windows.
    await service.reward_voice_ping_response(
        responder_id="medium",
        channel_id=CHANNEL,
        now=ping_time + timedelta(seconds=default_settings.medium_response_seconds - 1),
    )
    # Slow: past the medium window but inside the response window.
    await service.reward_voice_ping_response(
        responder_id="slow",
        channel_id=CHANNEL,
        now=ping_time
        + timedelta(seconds=default_settings.medium_response_seconds + 60),
    )

    fast = await fake_user_repo.get(GUILD, "fast")
    medium = await fake_user_repo.get(GUILD, "medium")
    slow = await fake_user_repo.get(GUILD, "slow")
    assert fast is not None and medium is not None and slow is not None
    assert (
        fast.today.role_ping_join_minutes
        == base * default_settings.voice_ping_fast_multiplier
    )
    assert (
        medium.today.role_ping_join_minutes
        == base * default_settings.voice_ping_medium_multiplier
    )
    assert (
        slow.today.role_ping_join_minutes
        == base * default_settings.voice_ping_slow_multiplier
    )


async def test_first_joiner_gets_price_boost_and_is_tracked(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A7 (price): a first-N joiner gets the one-time join price boost."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

    await fake_user_repo.upsert(GUILD, _account("r1"))
    await fake_price_repo.upsert(GUILD, _stock("r1", current=Decimal("100.00")))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    await service.reward_voice_ping_response(
        responder_id="r1",
        channel_id=CHANNEL,
        now=ping_time + timedelta(seconds=30),
    )

    stock = await fake_price_repo.get(GUILD, "r1")
    assert stock is not None
    # 100.00 * 1.20 = 120.00.
    assert stock.current == Decimal("120.00")
    session = await store.get(1)
    assert session is not None
    assert session.first_10_joiners == ["r1"]


async def test_eleventh_joiner_goes_to_extra_joiners(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A8: once N joiners are recorded, the next is an extra joiner (no boost)."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    cap = default_settings.voice_ping_first_n_joiners

    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )
    # Fill the first-N slots.
    for i in range(cap):
        uid = f"r{i}"
        await fake_user_repo.upsert(GUILD, _account(uid))
        await fake_price_repo.upsert(GUILD, _stock(uid))
        await service.reward_voice_ping_response(
            responder_id=uid, channel_id=CHANNEL, now=ping_time + timedelta(seconds=30)
        )

    # The (N+1)th joiner.
    extra_id = "extra"
    await fake_user_repo.upsert(GUILD, _account(extra_id))
    await fake_price_repo.upsert(GUILD, _stock(extra_id, current=Decimal("100.00")))
    await service.reward_voice_ping_response(
        responder_id=extra_id, channel_id=CHANNEL, now=ping_time + timedelta(seconds=40)
    )

    session = await store.get(1)
    assert session is not None
    assert len(session.first_10_joiners) == cap
    assert extra_id not in session.first_10_joiners
    assert extra_id in session.extra_joiners
    # No one-time join price boost for the extra joiner.
    extra_stock = await fake_price_repo.get(GUILD, extra_id)
    assert extra_stock is not None
    assert extra_stock.current == Decimal("100.00")


async def test_cleanup_expired_pings_evicts_old_sessions(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A9: a ping older than the response window is swept; a fresh one survives."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    window = default_settings.voice_ping_window_seconds

    expired_time = now - timedelta(seconds=window + 10)
    fresh_time = now - timedelta(seconds=10)
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=expired_time
    )
    await service.register_ping_message(
        message_id=2, host_id=HOST, channel_id=CHANNEL, timestamp=fresh_time
    )

    evicted = await service.cleanup_expired_pings(now)

    assert evicted == 1
    assert await store.get(1) is None
    assert await store.get(2) is not None


async def test_reward_is_idempotent_per_responder(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A10: the same responder joining twice does not double-pay."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

    await fake_user_repo.upsert(GUILD, _account("r1"))
    await fake_price_repo.upsert(GUILD, _stock("r1", current=Decimal("100.00")))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    await service.reward_voice_ping_response(
        responder_id="r1", channel_id=CHANNEL, now=ping_time + timedelta(seconds=30)
    )
    first = await fake_user_repo.get(GUILD, "r1")
    first_stock = await fake_price_repo.get(GUILD, "r1")
    assert first is not None and first_stock is not None
    points_after_first = first.today.role_ping_join_minutes
    price_after_first = first_stock.current

    # Re-trigger the same responder on the same ping.
    await service.reward_voice_ping_response(
        responder_id="r1", channel_id=CHANNEL, now=ping_time + timedelta(seconds=45)
    )

    second = await fake_user_repo.get(GUILD, "r1")
    second_stock = await fake_price_repo.get(GUILD, "r1")
    assert second is not None and second_stock is not None
    # No additional engagement credit and no second price boost.
    assert second.today.role_ping_join_minutes == points_after_first
    assert second_stock.current == price_after_first
    session = await store.get(1)
    assert session is not None
    assert session.first_10_joiners.count("r1") == 1


async def test_response_in_other_channel_is_ignored(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A join in a different channel earns nothing from this ping."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

    await fake_user_repo.upsert(GUILD, _account("r1"))
    await fake_price_repo.upsert(GUILD, _stock("r1", current=Decimal("100.00")))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    await service.reward_voice_ping_response(
        responder_id="r1",
        channel_id=OTHER_CHANNEL,
        now=ping_time + timedelta(seconds=30),
    )

    account = await fake_user_repo.get(GUILD, "r1")
    assert account is not None
    assert account.today.role_ping_join_minutes == 0.0


async def test_host_does_not_reward_self_on_own_ping(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A host joining their own ping's channel earns no responder reward."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

    await fake_user_repo.upsert(GUILD, _account(HOST))
    await fake_price_repo.upsert(GUILD, _stock(HOST, current=Decimal("100.00")))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    await service.reward_voice_ping_response(
        responder_id=HOST, channel_id=CHANNEL, now=ping_time + timedelta(seconds=30)
    )

    session = await store.get(1)
    assert session is not None
    assert HOST not in session.first_10_joiners
    stock = await fake_price_repo.get(GUILD, HOST)
    assert stock is not None
    assert stock.current == Decimal("100.00")


async def test_response_after_window_is_ignored(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A join after the response window closes earns nothing from the ping."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    window = default_settings.voice_ping_window_seconds

    await fake_user_repo.upsert(GUILD, _account("late"))
    await fake_price_repo.upsert(GUILD, _stock("late", current=Decimal("100.00")))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    await service.reward_voice_ping_response(
        responder_id="late",
        channel_id=CHANNEL,
        now=ping_time + timedelta(seconds=window + 60),
    )

    account = await fake_user_repo.get(GUILD, "late")
    assert account is not None
    assert account.today.role_ping_join_minutes == 0.0


async def test_first_joiner_with_no_stock_still_credits_engagement(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A first-N joiner lacking a stock row gets engagement credit, no boost."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

    await fake_user_repo.upsert(GUILD, _account("no-stock"))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    await service.reward_voice_ping_response(
        responder_id="no-stock",
        channel_id=CHANNEL,
        now=ping_time + timedelta(seconds=30),
    )

    session = await store.get(1)
    assert session is not None
    assert session.first_10_joiners == ["no-stock"]
    account = await fake_user_repo.get(GUILD, "no-stock")
    assert account is not None
    assert account.today.role_ping_join_minutes > 0.0
    assert await fake_price_repo.get(GUILD, "no-stock") is None


async def test_register_ping_credits_host_role_ping_joins(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """Issuing a ping credits the host one ``role_ping_joins`` point."""
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, default_settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)

    await fake_user_repo.upsert(GUILD, _account(HOST))
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    host = await fake_user_repo.get(GUILD, HOST)
    assert host is not None
    assert host.today.role_ping_joins == 1.0
    assert host.week.role_ping_joins == 1.0


async def test_collect_extra_boosts_emits_one_entry_per_extra_joiner(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """CF-4: ``collect_extra_boosts`` enumerates extras across open ping sessions.

    Phase 12 voice listener seeds :class:`VcBoostTask`'s per-guild store from
    this query after every join/switch — keeping the periodic boost task
    aware of the latest extra-joiner roster without exposing the ping-session
    internals to the listener.

    Each ``extra_joiner`` on each open ping yields a single
    :class:`VcExtraBoost` with ``ping_time = session.timestamp`` and
    ``end_time = session.timestamp + voice_ping_window_seconds``.
    """
    settings = default_settings.model_copy(update={"voice_ping_first_n_joiners": 1})
    store = VoicePingSessionStore()
    service = _make_service(fake_user_repo, fake_price_repo, settings, store)
    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    now = ping_time + timedelta(seconds=30)

    # Two pings: one with one extra joiner, one with two extras.
    await service.register_ping_message(
        message_id=1, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )
    await service.register_ping_message(
        message_id=2, host_id=HOST, channel_id=OTHER_CHANNEL, timestamp=ping_time
    )
    # Fill cap on ping 1, then add one extra.
    for uid in ("a1_first", "a1_extra"):
        await fake_user_repo.upsert(GUILD, _account(uid))
        await fake_price_repo.upsert(GUILD, _stock(uid))
        await service.reward_voice_ping_response(
            responder_id=uid, channel_id=CHANNEL, now=now
        )
    # Fill cap on ping 2, then add two extras.
    for uid in ("b2_first", "b2_extra_a", "b2_extra_b"):
        await fake_user_repo.upsert(GUILD, _account(uid))
        await fake_price_repo.upsert(GUILD, _stock(uid))
        await service.reward_voice_ping_response(
            responder_id=uid, channel_id=OTHER_CHANNEL, now=now
        )

    boosts = await service.collect_extra_boosts(now=now)

    user_ids = sorted(b.user_id for b in boosts)
    assert user_ids == ["a1_extra", "b2_extra_a", "b2_extra_b"]
    window = settings.voice_ping_window_seconds
    for boost in boosts:
        assert boost.ping_time == ping_time
        assert boost.last_boost == now
        assert boost.end_time == ping_time + timedelta(seconds=window)


class _BarrierPingSessionStore(VoicePingSessionStore):
    """Ping-session store whose ``list_all`` parks on a barrier on first call.

    Used by the CF-2 RMW-atomicity test to deterministically race two
    responders against the same ping: both callers stage at the barrier
    *after* fetching the session snapshot (which observes ``first_10_joiners == []``)
    and only proceed once both have arrived. Without the per-ping lock
    around the cap-check + write, both pass ``len(first_10_joiners) < cap``
    and both get recorded — exceeding cap.
    """

    def __init__(self, *, parties: int) -> None:
        super().__init__()
        # Barrier releases once ``parties`` callers reach ``list_all``.
        # NOTE: ``_barrier_fired`` is a per-instance, single-fire latch. A
        # second wave of ``list_all`` calls will NOT re-park — they pass
        # straight through. Tests that need multi-wave staging must
        # construct a fresh store (or extend with a wave counter); reusing
        # one instance across waves is a silent no-op on subsequent passes.
        self._barrier: asyncio.Barrier = asyncio.Barrier(parties)
        self._barrier_fired: bool = False

    async def list_all(self) -> list[VoicePingSession]:
        """Return the live snapshot; park at the barrier on the first wave only."""
        snapshot = await super().list_all()
        if not self._barrier_fired:
            await self._barrier.wait()
            self._barrier_fired = True
        return snapshot


async def test_concurrent_responders_respect_cap_under_per_ping_lock(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """CF-2: per-ping lock keeps two concurrent responders from over-crediting.

    Under the unlocked (pre-fix) code, two responders arriving at the same
    ping with a stale ``first_10_joiners`` snapshot both pass the cap-check
    (``0 < cap``) and both get the one-time join boost — exceeding the cap.

    The fix wraps the cap-check + write under
    ``lock_manager.locked(f"{guild_id}:ping:{session.message_id}")`` so the
    second responder re-reads after the first writes and falls through to
    ``extra_joiners``.

    Load-bearing: without the per-ping lock, the loop ``list_all`` snapshot
    is identical for both callers and both rewards are applied. The
    barrier ensures both reach the critical section concurrently.
    """
    # Force a tiny cap so the race is observable with just two responders.
    settings = default_settings.model_copy(update={"voice_ping_first_n_joiners": 1})
    store = _BarrierPingSessionStore(parties=2)
    service = _make_service(fake_user_repo, fake_price_repo, settings, store)

    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    for uid in ("r_first", "r_second"):
        await fake_user_repo.upsert(GUILD, _account(uid))
        await fake_price_repo.upsert(GUILD, _stock(uid, current=Decimal("100.00")))
    await service.register_ping_message(
        message_id=42, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    response_now = ping_time + timedelta(seconds=30)
    await asyncio.wait_for(
        asyncio.gather(
            service.reward_voice_ping_response(
                responder_id="r_first", channel_id=CHANNEL, now=response_now
            ),
            service.reward_voice_ping_response(
                responder_id="r_second", channel_id=CHANNEL, now=response_now
            ),
        ),
        timeout=2.0,
    )

    # The load-bearing observable: how many responders got the 1.20x join boost.
    # Under the unlocked code, both pass the stale cap-check and ``_apply_join_boost``
    # runs twice — both stocks hit $120. Under the per-ping lock, exactly cap (=1)
    # responder is boosted.
    first_stock = await fake_price_repo.get(GUILD, "r_first")
    second_stock = await fake_price_repo.get(GUILD, "r_second")
    assert first_stock is not None and second_stock is not None
    boosted_count = sum(
        1 for s in (first_stock, second_stock) if s.current == Decimal("120.00")
    )
    unboosted_count = sum(
        1 for s in (first_stock, second_stock) if s.current == Decimal("100.00")
    )
    assert boosted_count == 1, (
        "CF-2 cap violation: more than one responder got the join price boost "
        f"(r_first={first_stock.current}, r_second={second_stock.current}); "
        "per-ping lock must serialise cap-check + write"
    )
    assert unboosted_count == 1

    # The losing responder must still be tracked somewhere on the session
    # so they receive periodic boosts via ``extra_joiners`` (no slot left silent).
    session = await store.get(42)
    assert session is not None
    recorded = set(session.first_10_joiners) | set(session.extra_joiners)
    assert recorded == {"r_first", "r_second"}


# ---------------------------------------------------------------------------
# CF-2 stronger mutation pin (Phase 12b iter-1 LOW-1 follow-up)
# ---------------------------------------------------------------------------


class _NoOpLockManager(LockManager):
    """Pass-through ``LockManager`` whose ``locked()`` acquires nothing.

    Drop-in replacement that satisfies the type contract while stripping the
    per-key serialisation. Used by the CF-2 stronger-pin test (below) to
    inject the "regression" where the per-ping lock is removed, then assert
    the cap is violated under deterministically-staged concurrent responders.
    """

    @asynccontextmanager
    async def locked(self, *user_ids: str) -> AsyncIterator[None]:
        """Yield immediately without acquiring any lock."""
        yield


class _GetBarrierPingSessionStore(VoicePingSessionStore):
    """Variant whose ``get`` parks on a barrier on the first wave only.

    Forces two concurrent ``get`` calls from inside ``_reward_for_session`` to
    return the SAME stale snapshot, defeating the accidental FIFO
    serialisation provided by the inner ``VoicePingSessionStore._lock``. Used
    only by the CF-2 stronger-pin test to prove the per-ping
    :class:`LockManager` guard — not the inner store lock — is the
    load-bearing fence against cap violation.

    One-shot per instance: the first ``parties`` ``get`` calls all park; the
    flag flips after release and subsequent calls pass straight through. Do
    not reuse across waves.
    """

    def __init__(self, *, parties: int) -> None:
        super().__init__()
        self._get_barrier: asyncio.Barrier = asyncio.Barrier(parties)
        self._get_barrier_fired: bool = False

    async def get(self, message_id: int) -> VoicePingSession | None:
        """Park at the barrier on the first wave; pass through after."""
        result = await super().get(message_id)
        if not self._get_barrier_fired:
            await self._get_barrier.wait()
            self._get_barrier_fired = True
        return result


async def test_per_ping_lockmanager_guard_is_loadbearing_for_cf2(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """CF-2 stronger pin: the ``LockManager`` guard is load-bearing.

    The primary CF-2 test
    (:func:`test_concurrent_responders_respect_cap_under_per_ping_lock`)
    pins the FULL fix — both the
    ``async with self._locks.locked(self._ping_lock_key(...))`` wrapper AND
    the inner re-read together. But a regression that removes ONLY the
    ``locked()`` wrapper (keeping the inner re-read) still passes that test
    because the inner ``VoicePingSessionStore._lock`` FIFO scheduling
    accidentally serialises responder 1's write before responder 2's read.

    This test removes that accidental safety net by also barrier-staging the
    two concurrent ``get`` calls so they return the SAME stale snapshot.
    With the no-op LockManager, both responders observe
    ``first_10_joiners == []``, both pass the cap check, both apply the join
    boost → cap violated. With the real LockManager, only one responder
    holds the per-ping lock at a time → only one ``get`` runs at a time →
    the get-barrier never has two waiters and the gather would deadlock
    (verified locally: swapping ``_NoOpLockManager()`` for ``LockManager()``
    in this test reproduces the deadlock as ``asyncio.TimeoutError``).

    Load-bearing for: any future change that drops the
    ``self._locks.locked(self._ping_lock_key(...))`` wrapper while keeping
    the inner ``await self._ping_sessions.get(...)`` re-read. Closes the
    Phase 12b iter-1 review LOW-1.
    """
    settings = default_settings.model_copy(update={"voice_ping_first_n_joiners": 1})
    store = _GetBarrierPingSessionStore(parties=2)
    # Inject the no-op LockManager so the per-ping serialisation is OFF.
    service = VoicePingService(
        guild_id=GUILD,
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=_NoOpLockManager(),
        settings=settings,
        ping_sessions=store,
    )

    ping_time = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    for uid in ("r_first", "r_second"):
        await fake_user_repo.upsert(GUILD, _account(uid))
        await fake_price_repo.upsert(GUILD, _stock(uid, current=Decimal("100.00")))
    await service.register_ping_message(
        message_id=42, host_id=HOST, channel_id=CHANNEL, timestamp=ping_time
    )

    response_now = ping_time + timedelta(seconds=30)
    await asyncio.wait_for(
        asyncio.gather(
            service.reward_voice_ping_response(
                responder_id="r_first", channel_id=CHANNEL, now=response_now
            ),
            service.reward_voice_ping_response(
                responder_id="r_second", channel_id=CHANNEL, now=response_now
            ),
        ),
        timeout=2.0,
    )

    first_stock = await fake_price_repo.get(GUILD, "r_first")
    second_stock = await fake_price_repo.get(GUILD, "r_second")
    assert first_stock is not None and second_stock is not None
    boosted_count = sum(
        1 for s in (first_stock, second_stock) if s.current == Decimal("120.00")
    )
    # Negative assertion: without the LockManager guard, BOTH responders pass
    # the stale cap check and both get boosted. If this ever becomes
    # ``boosted_count == 1`` again, it means some OTHER mechanism (a future
    # inner-store lock change, a store snapshot change, etc.) is masking the
    # missing per-ping lock — at which point the primary CF-2 test is no
    # longer fenced and the regression contract needs re-derivation.
    assert boosted_count == 2, (
        "CF-2 stronger pin: with a no-op LockManager + staged-`get` race, both "
        f"responders MUST observe the stale snapshot and both be boosted "
        f"(r_first={first_stock.current}, r_second={second_stock.current}). "
        "If only one is boosted, the LockManager guard is no longer the sole "
        "fence — re-derive the regression contract."
    )
