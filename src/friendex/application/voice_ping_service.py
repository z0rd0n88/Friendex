"""Application service for voice-ping detection and responder rewards.

When a host pings a VC role, a :class:`~friendex.domain.models.VoicePingSession`
is opened; members who then join the *same* voice channel within the response
window are "responders". This service handles the three sides of that flow:

* :meth:`register_ping_message` — opens a ping session and credits the host;
* :meth:`reward_voice_ping_response` — rewards a responder (engagement credit
  scaled by response speed, plus a one-time price boost for the first N unique
  joiners; later joiners are tracked as ``extra_joiners`` for the periodic-boost
  task) and credits the host per responder;
* :meth:`cleanup_expired_pings` — sweeps ping sessions past the window.

**Volatile ping state.** Open ping sessions live in an injected in-memory
:class:`~friendex.application.voice_session_store.VoicePingSessionStore` (a dict
guarded by an :class:`asyncio.Lock`) — intentionally not persisted, mirroring the
original ``voice_ping_sessions`` dict, since an open ping is meaningless after a
restart.

**Guild scoping (ADR-0001) + concurrency + immutability** follow the same rules
as :class:`~friendex.application.activity_service.ActivityService`: ``guild_id``
is a constructor argument, every user mutation serialises under
``lock_manager.locked(user_id)``, and stored aggregates are replaced (never
mutated in place) and round-tripped through ``upsert``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    UserAccount,
    VoicePingSession,
)
from friendex.domain.price_engine import apply_floor_stall

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.interfaces import IPriceRepo, IUserRepo
    from friendex.application.lock_manager import LockManager
    from friendex.application.voice_session_store import VoicePingSessionStore


class VoicePingService:
    """Detects VC pings and rewards responders who join afterwards."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        lock_manager: LockManager,
        settings: Settings,
        ping_sessions: VoicePingSessionStore,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._locks = lock_manager
        self._settings = settings
        self._ping_sessions = ping_sessions

    # -- ping lifecycle -----------------------------------------------------

    async def register_ping_message(
        self,
        message_id: int,
        host_id: str,
        channel_id: int,
        timestamp: datetime,
    ) -> None:
        """Open a ping session for ``message_id`` and credit the host.

        The host earns one ``role_ping_joins`` point (today + week) for issuing
        the ping, matching the original ``on_message`` voice-ping branch.
        """
        session = VoicePingSession(
            message_id=message_id,
            host_id=host_id,
            channel_id=channel_id,
            timestamp=timestamp,
            first_10_joiners=[],
            extra_joiners=[],
        )
        await self._ping_sessions.set(session)
        await self._credit(host_id, role_ping_joins=1.0)

    async def cleanup_expired_pings(self, now: datetime) -> int:
        """Evict ping sessions older than the response window; return the count.

        A session expires once ``now - timestamp`` exceeds
        ``voice_ping_window_seconds``.
        """
        window = self._settings.voice_ping_window_seconds
        evicted = 0
        for session in await self._ping_sessions.list_all():
            age = (now - session.timestamp).total_seconds()
            if age > window and await self._ping_sessions.pop(session.message_id):
                evicted += 1
        return evicted

    # -- responder reward ---------------------------------------------------

    async def reward_voice_ping_response(
        self,
        responder_id: str,
        channel_id: int,
        now: datetime,
    ) -> None:
        """Reward ``responder_id`` for joining ``channel_id`` after a ping.

        Scans every open ping session; for each one whose channel matches, whose
        age is within the window, and whose host is not the responder, the
        responder is rewarded *once* per ping:

        * first N unique joiners get the one-time join price boost and are
          recorded in ``first_10_joiners``;
        * later joiners are recorded in ``extra_joiners`` (no price boost);
        * the responder earns speed-scaled engagement credit and the host earns
          a fixed per-responder credit.

        Idempotent per ``(ping, responder)``: a responder already recorded for a
        ping is skipped for that ping.
        """
        window = self._settings.voice_ping_window_seconds
        for session in await self._ping_sessions.list_all():
            if session.channel_id != channel_id:
                continue
            if responder_id == session.host_id:
                continue
            age = (now - session.timestamp).total_seconds()
            if age < 0 or age > window:
                continue
            if (
                responder_id in session.first_10_joiners
                or responder_id in session.extra_joiners
            ):
                continue  # idempotent: already rewarded for this ping

            await self._reward_for_session(session, responder_id, age)

    async def _reward_for_session(
        self,
        session: VoicePingSession,
        responder_id: str,
        age: float,
    ) -> None:
        """Apply the join placement, price boost, and engagement credit once."""
        cap = self._settings.voice_ping_first_n_joiners
        if len(session.first_10_joiners) < cap:
            updated = replace(
                session,
                first_10_joiners=[*session.first_10_joiners, responder_id],
            )
            await self._ping_sessions.set(updated)
            await self._apply_join_boost(responder_id)
        else:
            updated = replace(
                session,
                extra_joiners=[*session.extra_joiners, responder_id],
            )
            await self._ping_sessions.set(updated)

        bonus = self._settings.voice_ping_base_points * self._speed_multiplier(age)
        await self._credit(responder_id, role_ping_join_minutes=bonus)
        await self._credit(
            session.host_id, role_ping_joins=self._settings.voice_ping_host_credit
        )

    def _speed_multiplier(self, age: float) -> float:
        """Return the engagement multiplier for a responder's reaction speed."""
        if age <= self._settings.fast_response_seconds:
            return self._settings.voice_ping_fast_multiplier
        if age <= self._settings.medium_response_seconds:
            return self._settings.voice_ping_medium_multiplier
        return self._settings.voice_ping_slow_multiplier

    async def _apply_join_boost(self, responder_id: str) -> None:
        """Apply the one-time first-N-joiner price boost to ``responder_id``."""
        min_price = Decimal(str(self._settings.min_price))
        boost = Decimal(str(self._settings.voice_ping_join_boost))
        async with self._locks.locked(responder_id):
            stock = await self._price_repo.get(self._guild_id, responder_id)
            if stock is None:
                return
            proposed = stock.current * boost
            new_price = apply_floor_stall(stock.current, proposed, min_price)
            await self._price_repo.upsert(
                self._guild_id, replace(stock, current=new_price)
            )

    # -- shared account helpers --------------------------------------------

    async def _credit(self, user_id: str, **deltas: float) -> None:
        """Add ``deltas`` to ``user_id``'s today + week buckets under its lock."""
        async with self._locks.locked(user_id):
            account = await self._get_or_create(user_id)
            today = replace(
                account.today,
                **{
                    name: getattr(account.today, name) + delta
                    for name, delta in deltas.items()
                },
            )
            week = replace(
                account.week,
                **{
                    name: getattr(account.week, name) + delta
                    for name, delta in deltas.items()
                },
            )
            await self._user_repo.upsert(
                self._guild_id, replace(account, today=today, week=week)
            )

    async def _get_or_create(self, user_id: str) -> UserAccount:
        """Return the stored account for ``user_id`` or a fresh default one."""
        existing = await self._user_repo.get(self._guild_id, user_id)
        if existing is not None:
            return existing
        now = datetime.now(tz=UTC)
        initial_cash = Decimal(str(self._settings.initial_cash))
        return UserAccount(
            user_id=user_id,
            cash_balance=initial_cash,
            net_worth=initial_cash,
            month_start_net_worth=initial_cash,
            long_positions={},
            short_positions={},
            today=ActivityBucket(bucket_start=now),
            week=ActivityBucket(bucket_start=now),
            daily=DailyProgress(last_claim=None, streak=0),
            last_activity=now,
        )
