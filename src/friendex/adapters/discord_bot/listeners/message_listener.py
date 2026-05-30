"""``MessageListener`` — credits message activity and opens VC ping sessions.

Two side-effects on every guild non-bot message:

1. :meth:`ActivityService.record_message` — credits text/media engagement
   (with reply detection via ``message.reference``); a media post in a
   configured photo-bonus channel also grants ``role_ping_join_minutes``
   inside the service (Phase 8a contract). ``channel_id`` is forwarded
   so the service can decide the bonus branch.
2. :meth:`VoicePingService.register_ping_message` — opens a ping session
   **only** when both conditions hold:

   * the author is currently in a voice channel
     (``message.author.voice and message.author.voice.channel``); and
   * the message mentions at least one role whose id is in
     ``settings.vc_ping_role_ids`` — the
     :func:`is_voice_ping_message` rule replicated from
     ``docs/spec/original-skeleton.md:494-503``.

   When ``vc_ping_role_ids`` is empty (default), no message ever triggers
   a ping — exact replica of the original bot's behaviour.

   On every qualifying ping the listener also snapshots the union of
   ``role.members`` across every matched ping role and forwards it as
   ``host_role_member_ids`` so the service-side alt-account farming
   guard (issue #84 M) has data to act on (PR #93 H1 production wiring).

**Bot-skip applies to ALL bots** (signoff decision 3): own bot, other bots,
and webhook senders — anything where ``author.bot is True`` — are silently
dropped.

**DM-narrow** at the boundary: messages without a ``guild`` (DMs) are
dropped before any service is touched (ADR-0001 — no economy outside
guilds).

**Domain errors propagate uncaught**: Phase 13 owns the central handler;
listeners share the same policy as cogs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from discord.ext import commands

if TYPE_CHECKING:
    from collections.abc import Callable

    import discord

    from friendex.adapters.config import Settings
    from friendex.application.activity_service import ActivityService
    from friendex.application.voice_ping_service import VoicePingService


class _PingMatch(NamedTuple):
    """Resolved ping-match result returned by :meth:`_resolve_ping_match`.

    ``voice_channel_id`` is the host's voice channel (the listener already
    requires the host to be in voice for a ping to qualify).
    ``matched_roles`` is the subset of ``message.role_mentions`` whose
    ids appear in ``settings.vc_ping_role_ids`` — used by ``on_message``
    to snapshot ``role.members`` for the alt-account farming guard
    (issue #84 M / PR #93 H1).
    """

    voice_channel_id: int
    matched_roles: tuple[discord.Role, ...]


class MessageListener(commands.Cog):
    """Routes :py:obj:`on_message` to activity + voice-ping services."""

    def __init__(
        self,
        *,
        activity_service_factory: Callable[[str], ActivityService],
        voice_ping_service_factory: Callable[[str], VoicePingService],
        settings: Settings,
    ) -> None:
        self._activity_factory = activity_service_factory
        self._voice_ping_factory = voice_ping_service_factory
        self._settings = settings

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Credit message engagement and, when applicable, open a ping session.

        Skips silently when ``author.bot is True`` (all bots) or
        ``message.guild is None`` (DM). Otherwise:

        * always calls :meth:`ActivityService.record_message` with the
          detected text/media/reply flags + originating channel id;
        * additionally calls :meth:`VoicePingService.register_ping_message`
          iff the author is in a voice channel AND the message role-mentions
          a configured VC ping role.
        """
        if message.author.bot:
            return
        guild = message.guild
        if guild is None:
            return

        guild_id = str(guild.id)
        author_id = str(message.author.id)
        has_attachment = bool(message.attachments)
        is_reply = message.reference is not None
        channel_id = int(message.channel.id)

        activity_service = self._activity_factory(guild_id)
        await activity_service.record_message(
            author_id=author_id,
            has_attachment=has_attachment,
            is_reply=is_reply,
            channel_id=channel_id,
        )

        ping_match = self._resolve_ping_match(message)
        if ping_match is None:
            return

        # Issue #84 M (PR #93 H1) — snapshot the union of role-member ids
        # across every matched ping role at ping time. The service-side
        # alt-account farming guard rejects responders whose id appears
        # in this set. ``role.members`` is the live in-process view of
        # the cached role membership; freezing the snapshot here means
        # subsequent role joins between ping and reward do not let a
        # post-hoc alt slip through. The host's own id may appear in
        # the snapshot — the service's ``responder_id == host_id``
        # self-check covers it, so deduping it out is unnecessary.
        host_role_member_ids = frozenset(
            str(member.id)
            for role in ping_match.matched_roles
            for member in role.members
        )

        voice_ping_service = self._voice_ping_factory(guild_id)
        await voice_ping_service.register_ping_message(
            message_id=int(message.id),
            host_id=author_id,
            channel_id=ping_match.voice_channel_id,
            timestamp=datetime.now(tz=UTC),
            host_role_member_ids=host_role_member_ids,
        )

    def _resolve_ping_match(self, message: discord.Message) -> _PingMatch | None:
        """Return the resolved ping match (channel + matched roles) or ``None``.

        Replicates ``is_voice_ping_message`` from
        ``docs/spec/original-skeleton.md:494-503``: the author must be in a
        voice channel AND the message must mention at least one role whose
        id is in ``settings.vc_ping_role_ids``. With an empty config (the
        default), this returns ``None`` unconditionally.

        Returns the matched-role tuple alongside the channel id so the
        caller can snapshot ``role.members`` for the alt-account farming
        guard (issue #84 M / PR #93 H1) without re-running the role-id
        filter.
        """
        voice = getattr(message.author, "voice", None)
        if voice is None or voice.channel is None:
            return None
        ping_role_ids = set(self._settings.vc_ping_role_ids)
        if not ping_role_ids:
            return None
        matched_roles = tuple(
            role for role in message.role_mentions if role.id in ping_role_ids
        )
        if not matched_roles:
            return None
        return _PingMatch(
            voice_channel_id=int(voice.channel.id),
            matched_roles=matched_roles,
        )
