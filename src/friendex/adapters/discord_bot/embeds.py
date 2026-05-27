"""Pure embed builders for Friendex's Discord slash commands.

Every function here is a **pure function** that takes an application-layer
result/snapshot dataclass and returns a :class:`discord.Embed`. No I/O, no
network, no bot state — embeds are constructed off frozen DTOs the service
layer already populated, so the builders are trivially testable via
:meth:`discord.Embed.to_dict`.

This module is **the first place** in the Friendex codebase that imports
``discord``. The surface is deliberately narrow: only :class:`discord.Embed`
and :class:`discord.Color` are used. No :class:`discord.Interaction`,
:class:`discord.Client`, or any other runtime coupling — those belong in
Phase 11 cogs and Phase 12 listeners.

**Color palette** (module-level constants, re-exported for cogs and the
liquidation notifier in Phase 11/14):

* ``COLOR_SUCCESS`` — green; buy, sell, short, cover, daily.
* ``COLOR_ERROR`` — red; all :class:`DomainError` renderings.
* ``COLOR_WARNING`` — orange; liquidation notifications.
* ``COLOR_INFO`` — blurple; static informational embeds (intro, help).
* ``COLOR_NEUTRAL`` — blue; read-only command embeds (balance, portfolio,
  trending, mystats, price, fund_info).

**Money / datetime invariants** (Phase 3.1 preserved):

* Currency :class:`~decimal.Decimal` fields format via the
  ``f"${value:,.2f}"`` template (two decimals, thousands separator).
* Datetimes are rendered as Discord relative-time tags (``<t:UNIX:R>``)
  so the client renders a human-friendly "in 2 hours" / "3 minutes ago"
  string instead of a wall-clock ISO string for a different timezone.

See ``docs/04-migration-plan.md`` §Phase 10 for the spec and the design
notes in the matching baton-pass for the per-builder signature choices.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from decimal import Decimal

    from friendex.application.daily_result import DailyClaimResult
    from friendex.application.liquidation_events import LiquidationEvent
    from friendex.application.snapshot_models import (
        PortfolioSnapshot,
        PriceStats,
        TrendingEntry,
        UserStats,
    )
    from friendex.application.trade_results import (
        BuyResult,
        CoverResult,
        SellResult,
        ShortResult,
    )
    from friendex.domain.errors import DomainError
    from friendex.domain.models import HedgeFund

# ---------------------------------------------------------------------------
# Semantic color palette (re-exported)

COLOR_SUCCESS: discord.Color = discord.Color.green()
COLOR_ERROR: discord.Color = discord.Color.red()
COLOR_WARNING: discord.Color = discord.Color.orange()
COLOR_INFO: discord.Color = discord.Color.blurple()
COLOR_NEUTRAL: discord.Color = discord.Color.blue()


# ---------------------------------------------------------------------------
# Small render helpers (private; pure)


def _money(value: Decimal) -> str:
    """Render a :class:`Decimal` as ``$1,234.56`` with banker-friendly formatting.

    The ``,`` thousands separator and ``.2f`` precision match the Phase 3.1
    currency convention. ``Decimal`` supports the ``:,.2f`` spec natively.
    """
    return f"${value:,.2f}"


def _signed_money(value: Decimal) -> str:
    """Render a signed Decimal — leading ``+`` on profit, ``-`` on loss."""
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"+${value:,.2f}"


def _percent(rate: float) -> str:
    """Render a unit-interval rate as a percentage (``0.15`` → ``15.00%``)."""
    return f"{rate * 100:.2f}%"


def _user_mention(user_id: str) -> str:
    """Discord <@user> mention syntax — falls back to plain id if non-numeric.

    Phase 11 cogs pass real Discord snowflake strings; the test suite passes
    short opaque identifiers (``"target-1"``) so the embed still has to render
    something. The mention syntax requires a numeric snowflake, so we only
    emit it when ``user_id`` parses as a positive integer.
    """
    if user_id.isdigit():
        return f"<@{user_id}>"
    return user_id


def _relative_timestamp(when: datetime) -> str:
    """Discord ``<t:UNIX:R>`` relative-time tag (``2 hours ago`` / ``in 5 m``).

    UTC-aware datetimes are required (Phase 3.1 invariant); we trust the caller
    rather than re-validating at every render site.
    """
    return f"<t:{int(when.timestamp())}:R>"


# ---------------------------------------------------------------------------
# /balance


def build_balance_embed(snapshot: PortfolioSnapshot) -> discord.Embed:
    """Render the ``/balance`` summary embed from a :class:`PortfolioSnapshot`.

    The snapshot is the same DTO ``/portfolio`` consumes — choosing it over
    ``UserAccount + prices + HedgeFund`` keeps the embed builder ignorant of
    persistence and pricing concerns. ``net_worth`` and ``fund_balance`` are
    pre-computed by :class:`PortfolioService`.
    """
    embed = discord.Embed(
        title="Account Balance",
        color=COLOR_NEUTRAL,
        description=(
            f"Cash: {_money(snapshot.cash_balance)}\n"
            f"Net worth: {_money(snapshot.net_worth)}\n"
            f"Hedge fund: {_money(snapshot.fund_balance)}"
        ),
    )
    embed.add_field(
        name="Longs",
        value=str(len(snapshot.long_positions)),
        inline=True,
    )
    embed.add_field(
        name="Shorts",
        value=str(len(snapshot.short_positions)),
        inline=True,
    )
    embed.add_field(
        name="Month-start net worth",
        value=_money(snapshot.month_start_net_worth),
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# /daily


def build_daily_embed(result: DailyClaimResult) -> discord.Embed:
    """Render the ``/daily`` claim outcome.

    Green success color; the 7-day bonus flag is surfaced in the title so
    the user can see at a glance that the bonus fired (``DailyClaimResult``
    semantics: ``streak`` resets to 0 immediately after the bonus pays out).
    """
    title = "Daily Reward — Streak Bonus!" if result.is_streak_bonus else "Daily Reward"
    streak_line = (
        "Streak reset after 7-day bonus."
        if result.is_streak_bonus
        else f"Streak: {result.streak}"
    )
    embed = discord.Embed(
        title=title,
        color=COLOR_SUCCESS,
        description=(
            f"Reward: {_money(result.reward)}\n"
            f"{streak_line}\n"
            f"New balance: {_money(result.new_cash_balance)}"
        ),
    )
    return embed


# ---------------------------------------------------------------------------
# /price · /mystock


def build_price_embed(stats: PriceStats) -> discord.Embed:
    """Render a price-stats lookup (used by ``/price`` and ``/mystock``)."""
    embed = discord.Embed(
        title=f"Price — {_user_mention(stats.user_id)}",
        color=COLOR_NEUTRAL,
        description=f"Current: {_money(stats.current)}",
    )
    embed.add_field(name="24h High", value=_money(stats.high_24h), inline=True)
    embed.add_field(name="24h Low", value=_money(stats.low_24h), inline=True)
    embed.add_field(
        name="All-time High",
        value=_money(stats.all_time_high),
        inline=True,
    )
    embed.add_field(
        name="Owner",
        value=_user_mention(stats.user_id),
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# /buy


def build_buy_confirmation_embed(result: BuyResult) -> discord.Embed:
    """Render a successful ``/buy`` outcome — green confirmation."""
    embed = discord.Embed(
        title="Buy Confirmed",
        color=COLOR_SUCCESS,
        description=(
            f"Bought **{result.shares}** share(s) of "
            f"{_user_mention(result.target_id)} "
            f"at {_money(result.price_per_share)} each.\n"
            f"Total cost: {_money(result.total_cost)}"
        ),
    )
    embed.add_field(
        name="Cash balance",
        value=_money(result.new_cash_balance),
        inline=True,
    )
    embed.add_field(
        name="Price moved",
        value=f"{_money(result.old_price)} → {_money(result.new_price)}",
        inline=True,
    )
    return embed


# ---------------------------------------------------------------------------
# /sell


def build_sell_confirmation_embed(result: SellResult) -> discord.Embed:
    """Render a successful ``/sell`` outcome — green confirmation."""
    embed = discord.Embed(
        title="Sell Confirmed",
        color=COLOR_SUCCESS,
        description=(
            f"Sold **{result.shares}** share(s) of "
            f"{_user_mention(result.target_id)} "
            f"at {_money(result.price_per_share)} each.\n"
            f"Total revenue: {_money(result.total_revenue)}"
        ),
    )
    embed.add_field(
        name="Cash balance",
        value=_money(result.new_cash_balance),
        inline=True,
    )
    embed.add_field(
        name="Price moved",
        value=f"{_money(result.old_price)} → {_money(result.new_price)}",
        inline=True,
    )
    position_after = (
        f"{result.position_after.shares} shares remaining"
        if result.position_after is not None
        else "Position fully closed"
    )
    embed.add_field(name="Position", value=position_after, inline=False)
    return embed


# ---------------------------------------------------------------------------
# /short


def build_short_confirmation_embed(result: ShortResult) -> discord.Embed:
    """Render a successful ``/short`` outcome — green confirmation.

    The collateral split (``locked_cash`` from trading cash + ``locked_fund``
    from up to 50% of the personal hedge fund) is surfaced field-by-field per
    the original spec, alongside the 30-minute freeze window the position
    enters.
    """
    embed = discord.Embed(
        title="Short Opened",
        color=COLOR_SUCCESS,
        description=(
            f"Shorted **{result.shares}** share(s) of "
            f"{_user_mention(result.target_id)} "
            f"at {_money(result.price_per_share)} each.\n"
            f"Notional: {_money(result.notional)}"
        ),
    )
    embed.add_field(
        name="Collateral — Cash",
        value=_money(result.locked_cash),
        inline=True,
    )
    embed.add_field(
        name="Collateral — Fund",
        value=_money(result.locked_fund),
        inline=True,
    )
    embed.add_field(
        name="Cash balance",
        value=_money(result.new_cash_balance),
        inline=True,
    )
    embed.add_field(
        name="Fund balance",
        value=_money(result.new_fund_balance),
        inline=True,
    )
    embed.add_field(
        name="Position frozen for",
        value="30 minutes (no cover during freeze)",
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# /cover


def build_cover_confirmation_embed(result: CoverResult) -> discord.Embed:
    """Render a successful ``/cover`` outcome — green confirmation.

    ``CoverResult.pnl`` is signed *in the standard long sense*: positive =
    profit (cover price below entry), negative = loss. The embed surfaces a
    short-friendly label (``Profit`` / ``Loss``) plus the signed value so the
    user can read the outcome without parsing the sign convention.
    """
    is_profit = result.pnl >= 0
    pnl_label = "Profit" if is_profit else "Loss"
    embed = discord.Embed(
        title="Short Covered",
        color=COLOR_SUCCESS,
        description=(
            f"Covered **{result.shares}** share(s) of "
            f"{_user_mention(result.target_id)} "
            f"at {_money(result.price_per_share)} each.\n"
            f"Cost: {_money(result.cost)}"
        ),
    )
    embed.add_field(name=pnl_label, value=_signed_money(result.pnl), inline=True)
    embed.add_field(
        name="Released — Cash",
        value=_money(result.released_cash),
        inline=True,
    )
    embed.add_field(
        name="Released — Fund",
        value=_money(result.released_fund),
        inline=True,
    )
    embed.add_field(
        name="Cash balance",
        value=_money(result.new_cash_balance),
        inline=True,
    )
    embed.add_field(
        name="Fund balance",
        value=_money(result.new_fund_balance),
        inline=True,
    )
    position_after = (
        f"{result.position_after.shares} shares remaining"
        if result.position_after is not None
        else "Position fully closed"
    )
    embed.add_field(name="Position", value=position_after, inline=False)
    return embed


# ---------------------------------------------------------------------------
# /portfolio


def build_portfolio_embed(snapshot: PortfolioSnapshot) -> discord.Embed:
    """Render the full per-position ``/portfolio`` listing.

    Long and short positions are listed in their own fields so each row stays
    legible even when the user has dozens of positions. An empty portfolio
    still produces a structured embed — the empty-state copy makes the embed
    body non-empty.
    """
    embed = discord.Embed(
        title="Portfolio",
        color=COLOR_NEUTRAL,
        description=(
            f"Cash: {_money(snapshot.cash_balance)} · "
            f"Net worth: {_money(snapshot.net_worth)} · "
            f"Fund: {_money(snapshot.fund_balance)}"
        ),
    )
    if snapshot.long_positions:
        longs_value = "\n".join(
            f"{_user_mention(pos.target_user_id)} — "
            f"{pos.shares} @ {_money(pos.avg_entry)}"
            for pos in snapshot.long_positions.values()
        )
    else:
        longs_value = "_No long positions._"
    embed.add_field(name="Longs", value=longs_value, inline=False)

    if snapshot.short_positions:
        shorts_value = "\n".join(
            f"{_user_mention(pos.target_user_id)} — "
            f"{pos.shares} @ {_money(pos.entry_price)} "
            f"(collateral {_money(pos.locked_cash + pos.locked_fund)})"
            for pos in snapshot.short_positions.values()
        )
    else:
        shorts_value = "_No short positions._"
    embed.add_field(name="Shorts", value=shorts_value, inline=False)

    return embed


# ---------------------------------------------------------------------------
# /trending


def build_trending_embed(entries: Sequence[TrendingEntry]) -> discord.Embed:
    """Render the ``/trending`` leaderboard from a ranked sequence.

    Accepts any :class:`Sequence` to keep the contract permissive for the
    Phase 11 cog (which receives ``list[TrendingEntry]`` from
    :meth:`StatsService.trending_snapshot`). The embed renders an explicit
    empty-state line when the leaderboard is empty (brand-new guild).
    """
    embed = discord.Embed(
        title="Trending Stocks",
        color=COLOR_NEUTRAL,
    )
    if not entries:
        embed.description = "_No trending stocks yet — get talking!_"
        return embed
    embed.description = "\n".join(
        f"**#{entry.rank}** {_user_mention(entry.user_id)} · "
        f"{_money(entry.current_price)} · score {entry.score:.2f}"
        for entry in entries
    )
    return embed


# ---------------------------------------------------------------------------
# /mystats


def build_mystats_embed(stats: UserStats) -> discord.Embed:
    """Render the ``/mystats`` personal engagement embed."""
    embed = discord.Embed(
        title="Your Activity Stats",
        color=COLOR_NEUTRAL,
    )
    embed.add_field(
        name="Engagement tier",
        value=stats.engagement_tier,
        inline=True,
    )
    embed.add_field(
        name="Trending score",
        value=f"{stats.trending_score:.2f}",
        inline=True,
    )
    embed.add_field(
        name="Last activity",
        value=_relative_timestamp(stats.last_activity),
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# /fund info


def build_fund_info_embed(
    *,
    fund: HedgeFund,
    base_apy: float,
    effective_apy: float,
    has_penalty: bool,
) -> discord.Embed:
    """Render the ``/fund info`` embed.

    Takes the persisted :class:`HedgeFund` aggregate plus the pre-computed
    APY rates and penalty flag — the docstring on
    :meth:`FundService.fund_info` makes the embed builder responsible for
    rendering the effective APY (penalty applied) rather than passing a
    pre-baked DTO. Keeping the inputs primitive avoids defining a one-off
    read-model dataclass just for this one embed.

    ``has_penalty`` is supplied explicitly (rather than inferred from
    ``base_apy != effective_apy``) so a zero-penalty edge case still renders
    a clean "no penalty" line.
    """
    embed = discord.Embed(
        title=f"Hedge Fund — {fund.name}",
        color=COLOR_NEUTRAL,
        description=(f"**{fund.name}**\nManager: {_user_mention(fund.manager_id)}"),
    )
    embed.add_field(
        name="Balance",
        value=_money(fund.cash_balance),
        inline=True,
    )
    embed.add_field(
        name="Effective APY",
        value=_percent(effective_apy),
        inline=True,
    )
    embed.add_field(
        name="Base APY",
        value=_percent(base_apy),
        inline=True,
    )
    penalty_value = (
        f"Active — effective APY reduced from "
        f"{_percent(base_apy)} to {_percent(effective_apy)}."
        if has_penalty
        else "No active early-withdrawal penalty."
    )
    embed.add_field(name="Penalty status", value=penalty_value, inline=False)
    return embed


# ---------------------------------------------------------------------------
# Static intro


def build_intro_embed() -> discord.Embed:
    """Static introduction embed (shown on first interaction)."""
    embed = discord.Embed(
        title="Welcome to Friendex",
        color=COLOR_INFO,
        description=(
            "Friendex is a stock-exchange game where every server member "
            "has their own tradable stock. Prices move with real Discord "
            "activity — messages, voice chat, reactions, replies.\n\n"
            "Run `/help` to see every command, or jump in with `/daily` "
            "for your first reward and `/buy` to start trading."
        ),
    )
    embed.add_field(
        name="Starter cash",
        value="$10,000.00 (your trading float)",
        inline=True,
    )
    embed.add_field(
        name="Daily reward",
        value="$500.00 (plus a streak bonus every 7 days)",
        inline=True,
    )
    embed.add_field(
        name="Opt out anytime",
        value="`/optout` removes your stock from the market.",
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# /help


def build_help_embed() -> discord.Embed:
    """Static help embed — lists every canonical slash command.

    Content mirrors the "Bot Commands" table in the project ``CLAUDE.md`` so
    the help text and the architecture spec stay in lockstep.
    """
    embed = discord.Embed(
        title="Friendex Commands",
        color=COLOR_INFO,
        description=(
            "All commands are slash commands. Personal/read commands reply "
            "ephemerally; action commands reply publicly so trades stay "
            "visible in-channel."
        ),
    )
    embed.add_field(
        name="Account",
        value=(
            "`/balance` — cash + portfolio summary (ephemeral)\n"
            "`/daily` — claim daily $500 (public)\n"
            "`/mystats` — personal activity stats (ephemeral)\n"
            "`/optin` · `/optout` — consent to be tradeable (ephemeral)"
        ),
        inline=False,
    )
    embed.add_field(
        name="Market",
        value=(
            "`/price [user]` — look up a stock price (ephemeral)\n"
            "`/mystock` — your own stock stats (ephemeral)\n"
            "`/trending` — top movers leaderboard (public)"
        ),
        inline=False,
    )
    embed.add_field(
        name="Trading",
        value=(
            "`/buy <user> <shares>` — long position (public)\n"
            "`/sell <user> <shares>` — close long (public)\n"
            "`/short <user> <shares>` — open short (public, cooldown)\n"
            "`/cover <user> <shares>` — close short (public)\n"
            "`/portfolio [user]` — full portfolio view (ephemeral)"
        ),
        inline=False,
    )
    embed.add_field(
        name="Hedge fund",
        value=(
            "`/fund create` · `/fund info` · `/fund withdraw` · "
            "`/fund send_events` — hedge fund management "
            "(info ephemeral, mutations public)"
        ),
        inline=False,
    )
    return embed


# ---------------------------------------------------------------------------
# Liquidation notification


def build_liquidation_notification_embed(
    event: LiquidationEvent,
) -> discord.Embed:
    """Render a liquidation notification — orange warning embed.

    Consumed by the Phase 9 ``LiquidationTask`` notifier callback (which the
    Phase 14 composition layer wires up). The embed is generated *outside*
    the ``adapters/tasks/`` package so the task itself never imports
    ``discord`` (Phase 9 convention).
    """
    embed = discord.Embed(
        title="Short Position Liquidated",
        color=COLOR_WARNING,
        description=(
            f"{_user_mention(event.holder_id)}'s short on "
            f"{_user_mention(event.target_id)} was auto-covered after the "
            f"price crossed the liquidation threshold."
        ),
    )
    embed.add_field(name="Shares", value=str(event.shares), inline=True)
    embed.add_field(
        name="Entry price",
        value=_money(event.entry_price),
        inline=True,
    )
    embed.add_field(
        name="Exit price",
        value=_money(event.exit_price),
        inline=True,
    )
    embed.add_field(name="P&L", value=_signed_money(event.pnl), inline=True)
    embed.add_field(
        name="Collateral returned",
        value=_money(event.collateral_returned),
        inline=True,
    )
    embed.add_field(
        name="At",
        value=_relative_timestamp(event.timestamp),
        inline=True,
    )
    return embed


# ---------------------------------------------------------------------------
# Error renderer


def build_error_embed(error: DomainError) -> discord.Embed:
    """Render a :class:`DomainError` as a red embed.

    Per AC8 the user-facing message is the embed's ``description`` verbatim,
    so every :class:`DomainError` subclass picks up a consistent presentation
    without each handler needing bespoke copy.
    """
    return discord.Embed(
        title="Error",
        color=COLOR_ERROR,
        description=error.user_facing_message,
    )
