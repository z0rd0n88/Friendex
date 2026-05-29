"""``MemberListener`` — applies disciplinary penalties on timeout / ban.

Two Discord events trigger a flat-percentage drop on the affected user's
own stock (``settings.discipline_penalty``; default 17%, floored at
``settings.min_price``):

* ``on_member_update`` — fires :meth:`DisciplineService.apply_discipline_penalty`
  with reason ``"timeout"`` **only** on the ``None → set`` transition of
  ``timed_out_until``. Extensions (``set → later-set``) and un-timeouts
  (``set → None``) do not re-trigger (Phase 12 signoff decision 4).
* ``on_member_ban`` — fires the same service with reason ``"ban"`` for every
  ban event.

**Audit trail + anti-spam (issue #84 M).** Every applied penalty emits one
structured ``discipline_penalty_applied`` log line (event name + stable
``guild_id`` / ``target_id`` / ``actor_id`` / ``reason`` fields) so a sudden
spike in moderation actions is observable from the log stream alone. A
24-hour in-memory cooldown keyed by ``(guild_id, target_id, actor_id)``
suppresses repeat penalties from the same moderator on the same member
within the window — a re-fire emits a
``discipline_penalty_skipped_cooldown`` line instead and the service is
NOT called. A *different* moderator within the same window is NOT
blocked (peer-escalation is a legitimate signal).

The actor (moderator) id is resolved via a one-entry
``guild.audit_logs(action=..., limit=1)`` lookup. When the bot lacks
``view_audit_log`` permission (or the audit feed has no matching recent
entry) the listener falls back to the sentinel ``"unknown"`` actor id —
the penalty still applies, but every ``"unknown"`` actor shares one
cooldown bucket per ``(guild, target)`` so a permission misconfiguration
cannot silently disable the spam guard.

The listener holds a per-guild service *factory* (matching the Phase 9
service_factory convention); it resolves the per-guild service via
``discipline_service_factory(str(guild.id))`` at event time.

Domain errors **propagate uncaught** (Phase 13 owns the central handler;
same policy as cogs).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.discipline_service import (
        DisciplineReason,
        DisciplineService,
    )


logger = logging.getLogger(__name__)

# Default cooldown window between repeated penalties from the SAME moderator
# on the SAME target. 24 hours mirrors the spec's "~24h rolling window" guidance
# for issue #84 M; injected at construction time so tests can shrink it.
_DEFAULT_COOLDOWN_SECONDS = 24 * 60 * 60

# Sentinel actor id used when the audit-log lookup yields no entry (no
# permission, no recent entry within the lookup window, ...). Buckets every
# unknown-actor penalty into a shared cooldown slot per (guild, target) so a
# permission misconfiguration cannot silently disable the cooldown gate.
_UNKNOWN_ACTOR_ID = "unknown"


class MemberListener(commands.Cog):
    """Routes :py:obj:`on_member_update` + :py:obj:`on_member_ban` to discipline."""

    def __init__(
        self,
        *,
        discipline_service_factory: Callable[[str], DisciplineService],
        cooldown_seconds: int = _DEFAULT_COOLDOWN_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._discipline_factory = discipline_service_factory
        self._cooldown_seconds = cooldown_seconds
        # Injecting the clock lets tests advance time without freezegun /
        # without monkey-patching the module-level ``datetime.now``.
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(tz=UTC))
        # In-memory cooldown: maps (guild_id, target_id, actor_id) → expiry
        # instant. Guarded by ``_cooldown_lock`` so concurrent listener
        # callbacks for the same triple cannot both pass the active-check.
        # Volatile by design — discipline cooldowns are short-lived and a
        # restart clearing them is acceptable behaviour.
        self._cooldown: dict[tuple[str, str, str], datetime] = {}
        self._cooldown_lock: asyncio.Lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_member_update(
        self,
        before: discord.Member,
        after: discord.Member,
    ) -> None:
        """Apply a ``"timeout"`` penalty on a ``None → set`` transition.

        The guard intentionally excludes extensions and un-timeouts so a
        moderator re-timing-out an already-muted member does not stack
        penalties (Phase 12 signoff decision 4). Mirrors the original
        bot's discipline branch.
        """
        if before.timed_out_until is not None:
            return
        if after.timed_out_until is None:
            return

        await self._apply_penalty(
            guild=after.guild,
            target_id=str(after.id),
            reason="timeout",
            audit_action=discord.AuditLogAction.member_update,
        )

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
    ) -> None:
        """Apply a ``"ban"`` penalty for every ban event in ``guild``."""
        await self._apply_penalty(
            guild=guild,
            target_id=str(user.id),
            reason="ban",
            audit_action=discord.AuditLogAction.ban,
        )

    async def _apply_penalty(
        self,
        *,
        guild: discord.Guild,
        target_id: str,
        reason: DisciplineReason,
        audit_action: discord.AuditLogAction,
    ) -> None:
        """Resolve the actor, check the cooldown, audit-log, and dispatch.

        Stages:
        1. Resolve the moderator id from the audit log (best-effort; falls
           back to the ``"unknown"`` sentinel on permission / lookup gaps).
        2. Take the per-target (guild, target, actor) cooldown lock and
           check active-vs-expired; skip with an audit log line if active.
        3. Set the cooldown TTL row inside the same critical section.
        4. Emit the ``discipline_penalty_applied`` audit line BEFORE the
           service call so the action is recorded even if the service
           raises.
        5. Delegate to :meth:`DisciplineService.apply_discipline_penalty`.
        """
        guild_id = str(guild.id)
        actor_id = await self._resolve_actor_id(guild, audit_action)
        key = (guild_id, target_id, actor_id)
        now = self._clock()

        async with self._cooldown_lock:
            expires_at = self._cooldown.get(key)
            if expires_at is not None and expires_at > now:
                logger.info(
                    "discipline_penalty_skipped_cooldown",
                    extra={
                        "guild_id": guild_id,
                        "target_id": target_id,
                        "actor_id": actor_id,
                        "reason": reason,
                        "expires_at": expires_at.isoformat(),
                    },
                )
                return
            self._cooldown[key] = now + timedelta(seconds=self._cooldown_seconds)

        logger.info(
            "discipline_penalty_applied",
            extra={
                "guild_id": guild_id,
                "target_id": target_id,
                "actor_id": actor_id,
                "reason": reason,
            },
        )
        discipline_service = self._discipline_factory(guild_id)
        await discipline_service.apply_discipline_penalty(target_id, reason)

    async def _resolve_actor_id(
        self,
        guild: discord.Guild,
        audit_action: discord.AuditLogAction,
    ) -> str:
        """Return the moderator id from the latest audit entry, or ``"unknown"``.

        Reads one audit-log entry of the matching action. Any failure
        (``Forbidden``, no entry, malformed payload) falls back to the
        ``_UNKNOWN_ACTOR_ID`` sentinel so the penalty still applies and
        the cooldown still gates spam from unknown-actor bursts.
        """
        try:
            async for entry in guild.audit_logs(action=audit_action, limit=1):
                user = getattr(entry, "user", None)
                user_id = getattr(user, "id", None)
                if user_id is not None:
                    return str(user_id)
                # First entry has no user — stop scanning.
                break
        except discord.Forbidden:
            # Bot lacks view_audit_log; fall back to the sentinel.
            return _UNKNOWN_ACTOR_ID
        except Exception:  # pragma: no cover - defensive
            # Any other audit-log retrieval failure (HTTP error, malformed
            # entry, ...) MUST NOT prevent the penalty from being applied.
            # The fallback ensures the cooldown gate still buckets spam.
            return _UNKNOWN_ACTOR_ID
        return _UNKNOWN_ACTOR_ID
