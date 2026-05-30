"""``VoiceListener`` — drives the per-voice-transition service fan-out.

Distinguishes three transitions from a single ``on_voice_state_update`` event:

* **JOIN** (``before.channel is None``, ``after.channel is not None``):
  open a session via :meth:`ActivityService.handle_voice_join`, then
  :meth:`VoicePingService.reward_voice_ping_response` on the joined
  channel, then seed the per-guild
  :class:`~friendex.adapters.tasks.vc_boost_task.VcBoostTask` store with
  the current extra-joiner roster from
  :meth:`VoicePingService.collect_extra_boosts`.
* **LEAVE** (``before.channel is not None``, ``after.channel is None``):
  compute ``stay_minutes`` from the live
  :class:`~friendex.application.voice_session_store.VoiceSessionStore`
  snapshot, infer ``joined_from_ping`` from the session's linked
  ping-message ids, then call
  :meth:`ActivityService.handle_voice_leave`. No ping reward or task
  seed — the responder has already left.
* **SWITCH** (both channels non-None, different): finalise the OLD
  channel via ``handle_voice_leave`` FIRST, then open the new session
  via ``handle_voice_join``, then ``reward_voice_ping_response`` on
  the new channel, then seed the per-guild boost store. The
  leave-first ordering matches the original bot semantics (a SWITCH
  credits the old channel's voice minutes before starting a new
  session) and is verified by a mutation-hardened ordering test.

  **Wave 1 (#84 H) — SWITCH error isolation.** ``_do_leave`` runs under
  a ``try/except`` so a transient leave failure (stale stock row, write
  contention, etc.) cannot skip the subsequent ``_do_join`` — the member
  HAS already moved channels at the Discord level, so dropping the join
  would desync the listener's volatile state from reality. The leave
  failure is logged at ERROR with ``exc_info=True`` so operators can
  diagnose it after the fact.

The listener holds:

* a per-guild :class:`ActivityService` factory and
  :class:`VoicePingService` factory (Phase 9 service-factory convention);
* a per-guild :class:`VoiceSessionStore` factory — same singleton store
  the per-guild :class:`ActivityService` is built against, used here to
  compute ``stay_minutes`` and ``joined_from_ping`` for LEAVE/SWITCH;
* a single :class:`VcBoostTask` instance (the task is single-instance
  across all guilds — Phase 9 digest §3) whose
  :meth:`set_store_for_guild` is invoked after JOIN/SWITCH.

Bot voice transitions are silently dropped (signoff decision 3).
:class:`~friendex.domain.errors.DomainError` propagates uncaught (Phase 13
owns the central handler).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
from discord.ext import commands

if TYPE_CHECKING:
    from collections.abc import Callable

    import discord

    from friendex.adapters.tasks.vc_boost_task import VcBoostTask
    from friendex.application.activity_service import ActivityService
    from friendex.application.voice_ping_service import VoicePingService
    from friendex.application.voice_session_store import VoiceSessionStore


# PR #94 review (M1): pre-fix this module held ``logger = logging.getLogger(
# __name__)`` and passed structured fields via the stdlib ``extra={...}``
# kwarg. ``configure_logging`` (``adapters/config.py``) installs the bare
# ``%(message)s`` format on the stdlib root, so ``extra`` was silently
# dropped from every rendered log line — the same silent-failure pattern
# the rest of the Wave 2 PR is migrating away from. Structlog accepts the
# structured fields as keyword arguments natively, so the JSON renderer in
# the production processor chain emits ``guild_id`` / ``user_id`` /
# ``before_channel_id`` / ``after_channel_id`` as top-level keys.
_log = structlog.get_logger(__name__)


class VoiceListener(commands.Cog):
    """Routes :py:obj:`on_voice_state_update` to activity + ping + boost-task."""

    def __init__(
        self,
        *,
        activity_service_factory: Callable[[str], ActivityService],
        voice_ping_service_factory: Callable[[str], VoicePingService],
        voice_session_store_factory: Callable[[str], VoiceSessionStore],
        vc_boost_task: VcBoostTask,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._activity_factory = activity_service_factory
        self._voice_ping_factory = voice_ping_service_factory
        self._voice_session_store_factory = voice_session_store_factory
        self._vc_boost_task = vc_boost_task
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Dispatch the JOIN / LEAVE / SWITCH branch for ``member``.

        Same-channel transitions (mute / deafen / video toggles) are
        silently dropped; only channel-membership changes drive services.
        """
        if member.bot:
            return

        before_channel = before.channel
        after_channel = after.channel
        before_id = None if before_channel is None else int(before_channel.id)
        after_id = None if after_channel is None else int(after_channel.id)

        if before_id == after_id:
            return  # no-op (mute / deafen / video toggle within the same channel)

        guild_id = str(member.guild.id)
        user_id = str(member.id)
        now = self._clock()

        activity_service = self._activity_factory(guild_id)
        voice_ping_service = self._voice_ping_factory(guild_id)
        session_store = self._voice_session_store_factory(guild_id)

        if before_id is None and after_id is not None:
            # JOIN
            await self._do_join(
                activity_service=activity_service,
                voice_ping_service=voice_ping_service,
                guild_id=guild_id,
                user_id=user_id,
                channel_id=after_id,
                now=now,
            )
            return

        if before_id is not None and after_id is None:
            # LEAVE
            await self._do_leave(
                activity_service=activity_service,
                session_store=session_store,
                user_id=user_id,
                channel_id=before_id,
                now=now,
            )
            return

        if before_id is not None and after_id is not None:
            # SWITCH — finalise OLD FIRST, then create NEW, then reward + seed.
            # Wave 1 (#84 H): isolate the leave from the join. A transient
            # error on leave MUST NOT skip the join — the user has already
            # moved channels at the Discord level, so dropping the join
            # would desync our volatile state from reality. ``Exception``
            # (not ``BaseException``) keeps cancellation + shutdown working.
            try:
                await self._do_leave(
                    activity_service=activity_service,
                    session_store=session_store,
                    user_id=user_id,
                    channel_id=before_id,
                    now=now,
                )
            except Exception:
                # Structlog accepts the structured fields as keyword
                # arguments — they land as top-level keys in the JSON sink.
                # The pre-fix call passed them via stdlib ``extra={...}``,
                # which the bare ``%(message)s`` formatter dropped silently.
                # (PR #94 review M1.)
                _log.error(
                    "voice_listener.switch_leave_failed",
                    guild_id=guild_id,
                    user_id=user_id,
                    before_channel_id=before_id,
                    after_channel_id=after_id,
                    exc_info=True,
                )
            await self._do_join(
                activity_service=activity_service,
                voice_ping_service=voice_ping_service,
                guild_id=guild_id,
                user_id=user_id,
                channel_id=after_id,
                now=now,
            )

    async def _do_join(
        self,
        *,
        activity_service: ActivityService,
        voice_ping_service: VoicePingService,
        guild_id: str,
        user_id: str,
        channel_id: int,
        now: datetime,
    ) -> None:
        """JOIN body: open session, reward ping responders, seed boost task."""
        await activity_service.handle_voice_join(
            user_id=user_id,
            channel_id=channel_id,
            joined_from_ping=False,
        )
        await voice_ping_service.reward_voice_ping_response(
            responder_id=user_id,
            channel_id=channel_id,
            now=now,
        )
        boosts = await voice_ping_service.collect_extra_boosts(now=now)
        self._vc_boost_task.set_store_for_guild(guild_id, boosts)

    async def _do_leave(
        self,
        *,
        activity_service: ActivityService,
        session_store: VoiceSessionStore,
        user_id: str,
        channel_id: int,
        now: datetime,
    ) -> None:
        """LEAVE body: compute stay_minutes + joined_from_ping, then credit.

        A LEAVE event without a recorded live session is a silent no-op:
        this happens after a bot restart (volatile session state is lost
        on reboot) and is the safest behaviour — there is no defensible
        ``stay_minutes`` to credit.
        """
        session = await session_store.get(user_id)
        if session is None:
            return
        stay_minutes = (now - session.start).total_seconds() / 60.0
        joined_from_ping = bool(session.from_ping_message_ids)
        await activity_service.handle_voice_leave(
            user_id=user_id,
            channel_id=channel_id,
            stay_minutes=stay_minutes,
            joined_from_ping=joined_from_ping,
        )
