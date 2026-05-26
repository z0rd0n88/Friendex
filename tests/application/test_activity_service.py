"""Behavioural tests for :class:`ActivityService` (Phase 8a).

The service records Discord activity (messages, reactions, voice joins/leaves)
into a user's today + week :class:`ActivityBucket`s and applies the one-time
voice stay price boost. Every assertion targets the *observable* outcome of a
public method against the in-memory fakes — never service internals — so a
service that passes here also passes against the SQLite adapters.

Acceptance criteria pinned here:

* A1 — a text message increments ``today.text_msgs`` AND ``week.text_msgs``;
* A2 — a media message increments the media counters;
* A3 — media in a photo-bonus channel grants the role_ping bonus;
* A4 — a reply increments ``reply_count``;
* A5 — ``handle_voice_leave`` with ``stay_minutes >= 60`` applies the 50% boost;
* A6 — ``reset_today_buckets`` resets only today, not week.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.activity_service import ActivityService
from friendex.application.lock_manager import LockManager
from friendex.application.voice_session_store import VoiceSessionStore
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    Stock,
    UserAccount,
    VoiceSession,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from tests.application.fakes.fake_repos import FakePriceRepo, FakeUserRepo

GUILD = "100000000000000001"
USER = "5001"
PHOTO_CHANNEL = 4242
PLAIN_CHANNEL = 7777


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
) -> ActivityService:
    """Construct an :class:`ActivityService` scoped to ``GUILD``."""
    return ActivityService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        lock_manager=LockManager(),
        settings=settings,
        voice_sessions=VoiceSessionStore(),
    )


async def test_text_message_increments_today_and_week_text_msgs(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A1: a plain text message bumps both today and week ``text_msgs``."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.record_message(
        author_id=USER,
        has_attachment=False,
        is_reply=False,
        channel_id=PLAIN_CHANNEL,
    )

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.text_msgs == 1
    assert account.week.text_msgs == 1
    assert account.today.media_msgs == 0


async def test_media_message_increments_media_counters(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A2: an attachment message bumps today + week ``media_msgs`` (not text)."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.record_message(
        author_id=USER,
        has_attachment=True,
        is_reply=False,
        channel_id=PLAIN_CHANNEL,
    )

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.media_msgs == 1
    assert account.week.media_msgs == 1
    assert account.today.text_msgs == 0


async def test_media_in_photo_bonus_channel_grants_role_ping_bonus(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A3: media in a photo-bonus channel grants the role_ping_join_minutes bonus."""
    settings = default_settings.model_copy(
        update={"photo_bonus_channel_ids": [PHOTO_CHANNEL]}
    )
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, settings)

    await service.record_message(
        author_id=USER,
        has_attachment=True,
        is_reply=False,
        channel_id=PHOTO_CHANNEL,
    )

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.media_msgs == 1
    assert account.today.role_ping_join_minutes == settings.photo_bonus_points
    assert account.week.role_ping_join_minutes == settings.photo_bonus_points


async def test_media_outside_photo_channel_grants_no_bonus(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A3 (negative): media outside a bonus channel earns no role_ping bonus."""
    settings = default_settings.model_copy(
        update={"photo_bonus_channel_ids": [PHOTO_CHANNEL]}
    )
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, settings)

    await service.record_message(
        author_id=USER,
        has_attachment=True,
        is_reply=False,
        channel_id=PLAIN_CHANNEL,
    )

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.role_ping_join_minutes == 0.0


async def test_reply_increments_reply_count(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A4: a reply message bumps today + week ``reply_count``."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.record_message(
        author_id=USER,
        has_attachment=False,
        is_reply=True,
        channel_id=PLAIN_CHANNEL,
    )

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.reply_count == 1
    assert account.week.reply_count == 1
    # A reply is still a text message.
    assert account.today.text_msgs == 1


async def test_voice_leave_long_stay_applies_50pct_boost(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A5: leaving after >= 60 minutes applies the one-time 50% price boost."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_price_repo.upsert(GUILD, _stock(USER, current=Decimal("100.00")))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.handle_voice_leave(
        user_id=USER,
        channel_id=PLAIN_CHANNEL,
        stay_minutes=75.0,
        joined_from_ping=True,
    )

    stock = await fake_price_repo.get(GUILD, USER)
    assert stock is not None
    # 100.00 * 1.50 = 150.00, well above the $70 floor.
    assert stock.current == Decimal("150.00")


async def test_voice_leave_short_stay_no_boost(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A5 (negative): a < 60 minute stay leaves the price unchanged."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_price_repo.upsert(GUILD, _stock(USER, current=Decimal("100.00")))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.handle_voice_leave(
        user_id=USER,
        channel_id=PLAIN_CHANNEL,
        stay_minutes=10.0,
        joined_from_ping=False,
    )

    stock = await fake_price_repo.get(GUILD, USER)
    assert stock is not None
    assert stock.current == Decimal("100.00")
    # Voice minutes are still credited regardless of the boost threshold.
    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.voice_minutes == 10.0


async def test_reset_today_buckets_resets_only_today(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A6: ``reset_today_buckets`` clears today's counters but not week's."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    # Build up some activity in both buckets via three text messages.
    for _ in range(3):
        await service.record_message(
            author_id=USER,
            has_attachment=False,
            is_reply=False,
            channel_id=PLAIN_CHANNEL,
        )

    before = await fake_user_repo.get(GUILD, USER)
    assert before is not None
    assert before.today.text_msgs == 3
    assert before.week.text_msgs == 3

    await service.reset_today_buckets()

    after = await fake_user_repo.get(GUILD, USER)
    assert after is not None
    assert after.today.text_msgs == 0
    assert after.week.text_msgs == 3


async def test_reset_week_buckets_resets_only_week(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """``reset_week_buckets`` clears week's counters but not today's."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    for _ in range(2):
        await service.record_message(
            author_id=USER,
            has_attachment=False,
            is_reply=False,
            channel_id=PLAIN_CHANNEL,
        )

    await service.reset_week_buckets()

    after = await fake_user_repo.get(GUILD, USER)
    assert after is not None
    assert after.week.text_msgs == 0
    assert after.today.text_msgs == 2


async def test_record_reaction_increments_reaction_count(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """``record_reaction`` bumps today + week ``reaction_count``."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.record_reaction(USER)

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.reaction_count == 1
    assert account.week.reaction_count == 1


async def test_record_message_creates_account_for_unknown_user(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """An activity event for a never-seen user lazily creates a default account."""
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)
    assert await fake_user_repo.get(GUILD, "brand-new") is None

    await service.record_message(
        author_id="brand-new",
        has_attachment=False,
        is_reply=False,
        channel_id=PLAIN_CHANNEL,
    )

    account = await fake_user_repo.get(GUILD, "brand-new")
    assert account is not None
    assert account.today.text_msgs == 1
    assert account.cash_balance == Decimal(str(default_settings.initial_cash))


async def test_handle_voice_join_opens_session_and_refreshes_activity(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """``handle_voice_join`` records a live session and bumps last_activity."""
    store = VoiceSessionStore()
    service = ActivityService(
        guild_id=GUILD,
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=LockManager(),
        settings=default_settings,
        voice_sessions=store,
    )

    await service.handle_voice_join(
        user_id=USER, channel_id=PLAIN_CHANNEL, joined_from_ping=True
    )

    session = await store.get(USER)
    assert session is not None
    assert session.channel_id == PLAIN_CHANNEL
    assert session.from_ping_message_ids == set()
    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None


async def test_voice_leave_with_no_stock_does_not_crash(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """A long stay for a user with no stock row simply credits voice minutes."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.handle_voice_leave(
        user_id=USER,
        channel_id=PLAIN_CHANNEL,
        stay_minutes=90.0,
        joined_from_ping=True,
    )

    assert await fake_price_repo.get(GUILD, USER) is None
    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.today.voice_minutes == 90.0


async def test_set_opt_in_and_mark_intro_shown(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """``set_opt_in`` and ``mark_intro_shown`` flip their respective flags."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(fake_user_repo, fake_price_repo, default_settings)

    await service.set_opt_in(USER, value=False)
    await service.mark_intro_shown(USER)

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.opt_in is False
    assert account.intro_shown is True


async def test_voice_session_store_set_get_pop_and_link_ping() -> None:
    """The volatile :class:`VoiceSessionStore` round-trips and links pings."""
    store = VoiceSessionStore()
    assert await store.get(USER) is None

    session = VoiceSession(
        user_id=USER,
        channel_id=PLAIN_CHANNEL,
        start=datetime.now(tz=UTC),
        from_ping_message_ids=set(),
    )
    await store.set(session)
    fetched = await store.get(USER)
    assert fetched is not None
    assert fetched.channel_id == PLAIN_CHANNEL

    # Linking a ping mutates the live session's set in place (volatile state).
    await store.link_ping(USER, 999)
    linked = await store.get(USER)
    assert linked is not None
    assert 999 in linked.from_ping_message_ids

    # Linking for an unknown user is a no-op.
    await store.link_ping("ghost", 1)
    assert await store.get("ghost") is None

    popped = await store.pop(USER)
    assert popped is not None
    assert await store.get(USER) is None


class _BarrierUserRepo:
    """A :class:`FakeUserRepo`-shaped wrapper whose ``upsert`` parks on a barrier.

    Used by the per-guild lock isolation test to deterministically prove two
    service calls are *both* inside their critical sections at the same time:
    each ``upsert`` signals it has entered, then waits on a shared barrier that
    only releases once *both* have arrived. A serialising lock would prevent the
    second caller from ever reaching its barrier wait and the test's timeout
    would trip.
    """

    def __init__(self, inner: FakeUserRepo, barrier: asyncio.Barrier) -> None:
        self._inner = inner
        self._barrier = barrier
        self.entered: list[str] = []

    async def get(self, guild_id: str, user_id: str):  # type: ignore[no-untyped-def]
        return await self._inner.get(guild_id, user_id)

    async def upsert(self, guild_id: str, account) -> None:  # type: ignore[no-untyped-def]
        self.entered.append(guild_id)
        await self._barrier.wait()
        await self._inner.upsert(guild_id, account)

    async def delete(self, guild_id: str, user_id: str) -> None:
        await self._inner.delete(guild_id, user_id)

    async def list_all(self, guild_id: str):  # type: ignore[no-untyped-def]
        return await self._inner.list_all(guild_id)

    async def list_active_in_last(self, guild_id: str, seconds: float):  # type: ignore[no-untyped-def]
        return await self._inner.list_active_in_last(guild_id, seconds)


async def test_same_user_in_two_guilds_does_not_serialise_on_shared_lock_manager(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    default_settings: Settings,
) -> None:
    """ADR-0001: the same user in two guilds must not contend on one LockManager.

    Phase 14 injects a *single* shared :class:`LockManager` into every per-guild
    service scope. The lock key must therefore be the composite
    ``(guild_id, user_id)`` — keying on the bare ``user_id`` would make user
    ``USER`` in guild A serialise against the *same* ``USER`` in guild B,
    breaking the per-guild market isolation ADR-0001 commits to.

    Proof: two ``ActivityService``s — one per guild, sharing one
    :class:`LockManager` — each perform a ``record_message`` for the *same*
    ``USER`` concurrently. The injected ``upsert`` parks on an
    :class:`asyncio.Barrier(2)`: both must arrive before either proceeds. With a
    bare-``user_id`` lock key, guild B's mutation would block on guild A's held
    lock and never reach the barrier — :func:`asyncio.gather` would time out.
    With the composite key the two services lock on independent
    ``"<guild>:<user>"`` keys, both enter their critical sections concurrently,
    the barrier releases, and ``gather`` completes well under the timeout.
    """
    guild_a = "100000000000000001"
    guild_b = "200000000000000002"
    shared_locks = LockManager()
    barrier = asyncio.Barrier(2)
    barrier_repo = _BarrierUserRepo(fake_user_repo, barrier)

    service_a = ActivityService(
        guild_id=guild_a,
        user_repo=barrier_repo,  # type: ignore[arg-type]
        price_repo=fake_price_repo,
        lock_manager=shared_locks,
        settings=default_settings,
        voice_sessions=VoiceSessionStore(),
    )
    service_b = ActivityService(
        guild_id=guild_b,
        user_repo=barrier_repo,  # type: ignore[arg-type]
        price_repo=fake_price_repo,
        lock_manager=shared_locks,
        settings=default_settings,
        voice_sessions=VoiceSessionStore(),
    )

    await asyncio.wait_for(
        asyncio.gather(
            service_a.record_message(
                author_id=USER,
                has_attachment=False,
                is_reply=False,
                channel_id=PLAIN_CHANNEL,
            ),
            service_b.record_message(
                author_id=USER,
                has_attachment=False,
                is_reply=False,
                channel_id=PLAIN_CHANNEL,
            ),
        ),
        timeout=1.0,
    )

    # Both guilds saw an independent USER account materialise with one text msg.
    account_a = await fake_user_repo.get(guild_a, USER)
    account_b = await fake_user_repo.get(guild_b, USER)
    assert account_a is not None
    assert account_b is not None
    assert account_a.today.text_msgs == 1
    assert account_b.today.text_msgs == 1
    assert sorted(barrier_repo.entered) == [guild_a, guild_b]
