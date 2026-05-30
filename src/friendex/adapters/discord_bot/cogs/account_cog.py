"""``AccountCog`` — ``/balance``, ``/optin``, ``/optout`` slash commands.

The account cog is the read-mostly half of the Phase 11 cog surface: it lets
a member see their cash + portfolio summary (``/balance``) and toggle their
own consent to be a tradeable stock (``/optin`` / ``/optout``). Every reply
is ephemeral so toggling consent does not leak into the channel.

The cog holds per-guild service *factories*, not service instances —
matching the Phase 9 service_factory convention (``baton-runner/
br-2026-05-25-phase-9/digest-phase-9.md``). On each invocation the cog
calls the factory with ``guild_id_of(interaction)`` to obtain the
guild-scoped service, then delegates the use case.

**Wave 1: defer + followup** (issue #82 H13). Discord rejects any
slash-command callback that does not acknowledge the interaction inside the
3 s deadline. Every callback ``await interaction.response.defer(...)`` as
its FIRST line and then replies via ``interaction.followup.send(...)``. The
ephemeral/public mapping mirrors the project's CLAUDE.md table (here all
three are ephemeral / personal).

**Wave 1: ``@app_commands.guild_only()``** (issue #82 H14). Every command
refuses DM dispatch at the Discord level — the gateway never delivers the
command in a DM context, so the cog body cannot run there. ``guild_id_of``
also raises ``NoPrivateMessage`` as a belt-and-braces guard if a
misconfigured deployment ever lets one through.

Domain errors **propagate uncaught**; Phase 13 will install a tree-wide
``app_commands`` error handler. Cogs neither catch
:class:`~friendex.domain.errors.DomainError` nor render
:func:`build_error_embed` here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord import app_commands
from discord.ext import commands

from friendex.adapters.discord_bot.cogs._interaction import guild_id_of
from friendex.adapters.discord_bot.embeds import (
    COLOR_NEUTRAL,
    COLOR_SUCCESS,
    build_balance_embed,
    build_intro_embed,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.application.activity_service import ActivityService
    from friendex.application.portfolio_service import PortfolioService

# PR #94 review (M1): pre-fix this module held ``logger = logging.getLogger(
# __name__)`` and passed structured fields via the stdlib ``extra={...}``
# kwarg. ``configure_logging`` (``adapters/config.py``) installs the bare
# ``%(message)s`` format on the stdlib root, so ``extra`` was silently
# dropped from every rendered log line. Structlog routes the structured
# kwargs through the JSON renderer as top-level keys.
_log = structlog.get_logger(__name__)


class AccountCog(commands.Cog):
    """Account-management slash commands.

    Ctor takes per-guild service factories so a single cog instance serves
    every guild the bot is in. Phase 13/14 wires the factories — they
    construct each per-guild service on demand against the shared
    persistence + lock-manager singletons.
    """

    def __init__(
        self,
        *,
        portfolio_service_factory: Callable[[str], PortfolioService],
        activity_service_factory: Callable[[str], ActivityService],
    ) -> None:
        self._portfolio_factory = portfolio_service_factory
        self._activity_factory = activity_service_factory

    # -- /balance -----------------------------------------------------------

    @app_commands.command(
        name="balance",
        description="View your cash, net worth, and hedge fund summary.",
    )
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction) -> None:
        """Reply ephemerally with the invoker's portfolio snapshot."""
        await interaction.response.defer(ephemeral=True)
        portfolio_service = self._portfolio_factory(guild_id_of(interaction))
        snapshot = await portfolio_service.portfolio_snapshot(
            user_id=str(interaction.user.id)
        )
        if snapshot is None:
            embed = discord.Embed(
                title="Account Balance",
                color=COLOR_NEUTRAL,
                description=(
                    "No account found yet — run `/daily` to open one and "
                    "claim your starter cash."
                ),
            )
        else:
            embed = build_balance_embed(snapshot)
        await interaction.followup.send(embed=embed, ephemeral=True)

    # -- /optin -------------------------------------------------------------

    @app_commands.command(
        name="optin",
        description="Opt in to being a tradeable stock on this server.",
    )
    @app_commands.guild_only()
    async def optin(self, interaction: discord.Interaction) -> None:
        """Mark the invoker's account as tradeable; DM the intro on first opt-in.

        Q10 auto-DM. ``opt_in_and_consume_intro`` returns ``True`` exactly
        once per account (the first /optin) and atomically flips both
        ``opt_in=True`` and ``intro_shown=True`` in the same write. On that
        first-time signal the cog DMs the intro embed; if the user has DMs
        disabled (``discord.Forbidden``) the intro embed is attached to the
        ephemeral confirmation reply so the user still sees it inline.

        The ephemeral acknowledgement is sent on every path — Discord's
        3 s interaction-ack deadline does not care whether the DM succeeded.
        Every send uses ``AllowedMentions.none()`` (Phase 10 invariant).
        ``discord.Forbidden`` is the only ``try/except`` permitted in the
        cog (Phase 13: DomainError propagates uncaught).
        """
        await interaction.response.defer(ephemeral=True)
        activity_service = self._activity_factory(guild_id_of(interaction))
        should_show_intro = await activity_service.opt_in_and_consume_intro(
            str(interaction.user.id)
        )
        confirmation_embed = discord.Embed(
            title="Opted in",
            color=COLOR_SUCCESS,
            description="You are now a tradeable stock on this server.",
        )

        if not should_show_intro:
            await interaction.followup.send(
                embed=confirmation_embed,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        intro_embed = build_intro_embed()
        try:
            await interaction.user.send(
                embed=intro_embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            # DMs closed — fall back to attaching the intro to the
            # ephemeral confirmation so the user still sees it. Record the
            # block at INFO so operators can spot trends (e.g. server-wide
            # DM restrictions); the embed payload is deliberately omitted.
            # Structlog accepts the structured fields as keyword arguments —
            # they land as top-level keys in the JSON sink. (PR #94 review M1.)
            _log.info(
                "account.optin_intro_dm_forbidden",
                user_id=str(interaction.user.id),
                guild_id=guild_id_of(interaction),
            )
            await interaction.followup.send(
                embeds=[intro_embed, confirmation_embed],
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        # DM succeeded — ephemeral ack carries the confirmation only.
        await interaction.followup.send(
            embed=confirmation_embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /optout ------------------------------------------------------------

    @app_commands.command(
        name="optout",
        description="Opt out of being a tradeable stock on this server.",
    )
    @app_commands.guild_only()
    async def optout(self, interaction: discord.Interaction) -> None:
        """Remove the invoker's account from the market and confirm ephemerally."""
        await interaction.response.defer(ephemeral=True)
        activity_service = self._activity_factory(guild_id_of(interaction))
        await activity_service.set_opt_in(str(interaction.user.id), False)
        embed = discord.Embed(
            title="Opted out",
            color=COLOR_SUCCESS,
            description="Your stock has been removed from the market.",
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
