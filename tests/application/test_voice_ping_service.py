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
)

if TYPE_CHECKING:
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
