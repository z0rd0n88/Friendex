"""``ReactionListener`` — credits reaction activity to the reactor.

When a member adds a reaction to *another* member's message, the reactor
earns a unit of engagement credit (recorded into their today + week
:class:`~friendex.domain.models.ActivityBucket` s via
:meth:`~friendex.application.activity_service.ActivityService.record_reaction`).
Self-reactions and bot reactions are silently dropped — the former is
gaming-prevention from the original bot; the latter applies to **all** bots,
including the project bot and any other bots in the server (Phase 12
signoff decision 3).

The listener holds a per-guild service *factory* rather than a service
instance — matching the Phase 9 service_factory convention. On each event
it resolves the per-guild service via
``activity_service_factory(str(reaction.message.guild.id))``.

Domain errors **propagate uncaught**: Phase 13 owns the central handler
(listeners and cogs share the same policy). The listener never wraps the
service call in ``try/except``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from collections.abc import Callable

    import discord

    from friendex.application.activity_service import ActivityService


class ReactionListener(commands.Cog):
    """Routes :py:obj:`on_reaction_add` to :class:`ActivityService`.

    Ctor takes a per-guild :class:`ActivityService` factory so a single
    listener instance serves every guild the bot is in. Phase 13/14 wires
    the factory — it constructs the per-guild service against the shared
    persistence + lock-manager singletons.
    """

    def __init__(
        self,
        *,
        activity_service_factory: Callable[[str], ActivityService],
    ) -> None:
        self._activity_factory = activity_service_factory

    @commands.Cog.listener()
    async def on_reaction_add(
        self,
        reaction: discord.Reaction,
        user: discord.User | discord.Member,
    ) -> None:
        """Credit ``user`` for adding ``reaction`` to another member's message.

        Skips silently when:

        * ``user.bot`` is :data:`True` (all bots — see signoff decision 3); or
        * the reactor is the message author (self-reaction; original bot rule).

        Otherwise delegates to
        :meth:`ActivityService.record_reaction` for the reactor.
        """
        if user.bot:
            return
        if user.id == reaction.message.author.id:
            return

        # Narrow ``Guild | None``: DM reactions are dropped here (the bot
        # has no economy outside guilds — Phase 11 ``guild_id_of`` rule for
        # cogs, restated for listeners which carry events instead of
        # interactions).
        guild = reaction.message.guild
        if guild is None:
            return

        activity_service = self._activity_factory(str(guild.id))
        await activity_service.record_reaction(user_id=str(user.id))
