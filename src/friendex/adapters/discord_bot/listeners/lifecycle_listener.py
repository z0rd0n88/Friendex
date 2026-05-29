"""``LifecycleListener`` — bridges discord.py guild-lifecycle events to a
container-supplied cleanup hook.

Today this listener exists to satisfy Wave 1 (#82 M2): when the bot is
removed from a guild, the composition root's volatile per-guild stores
(``_voice_sessions``, ``_ping_sessions``) need to be evicted so a
long-running bot does not accumulate dead entries across thousands of
short-lived guild adds/removes.

**Why a separate cog?** The Phase 12 listener taxonomy says each cog owns
one event domain (``MessageListener`` → ``on_message``,
``VoiceListener`` → ``on_voice_state_update``, etc.). Bot-level
lifecycle events (``on_guild_remove``, eventually
``on_guild_join`` / ``on_ready``) deserve their own cog rather than being
smuggled onto an existing event domain.

**Why a callback, not a ``Container`` import?** The listener layer must
not depend inward on the composition root (the container imports
listeners, not the reverse — circular import otherwise). The container
passes its own ``on_guild_remove`` method as a bare
``Callable[[discord.Guild], Awaitable[None]]`` and this cog forwards the
event with no further coupling.

**Domain errors propagate uncaught**: Phase 13 owns the central error
handler; listener-level callbacks share the same policy as cog
callbacks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import discord


class LifecycleListener(commands.Cog):
    """Forwards ``on_guild_remove`` to a composition-root cleanup hook."""

    def __init__(
        self,
        *,
        on_guild_remove: Callable[[discord.Guild], Awaitable[None]],
    ) -> None:
        self._on_guild_remove = on_guild_remove

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Dispatch ``on_guild_remove`` to the injected cleanup hook."""
        await self._on_guild_remove(guild)
