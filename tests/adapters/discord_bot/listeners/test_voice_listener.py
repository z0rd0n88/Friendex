"""Tests for :class:`VoiceListener` — ``on_voice_state_update``.

The listener distinguishes three voice transitions:

* **JOIN** (``before.channel is None`` AND ``after.channel is not None``):
  ``ActivityService.handle_voice_join`` →
  ``VoicePingService.reward_voice_ping_response`` →
  ``VcBoostTask.set_store_for_guild`` (seeded from
  ``VoicePingService.collect_extra_boosts``).
* **LEAVE** (``before.channel is not None`` AND ``after.channel is None``):
  ``ActivityService.handle_voice_leave`` with the elapsed ``stay_minutes``
  computed from the live :class:`VoiceSessionStore` snapshot.
* **SWITCH** (both non-None, ``before.channel != after.channel``):
  finalise old channel FIRST (``handle_voice_leave``), then create new
  (``handle_voice_join``), then ``reward_voice_ping_response`` on the new
  channel, then seed the per-guild VC-boost store.

Bot user is ignored; ``DomainError`` propagates uncaught.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog

from friendex.adapters.discord_bot.listeners.voice_listener import VoiceListener
from friendex.application.voice_session_store import VoiceSessionStore
from friendex.domain.errors import OptedOut
from friendex.domain.models import VcExtraBoost, VoiceSession

if TYPE_CHECKING:
    from collections.abc import Callable


# ---------------------------------------------------------------------------
# Helpers


def _voice_session_store_factory_for(
    store: VoiceSessionStore,
) -> Callable[[str], VoiceSessionStore]:
    """Return a factory that yields ``store`` for any guild id."""

    def _factory(_guild_id: str) -> VoiceSessionStore:
        return store

    return _factory


def _vc_boost_task() -> MagicMock:
    """Build a minimal ``VcBoostTask`` stand-in exposing ``set_store_for_guild``."""
    task = MagicMock(name="VcBoostTask")
    task.set_store_for_guild = MagicMock(name="set_store_for_guild")
    return task


def _fixed_clock(when: datetime) -> Callable[[], datetime]:
    """Return a clock callable that always emits ``when``."""

    def _clock() -> datetime:
        return when

    return _clock


def _build_listener(
    *,
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
    voice_session_store: VoiceSessionStore,
    vc_boost_task: MagicMock,
    clock: Callable[[], datetime] | None = None,
) -> VoiceListener:
    """Build a :class:`VoiceListener` with the provided wiring."""
    return VoiceListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store_factory=_voice_session_store_factory_for(
            voice_session_store
        ),
        vc_boost_task=vc_boost_task,
        clock=clock,
    )


# ---------------------------------------------------------------------------
# JOIN


async def test_on_voice_state_update_join_calls_join_reward_and_seed(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """JOIN fires join → reward → set_store_for_guild in that order."""
    when = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    boosts = [
        VcExtraBoost(
            user_id="9999",
            ping_time=when,
            last_boost=when,
            end_time=when,
        )
    ]
    voice_ping_service.collect_extra_boosts.return_value = boosts
    store = VoiceSessionStore()
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
        clock=_fixed_clock(when),
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=None)
    after = fake_voice_state(channel_id=5555)

    await listener.on_voice_state_update(member, before, after)

    activity_service.handle_voice_join.assert_awaited_once_with(
        user_id="42",
        channel_id=5555,
        joined_from_ping=False,
    )
    voice_ping_service.reward_voice_ping_response.assert_awaited_once_with(
        responder_id="42",
        channel_id=5555,
        now=when,
    )
    voice_ping_service.collect_extra_boosts.assert_awaited_once_with(now=when)
    task.set_store_for_guild.assert_called_once_with("999", boosts)


# ---------------------------------------------------------------------------
# LEAVE


async def test_on_voice_state_update_leave_calls_leave_with_stay_minutes(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """LEAVE fires handle_voice_leave with stay_minutes computed from the store."""
    start = datetime(2026, 5, 25, 11, 0, 0, tzinfo=UTC)  # 1h ago
    when = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    store = VoiceSessionStore()
    await store.set(
        VoiceSession(
            user_id="42",
            channel_id=5555,
            start=start,
            from_ping_message_ids=set(),
        )
    )
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
        clock=_fixed_clock(when),
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=None)

    await listener.on_voice_state_update(member, before, after)

    activity_service.handle_voice_leave.assert_awaited_once_with(
        user_id="42",
        channel_id=5555,
        stay_minutes=60.0,
        joined_from_ping=False,
    )
    # LEAVE must NOT call the ping reward or seed the task — only JOIN/SWITCH do.
    voice_ping_service.reward_voice_ping_response.assert_not_called()
    task.set_store_for_guild.assert_not_called()


async def test_on_voice_state_update_leave_passes_joined_from_ping_true(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """LEAVE forwards ``joined_from_ping=True`` when the session was ping-linked."""
    start = datetime(2026, 5, 25, 11, 0, 0, tzinfo=UTC)
    when = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    store = VoiceSessionStore()
    await store.set(
        VoiceSession(
            user_id="42",
            channel_id=5555,
            start=start,
            from_ping_message_ids={111},
        )
    )
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
        clock=_fixed_clock(when),
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=None)

    await listener.on_voice_state_update(member, before, after)

    activity_service.handle_voice_leave.assert_awaited_once_with(
        user_id="42",
        channel_id=5555,
        stay_minutes=60.0,
        joined_from_ping=True,
    )


async def test_on_voice_state_update_leave_without_live_session_skips(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """LEAVE with no recorded session (e.g. restart-while-in-VC) is a silent no-op."""
    store = VoiceSessionStore()  # empty
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=None)

    await listener.on_voice_state_update(member, before, after)

    activity_service.handle_voice_leave.assert_not_called()


# ---------------------------------------------------------------------------
# SWITCH (B2 + B6 mutation-hardening on ordering)


async def test_on_voice_state_update_switch_finalises_old_before_new(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """SWITCH calls leave(OLD) FIRST, then join(NEW), then reward + seed.

    Load-bearing for B6: if the order is flipped (join-new before
    leave-old), the assertion on ``mock_calls`` order fails.
    """
    start = datetime(2026, 5, 25, 11, 30, 0, tzinfo=UTC)  # 30min ago
    when = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    store = VoiceSessionStore()
    await store.set(
        VoiceSession(
            user_id="42",
            channel_id=5555,
            start=start,
            from_ping_message_ids=set(),
        )
    )
    voice_ping_service.collect_extra_boosts.return_value = []
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
        clock=_fixed_clock(when),
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=6666)

    await listener.on_voice_state_update(member, before, after)

    # leave(OLD=5555) BEFORE join(NEW=6666). Pull the recorded call order
    # from the shared activity-service mock and assert positionally.
    call_names = [c[0] for c in activity_service.mock_calls if c[0] != ""]
    leave_index = call_names.index("handle_voice_leave")
    join_index = call_names.index("handle_voice_join")
    assert leave_index < join_index, (
        f"SWITCH order violated: leave_index={leave_index}, join_index={join_index}; "
        "must finalise old before creating new"
    )

    activity_service.handle_voice_leave.assert_awaited_once_with(
        user_id="42",
        channel_id=5555,
        stay_minutes=30.0,
        joined_from_ping=False,
    )
    activity_service.handle_voice_join.assert_awaited_once_with(
        user_id="42",
        channel_id=6666,
        joined_from_ping=False,
    )
    # Reward fires on the NEW channel.
    voice_ping_service.reward_voice_ping_response.assert_awaited_once_with(
        responder_id="42",
        channel_id=6666,
        now=when,
    )
    task.set_store_for_guild.assert_called_once_with("999", [])


# ---------------------------------------------------------------------------
# Bot-skip


async def test_on_voice_state_update_skips_bot_member(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """Bot voice transitions are silently dropped (signoff decision 3)."""
    store = VoiceSessionStore()
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
    )

    member = fake_member(user_id=99, guild_id=999)
    member.bot = True
    before = fake_voice_state(channel_id=None)
    after = fake_voice_state(channel_id=5555)

    await listener.on_voice_state_update(member, before, after)

    activity_service.handle_voice_join.assert_not_called()
    activity_service.handle_voice_leave.assert_not_called()
    voice_ping_service.reward_voice_ping_response.assert_not_called()
    task.set_store_for_guild.assert_not_called()


# ---------------------------------------------------------------------------
# No-op transition (same channel before and after)


async def test_on_voice_state_update_same_channel_is_noop(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """Mute / deafen / video toggles (same channel) fire no services."""
    store = VoiceSessionStore()
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=5555)

    await listener.on_voice_state_update(member, before, after)

    activity_service.handle_voice_join.assert_not_called()
    activity_service.handle_voice_leave.assert_not_called()
    voice_ping_service.reward_voice_ping_response.assert_not_called()
    task.set_store_for_guild.assert_not_called()


# ---------------------------------------------------------------------------
# DomainError propagation (B7)


async def test_on_voice_state_update_propagates_domain_error(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """A DomainError raised by ``handle_voice_join`` surfaces uncaught."""
    activity_service.handle_voice_join.side_effect = OptedOut(target_id="42")
    store = VoiceSessionStore()
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=None)
    after = fake_voice_state(channel_id=5555)

    with pytest.raises(OptedOut):
        await listener.on_voice_state_update(member, before, after)


# ---------------------------------------------------------------------------
# Wave 1 (#84 H): SWITCH leave failure must NOT skip the subsequent join


async def test_switch_leave_failure_does_not_skip_subsequent_join(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """SWITCH: if ``_do_leave`` raises, ``_do_join`` MUST still run.

    The original code awaited ``_do_leave`` then ``_do_join`` unguarded — a
    transient error on the leave (e.g. a stale stock row, a transient SQLite
    write error) would skip the join entirely, leaving the member in the
    listener's volatile state as if they had not transitioned channels.

    Mutation-hardening: a regression that drops the try/except (or
    re-raises) will cause this test to fail because ``_do_join`` is never
    called.

    PR #94 review (M1): the failure log now flows through structlog rather
    than stdlib ``logging`` so the structured kwargs survive the JSON
    renderer. The capture mechanism is ``structlog.testing.capture_logs()``
    rather than ``caplog`` because the production logger factory bypasses
    stdlib (``PrintLoggerFactory`` per ``adapters/config.py``).
    """
    start = datetime(2026, 5, 25, 11, 30, 0, tzinfo=UTC)
    when = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    store = VoiceSessionStore()
    await store.set(
        VoiceSession(
            user_id="42",
            channel_id=5555,
            start=start,
            from_ping_message_ids=set(),
        )
    )
    # leave-side fails with a transient runtime error.
    boom = RuntimeError("transient persistence error on leave")
    activity_service.handle_voice_leave.side_effect = boom
    voice_ping_service.collect_extra_boosts.return_value = []
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
        clock=_fixed_clock(when),
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=6666)

    with structlog.testing.capture_logs() as captured:
        await listener.on_voice_state_update(member, before, after)

    # leave fired and raised — confirmed by side_effect surfacing in caplog
    activity_service.handle_voice_leave.assert_awaited_once()
    # CRITICAL: join MUST still have run on the NEW channel.
    activity_service.handle_voice_join.assert_awaited_once_with(
        user_id="42",
        channel_id=6666,
        joined_from_ping=False,
    )
    voice_ping_service.reward_voice_ping_response.assert_awaited_once_with(
        responder_id="42",
        channel_id=6666,
        now=when,
    )
    # The leave failure was logged at ERROR with the original RuntimeError
    # carried via ``exc_info`` so operators can diagnose the leave after the
    # fact. Structlog captures ``exc_info=True`` as either the bare sentinel
    # or the resolved exception tuple, depending on the configured
    # processor chain; either shape is acceptable here — the contract is
    # "the exception survived the log call".
    error_records = [r for r in captured if r["log_level"] == "error"]
    assert error_records, (
        "expected an ERROR-level structlog entry for the swallowed leave failure"
    )
    assert error_records[0]["event"] == "voice_listener.switch_leave_failed"
    assert error_records[0].get("exc_info") is not None


# ---------------------------------------------------------------------------
# PR #94 review (M1) — structlog migration of the swallowed-leave log
#
# Pre-fix the listener held ``logger = logging.getLogger(__name__)`` and
# passed structured fields via the stdlib ``extra={}`` kwarg. ``configure_
# logging`` in ``adapters/config.py`` clears stdlib handlers and sets the
# bare ``%(message)s`` format — so ``extra={}`` was silently dropped from
# every rendered log line. Same silent-failure class the rest of the PR is
# migrating elsewhere. Pin: the swallowed-leave log goes through structlog
# with the structured fields visible as top-level keys.
#
# This test uses ``structlog.testing.capture_logs()`` rather than ``caplog``
# because the production processor chain runs through structlog's
# wrapper-class — the caplog-based ``test_switch_leave_failure_does_not_
# skip_subsequent_join`` above remains the behavioural pin (the leave
# fails, the join still runs), and this one pins the *log shape*.


async def test_switch_leave_failure_log_carries_structured_fields_via_structlog(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """``logger.error("event", k=v, exc_info=...)`` round-trips through structlog.

    Asserts the event name + every structured key (``guild_id``, ``user_id``,
    ``before_channel_id``, ``after_channel_id``) lands as a top-level field
    in the captured log dict — exactly the surface the production
    ``JSONRenderer`` indexes. The pre-fix ``extra={}`` shape would surface
    as ``extra={...}`` (a nested dict) and the JSON sink would silently
    drop it.
    """
    start = datetime(2026, 5, 25, 11, 30, 0, tzinfo=UTC)
    when = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    store = VoiceSessionStore()
    await store.set(
        VoiceSession(
            user_id="42",
            channel_id=5555,
            start=start,
            from_ping_message_ids=set(),
        )
    )
    boom = RuntimeError("transient persistence error on leave")
    activity_service.handle_voice_leave.side_effect = boom
    voice_ping_service.collect_extra_boosts.return_value = []
    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
        clock=_fixed_clock(when),
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=6666)

    with structlog.testing.capture_logs() as captured:
        await listener.on_voice_state_update(member, before, after)

    error_records = [r for r in captured if r["log_level"] == "error"]
    assert error_records, (
        "expected an ERROR-level structlog entry for the swallowed leave failure"
    )
    rec = error_records[0]
    assert rec["event"] == "voice_listener.switch_leave_failed"
    # The four structured fields MUST be top-level keys, not nested in an
    # ``extra`` sub-dict — that's the silent-failure trap this fix removes.
    assert rec["guild_id"] == "999"
    assert rec["user_id"] == "42"
    assert rec["before_channel_id"] == 5555
    assert rec["after_channel_id"] == 6666
    # ``exc_info=True`` survives the structlog round-trip as either the
    # bare sentinel or the exception instance, per the configured processor
    # chain — ``capture_logs`` captures whatever the call site passed.
    assert rec.get("exc_info") is not None


# ---------------------------------------------------------------------------
# Issue #84 H — SWITCH: join failure must NOT be swallowed
#
# The Wave 1 (#84 H) fix wraps ``_do_leave`` in try/except so a leave failure
# does not skip ``_do_join``. The fix contract is asymmetric:
#   * Leave exception: caught, logged, join MUST still run.
#   * Join exception: NOT caught — propagates to the caller.
#
# This test pins that asymmetry. A regression that extends the try/except to
# also swallow join failures would make this test pass incorrectly by never
# raising — so the test asserts that the join exception propagates.


async def test_switch_join_failure_propagates_when_leave_succeeds(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """SWITCH: a ``_do_join`` failure propagates — it is NOT swallowed.

    The Wave 1 fix isolates only the leave failure; join failures must still
    surface so the caller (the Discord event loop) knows the join did not
    complete. Swallowing a join failure would desync the bot's volatile state
    from reality just as silently as the original leave-skip bug.

    Mutation-hardening: a regression that wraps ``_do_join`` in the same
    try/except will cause this test to fail because the exception is no
    longer raised.
    """
    start = datetime(2026, 5, 25, 11, 30, 0, tzinfo=UTC)
    when = datetime(2026, 5, 25, 12, 0, 0, tzinfo=UTC)
    store = VoiceSessionStore()
    await store.set(
        VoiceSession(
            user_id="42",
            channel_id=5555,
            start=start,
            from_ping_message_ids=set(),
        )
    )
    # Leave succeeds; join raises a transient error.
    activity_service.handle_voice_leave.return_value = None
    activity_service.handle_voice_join.side_effect = RuntimeError(
        "transient DB error on join"
    )
    voice_ping_service.collect_extra_boosts.return_value = []

    task = _vc_boost_task()
    listener = _build_listener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store=store,
        vc_boost_task=task,
        clock=_fixed_clock(when),
    )

    member = fake_member(user_id=42, guild_id=999)
    before = fake_voice_state(channel_id=5555)
    after = fake_voice_state(channel_id=6666)

    # The join error MUST propagate — not be swallowed.
    with pytest.raises(RuntimeError, match="transient DB error on join"):
        await listener.on_voice_state_update(member, before, after)

    # Leave was called and succeeded (confirming SWITCH code path reached it).
    activity_service.handle_voice_leave.assert_awaited_once()
    # Join was called (the leave-swallow did not prevent the attempt).
    activity_service.handle_voice_join.assert_awaited_once()


# ---------------------------------------------------------------------------
# Per-guild factory routing


async def test_on_voice_state_update_routes_through_per_guild_factory(
    fake_member: Callable[..., MagicMock],
    fake_voice_state: Callable[..., MagicMock],
    activity_service: AsyncMock,
    voice_ping_service: AsyncMock,
) -> None:
    """Both service factories are called with ``str(member.guild.id)``."""
    seen_activity: list[str] = []
    seen_ping: list[str] = []

    def activity_factory(guild_id: str) -> object:
        seen_activity.append(guild_id)
        return activity_service

    def ping_factory(guild_id: str) -> object:
        seen_ping.append(guild_id)
        return voice_ping_service

    store = VoiceSessionStore()
    voice_ping_service.collect_extra_boosts.return_value = []

    listener = VoiceListener(
        activity_service_factory=activity_factory,
        voice_ping_service_factory=ping_factory,
        voice_session_store_factory=_voice_session_store_factory_for(store),
        vc_boost_task=_vc_boost_task(),
    )

    member = fake_member(user_id=42, guild_id=54321)
    before = fake_voice_state(channel_id=None)
    after = fake_voice_state(channel_id=5555)

    await listener.on_voice_state_update(member, before, after)

    assert seen_activity == ["54321"]
    assert seen_ping == ["54321"]


# ---------------------------------------------------------------------------
# Cog registration sanity


def test_voice_listener_is_a_cog() -> None:
    """The listener subclasses ``commands.Cog`` so Phase 13 can ``add_cog`` it."""
    from discord.ext import commands

    assert issubclass(VoiceListener, commands.Cog)


def test_voice_listener_registers_on_voice_state_update_listener(
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """``on_voice_state_update`` is decorated with :meth:`commands.Cog.listener`."""
    listener = VoiceListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        voice_session_store_factory=_voice_session_store_factory_for(
            VoiceSessionStore()
        ),
        vc_boost_task=_vc_boost_task(),
    )
    names = [name for name, _ in listener.get_listeners()]
    assert "on_voice_state_update" in names
