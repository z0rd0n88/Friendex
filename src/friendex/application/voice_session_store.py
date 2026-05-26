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
        """
        async with self._lock:
            session = self._sessions.get(user_id)
            if session is not None:
                session.from_ping_message_ids.add(message_id)


class VoicePingSessionStore:
    """Volatile map of ``message_id -> VoicePingSession`` for open VC pings.

    A ping session is created when a host pings a VC role and lives until it
    expires (older than the response window) or is swept. Guarded by an
    :class:`asyncio.Lock` so the register / reward / cleanup paths serialise.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, VoicePingSession] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, message_id: int) -> VoicePingSession | None:
        """Return the ping session for ``message_id`` or ``None``."""
        async with self._lock:
            return self._sessions.get(message_id)

    async def set(self, session: VoicePingSession) -> None:
        """Store ``session`` under its ``message_id`` (replacing any prior)."""
        async with self._lock:
            self._sessions[session.message_id] = session

    async def pop(self, message_id: int) -> VoicePingSession | None:
        """Remove and return the ping session for ``message_id``, or ``None``."""
        async with self._lock:
            return self._sessions.pop(message_id, None)

    async def list_all(self) -> list[VoicePingSession]:
        """Return a snapshot list of every open ping session."""
        async with self._lock:
            return list(self._sessions.values())
