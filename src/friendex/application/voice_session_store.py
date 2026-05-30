"""In-memory, volatile stores for live voice and voice-ping state.

The original monolith kept voice tracking in plain process-local dicts
(``voice_sessions`` keyed by user id, ``voice_ping_sessions`` keyed by ping
message id). That state is **intentionally volatile** — it represents who is
*currently* in a channel and which pings are *currently* open, both of which are
meaningless after a restart — so it is deliberately not persisted through the
repository layer.

These two classes wrap that design in async-safe containers: each is a thin
wrapper around a ``dict`` guarded by an :class:`asyncio.Lock`, so concurrent
listener callbacks (``on_voice_state_update``, ``on_message``) never corrupt the
shared map. They hold no business logic — that lives in the services that own
them.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from friendex.domain.models import VoicePingSession, VoiceSession


class VoiceSessionStore:
    """Volatile map of ``user_id -> VoiceSession`` for users currently in voice.

    Guarded by an :class:`asyncio.Lock` so the join/leave paths can read,
    mutate, and write back a session atomically.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, user_id: str) -> VoiceSession | None:
        """Return the live session for ``user_id`` or ``None``."""
        async with self._lock:
            return self._sessions.get(user_id)

    async def set(self, session: VoiceSession) -> None:
        """Store ``session`` under its ``user_id`` (replacing any prior one)."""
        async with self._lock:
            self._sessions[session.user_id] = session

    async def pop(self, user_id: str) -> VoiceSession | None:
        """Remove and return the session for ``user_id``, or ``None``."""
        async with self._lock:
            return self._sessions.pop(user_id, None)

    async def link_ping(self, user_id: str, message_id: int) -> None:
        """Record that ``user_id``'s current session came from ``message_id``.

        No-op if the user has no live session (matches the original
        ``setdefault`` guard that only linked a ping for users already tracked).

        **Immutable rebuild.** The stored :class:`VoiceSession` is replaced via
        :func:`dataclasses.replace` with a *fresh* ``set`` rather than mutating
        ``from_ping_message_ids`` in place. Volatile in-memory state still
        obeys the project-wide immutability rule (Phase 3.1 invariant) so a
        caller holding a reference to the original session sees an unchanged
        snapshot — matches the dataclass-replace pattern used everywhere else
        in the application layer.
        """
        async with self._lock:
            session = self._sessions.get(user_id)
            if session is not None:
                self._sessions[user_id] = replace(
                    session,
                    from_ping_message_ids=session.from_ping_message_ids | {message_id},
                )


class VoicePingSessionStore:
    """Volatile map of ``message_id -> VoicePingSession`` for open VC pings.

    A ping session is created when a host pings a VC role and lives until it
    expires (older than the response window) or is swept. Guarded by an
    :class:`asyncio.Lock` so the register / reward / cleanup paths serialise.

    **Co-located host-role-member snapshot (PR #93 C1 / issue #84 M).** A
    parallel dict keyed by ``message_id`` carries the union of role-member
    ids who shared the host's pinged VC role at ping time, captured by
    :meth:`~friendex.adapters.discord_bot.listeners.message_listener.MessageListener.on_message`
    from each matched :class:`discord.Role`'s live ``.members`` view. The
    snapshot lives ON THE STORE — NOT on the :class:`VoicePingService`
    instance — because the per-guild service factory in
    :class:`~friendex.adapters.container.Container` returns a **fresh**
    service per call. ``MessageListener`` calls the factory once (instance
    A) to register; ``VoiceListener`` calls the factory a second time
    (instance B) to reward. Instance B's per-instance dict is empty, so a
    per-service snapshot would silently degrade the alt-account guard to
    a no-op exactly where the exploit lives (PR #93 iter-2 review C1).
    The store is the same per-guild singleton both instances receive, so
    co-locating the snapshot with the session itself fixes the lifecycle
    and keeps both dicts in lockstep under the same lock (every mutation
    touches both atomically).
    """

    def __init__(self) -> None:
        self._sessions: dict[int, VoicePingSession] = {}
        # Co-located host-role-member snapshot (PR #93 C1). Keyed by the
        # same ``message_id`` as ``_sessions``; populated only when the
        # caller supplies a snapshot via :meth:`set_with_snapshot`. Both
        # dicts are mutated under ``self._lock`` so a reader never sees a
        # half-written pair.
        self._host_role_member_ids: dict[int, frozenset[str]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, message_id: int) -> VoicePingSession | None:
        """Return the ping session for ``message_id`` or ``None``."""
        async with self._lock:
            return self._sessions.get(message_id)

    async def set(self, session: VoicePingSession) -> None:
        """Store ``session`` under its ``message_id`` (replacing any prior)."""
        async with self._lock:
            self._sessions[session.message_id] = session

    async def set_with_snapshot(
        self,
        session: VoicePingSession,
        host_role_member_ids: frozenset[str] | None,
    ) -> None:
        """Store ``session`` and (optionally) its role-member snapshot atomically.

        Both writes happen under the same lock acquisition so a concurrent
        reader of either dict cannot observe a half-written pair. The
        snapshot dict is written **before** the session dict so the only
        possible drift is ``snapshot present, session missing`` — the
        reward path is already defensive about a missing session — rather
        than the inverse, which would degrade the alt-account guard to a
        no-op for that ping.

        ``None`` is the explicit historic-call signal: no snapshot is
        recorded, the alt-account guard is a no-op for this ping (legacy
        callers + simpler tests that omit the kwarg). An empty
        :class:`frozenset` is the explicit "no role members" snapshot:
        recorded as-is, the guard runs but rejects nobody.
        """
        async with self._lock:
            if host_role_member_ids is not None:
                self._host_role_member_ids[session.message_id] = host_role_member_ids
            self._sessions[session.message_id] = session

    async def get_role_snapshot(self, message_id: int) -> frozenset[str] | None:
        """Return the host-role-member snapshot for ``message_id`` or ``None``.

        ``None`` means either the ping was registered without a snapshot
        (historic call signature) or the ping is already swept — callers
        distinguish those by checking the session presence separately.
        Either way the alt-account guard short-circuits on ``None``.
        """
        async with self._lock:
            return self._host_role_member_ids.get(message_id)

    async def pop(self, message_id: int) -> VoicePingSession | None:
        """Remove and return the ping session for ``message_id``, or ``None``.

        The co-located role-member snapshot is dropped in the same lock
        acquisition so the two dicts cannot drift past eviction.
        """
        async with self._lock:
            self._host_role_member_ids.pop(message_id, None)
            return self._sessions.pop(message_id, None)

    async def list_all(self) -> list[VoicePingSession]:
        """Return a snapshot list of every open ping session."""
        async with self._lock:
            return list(self._sessions.values())
