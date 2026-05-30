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

**Alt-account farming guard (issue #84 M / PR #93 C1).**
``register_ping_message`` accepts an optional ``host_role_member_ids``
snapshot — the set of member ids wearing the pinged VC role at the moment
the ping was issued. The snapshot lives on the injected
:class:`~friendex.application.voice_session_store.VoicePingSessionStore`
(NOT on the service instance) so it survives the per-guild factory
boundary: ``MessageListener`` builds instance A to register, and
``VoiceListener`` builds a distinct instance B to reward — both share the
same store via ``container._ping_session_store_for(guild_id)``, so the
snapshot is reachable from both call paths. On every reward attempt, a
responder whose id is in that set is rejected — closing the exploit where
a host pings their own role and then claims responder credit from an
alt-account that wears the same role. The host-self check
(``host_id == responder_id``) covers the host themselves; this guard
catches the alts. Omitting the kwarg disables the alt-account guard
(historic behaviour) so legacy callers and tests are not broken on
adoption.

**Guild scoping (ADR-0001) + concurrency + immutability** follow the same rules
as :class:`~friendex.application.activity_service.ActivityService`: ``guild_id``
is a constructor argument, every user mutation serialises under
``lock_manager.locked(self._lock_key(user_id))`` (composite
``"<guild_id>:<user_id>"`` key, so the same user in a different guild does not
contend on the single shared :class:`LockManager`), and stored aggregates are
replaced (never mutated in place) and round-tripped through ``upsert``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from friendex.application.account_seed import seed_user_account
from friendex.application.lock_manager import guild_lock_key
from friendex.domain.models import (
    PricePoint,
    UserAccount,
    VcExtraBoost,
    VoicePingSession,
)
from friendex.domain.price_engine import apply_floor_stall

if TYPE_CHECKING:
    from collections.abc import Collection

    from friendex.adapters.config import Settings
    from friendex.application.interfaces import IPriceRepo, IUserRepo
    from friendex.application.lock_manager import LockManager
    from friendex.application.voice_session_store import VoicePingSessionStore

# Module-level structlog logger — keyword arguments are picked up by the
# configured processor chain in ``adapters/config.py``.
_log = structlog.get_logger(__name__)


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
        # The injected :class:`VoicePingSessionStore` carries BOTH the open
        # ping sessions and the parallel host-role-member snapshot used by
        # the alt-account farming guard (issue #84 M / PR #93 C1). The
        # snapshot lives on the store — NOT on the service instance —
        # because the per-guild factory in
        # :class:`~friendex.adapters.container.Container` returns a fresh
        # ``VoicePingService`` per call, so a per-instance snapshot dict
        # would die between register (instance A from MessageListener) and
        # reward (instance B from VoiceListener). The store is the same
        # per-guild singleton both instances receive.
        self._ping_sessions = ping_sessions

    # -- ping lifecycle -----------------------------------------------------

    async def register_ping_message(
        self,
        message_id: int,
        host_id: str,
        channel_id: int,
        timestamp: datetime,
        host_role_member_ids: Collection[str] | None = None,
    ) -> None:
        """Open a ping session for ``message_id`` and credit the host.

        The host earns one ``role_ping_joins`` point (today + week) for issuing
        the ping, matching the original ``on_message`` voice-ping branch.

        ``host_role_member_ids`` (issue #84 M) is the snapshot of member ids
        who share the host's VC role at ping time. Responders whose ids are
        in that set are rejected by :meth:`reward_voice_ping_response` to
        close the alt-account farming exploit.

        Semantics:

        * ``None`` — no snapshot supplied; the alt-account guard is a no-op
          for this ping. Preserved for the historic call signature so
          legacy callers (tests + adapters not yet wired through #93 H1)
          keep working.
        * An empty :class:`Collection` (``frozenset()``, ``[]``, ``set()``)
          — explicit empty snapshot, meaning "no role members other than
          the host". Treated as a real snapshot: the guard runs but
          rejects nobody, which is the correct behaviour.
        * A non-empty :class:`Collection` — full guard active.

        The host's own id MAY be in the snapshot — the existing
        ``responder_id == host_id`` self-check covers it. The type is
        :class:`~collections.abc.Collection` (rather than
        :class:`~collections.abc.Iterable`) so callers cannot accidentally
        pass a single-shot generator that would silently be consumed once
        before the :class:`frozenset` conversion runs.

        Write ordering: the parallel host-role-member snapshot is written
        BEFORE the ping session itself is durably stored, so the only
        possible drift between the two dicts is ``snapshot present,
        session missing`` — the responder reward path handles that case
        gracefully (no session → no responders to reject). The inverse
        (``session present, snapshot missing``) would degrade the alt-
        account guard to a no-op for that ping, so write-then-set keeps
        the security primitive consistent. Both writes happen under the
        store's single lock acquisition (via
        :meth:`VoicePingSessionStore.set_with_snapshot`) so a concurrent
        reader cannot observe a half-written pair.
        """
        session = VoicePingSession(
            message_id=message_id,
            host_id=host_id,
            channel_id=channel_id,
            timestamp=timestamp,
            first_10_joiners=[],
            extra_joiners=[],
        )
        snapshot = (
            frozenset(host_role_member_ids)
            if host_role_member_ids is not None
            else None
        )
        await self._ping_sessions.set_with_snapshot(session, snapshot)
        await self._credit(host_id, role_ping_joins=1.0)

    async def collect_extra_boosts(self, now: datetime) -> list[VcExtraBoost]:
        """Return one :class:`VcExtraBoost` per extra joiner across open pings.

        The Phase 12 voice listener calls this after every voice join/switch
        and pushes the result into
        :meth:`~friendex.adapters.tasks.vc_boost_task.VcBoostTask.set_store_for_guild`
        so the periodic boost loop has a fresh per-guild roster (Phase 9
        digest §3 + Phase 12 STATE.md CF-4). Each boost entry is built from
        the original spec recipe (``docs/spec/original-skeleton.md:559-563``):

        * ``ping_time`` — the ping session's timestamp;
        * ``last_boost`` — ``now`` (no boost has been applied yet);
        * ``end_time`` — ``ping_time + voice_ping_window_seconds``.

        Read-only: the ping-session store is not mutated.
        """
        window = timedelta(seconds=self._settings.voice_ping_window_seconds)
        boosts: list[VcExtraBoost] = []
        for session in await self._ping_sessions.list_all():
            end_time = session.timestamp + window
            for user_id in session.extra_joiners:
                boosts.append(
                    VcExtraBoost(
                        user_id=user_id,
                        ping_time=session.timestamp,
                        last_boost=now,
                        end_time=end_time,
                    )
                )
        return boosts

    async def cleanup_expired_pings(self, now: datetime) -> int:
        """Evict ping sessions older than the response window; return the count.

        A session expires once ``now - timestamp`` exceeds
        ``voice_ping_window_seconds``. The store's :meth:`pop` drops the
        co-located host-role-member snapshot in the same lock acquisition
        as the session itself, so the two dicts cannot drift past
        eviction (issue #84 M / PR #93 C1).
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
            # Alt-account farming guard (issue #84 M / PR #93 C1): reject
            # responders who share the host's pinged VC role. The snapshot
            # is read from the SHARED per-guild
            # :class:`VoicePingSessionStore` so it survives the
            # MessageListener → factory → VoiceListener boundary (where
            # the service instance writing the snapshot is GC'd before the
            # instance reading it is constructed). The snapshot is absent
            # for legacy callers that did not supply role-member ids —
            # that case falls through to the historic behaviour.
            host_role_members = await self._ping_sessions.get_role_snapshot(
                session.message_id
            )
            if host_role_members is not None and responder_id in host_role_members:
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
        """Apply the join placement, price boost, and engagement credit once.

        **RMW atomicity (CF-2 / Phase 8a LOW).** The cap-check + write is a
        read-modify-write on the live ping session: two responders racing the
        same ping must not both observe a stale ``first_10_joiners`` snapshot
        and both pass the cap. The cap-check and the ``_ping_sessions.set``
        write that records the placement are serialised under
        ``lock_manager.locked(f"{guild_id}:ping:{message_id}")`` — a composite
        per-ping key that does not contend with the per-user keys used
        elsewhere. The downstream price-boost + engagement-credit calls run
        outside this lock: they take their own per-user composite locks, and a
        concurrent responder arriving after the placement write sees the fresh
        ``first_10_joiners`` list and correctly falls through to ``extra_joiners``.
        """
        cap = self._settings.voice_ping_first_n_joiners
        placed_in_first_n = False
        async with self._locks.locked(self._ping_lock_key(session.message_id)):
            # Re-read under the lock so the second responder sees the first
            # responder's just-committed placement instead of the stale snapshot.
            current = await self._ping_sessions.get(session.message_id)
            if current is None:
                # Session was swept (cleanup_expired_pings) between the outer
                # snapshot and the lock acquisition; drop silently.
                return
            if responder_id in current.first_10_joiners or (
                responder_id in current.extra_joiners
            ):
                # Lost the race: already credited by another concurrent caller.
                return
            if len(current.first_10_joiners) < cap:
                updated = replace(
                    current,
                    first_10_joiners=[*current.first_10_joiners, responder_id],
                )
                placed_in_first_n = True
            else:
                updated = replace(
                    current,
                    extra_joiners=[*current.extra_joiners, responder_id],
                )
            await self._ping_sessions.set(updated)

        if placed_in_first_n:
            await self._apply_join_boost(responder_id)

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

    def _lock_key(self, user_id: str) -> str:
        """Return the ``LockManager`` key for ``user_id`` in this guild.

        Thin shim around :func:`guild_lock_key` (#82 H16). ADR-0001 mandates
        per-guild market isolation: the same user in two guilds must NOT
        serialise against themselves on the single shared :class:`LockManager`.
        """
        return guild_lock_key(self._guild_id, user_id)

    def _ping_lock_key(self, message_id: int) -> str:
        """Return the ``LockManager`` key for a ping session's RMW critical section.

        The cap-check + placement write in :meth:`_reward_for_session` is a
        read-modify-write on the live ping session; a per-ping composite key
        (``"<guild_id>:ping:<message_id>"``) keeps concurrent responders to
        the *same* ping serialised without contending with the per-user keys
        used by everything else in the service.
        """
        return f"{self._guild_id}:ping:{message_id}"

    async def _apply_join_boost(self, responder_id: str) -> None:
        """Apply the one-time first-N-joiner price boost to ``responder_id``.

        Logs ``join_boost_no_stock`` (warning) when the responder's stock row
        is missing — issue #84 M (silent-failures branch). Silently dropping
        the boost hid a persistence drift; the structured log lets the
        operator catch it without changing user-visible behaviour.

        Issue #82 M6 — the upsert is paired with an ``append_history`` call
        on a real price change so the 24h-window aggregations
        (:class:`PortfolioService` high/low derived from history) include
        this boost. The ``if new_price != stock.current`` guard mirrors
        :meth:`PriceTickService._rmw_price` and avoids padding history
        with duplicate points when the boost rounds to a no-op (e.g. the
        floor stall stalled the rise).
        """
        min_price = Decimal(str(self._settings.min_price))
        boost = Decimal(str(self._settings.voice_ping_join_boost))
        async with self._locks.locked(self._lock_key(responder_id)):
            stock = await self._price_repo.get(self._guild_id, responder_id)
            if stock is None:
                _log.warning(
                    "join_boost_no_stock",
                    user_id=responder_id,
                    guild_id=self._guild_id,
                )
                return
            proposed = stock.current * boost
            new_price = apply_floor_stall(stock.current, proposed, min_price)
            if new_price == stock.current:
                return
            await self._price_repo.upsert(
                self._guild_id, replace(stock, current=new_price)
            )
            await self._price_repo.append_history(
                self._guild_id,
                responder_id,
                PricePoint(price=new_price, timestamp=datetime.now(tz=UTC)),
            )

    # -- shared account helpers --------------------------------------------

    async def _credit(self, user_id: str, **deltas: float) -> None:
        """Add ``deltas`` to ``user_id``'s today + week buckets under its lock."""
        async with self._locks.locked(self._lock_key(user_id)):
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
        """Return the stored account for ``user_id`` or a fresh default one.

        Delegates to the shared
        :func:`friendex.application.account_seed.seed_user_account` (#82 H16).
        """
        existing = await self._user_repo.get(self._guild_id, user_id)
        if existing is not None:
            return existing
        return seed_user_account(user_id, self._settings)
