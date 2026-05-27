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

The listener holds a per-guild service *factory* (matching the Phase 9
service_factory convention); it resolves the per-guild service via
``discipline_service_factory(str(guild.id))`` at event time.

Domain errors **propagate uncaught** (Phase 13 owns the central handler;
same policy as cogs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from discord.ext import commands

if TYPE_CHECKING:
    from collections.abc import Callable

    import discord

    from friendex.application.discipline_service import DisciplineService


class MemberListener(commands.Cog):
    """Routes :py:obj:`on_member_update` + :py:obj:`on_member_ban` to discipline."""

    def __init__(
        self,
        *,
        discipline_service_factory: Callable[[str], DisciplineService],
    ) -> None:
        self._discipline_factory = discipline_service_factory

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

        discipline_service = self._discipline_factory(str(after.guild.id))
        await discipline_service.apply_discipline_penalty(str(after.id), "timeout")

    @commands.Cog.listener()
    async def on_member_ban(
        self,
        guild: discord.Guild,
        user: discord.User | discord.Member,
    ) -> None:
        """Apply a ``"ban"`` penalty for every ban event in ``guild``."""
        discipline_service = self._discipline_factory(str(guild.id))
        await discipline_service.apply_discipline_penalty(str(user.id), "ban")
