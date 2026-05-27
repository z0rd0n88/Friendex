"""Structural tests for the Phase 10 Discord embed builders.

Each builder returns a :class:`discord.Embed`; we assert on the structure via
:meth:`discord.Embed.to_dict` rather than a live bot or Discord network call.
Per the work-unit spec (`docs/04-migration-plan.md` §Phase 10) every embed
must carry a title, body (description or fields), and a semantic color.

The tests are organised by builder and follow the order the public surface is
exported from :mod:`friendex.adapters.discord_bot.embeds`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import discord

from friendex.adapters.discord_bot.embeds import (
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_NEUTRAL,
    COLOR_SUCCESS,
    COLOR_WARNING,
    build_balance_embed,
    build_buy_confirmation_embed,
    build_cover_confirmation_embed,
    build_daily_embed,
    build_error_embed,
    build_fund_info_embed,
    build_help_embed,
    build_intro_embed,
    build_liquidation_notification_embed,
    build_mystats_embed,
    build_portfolio_embed,
    build_price_embed,
    build_sell_confirmation_embed,
    build_short_confirmation_embed,
    build_trending_embed,
)
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
from friendex.domain.errors import (
    DomainError,
    InsufficientFunds,
    MarketClosed,
    SelfTrade,
)
from friendex.domain.models import HedgeFund, LongPosition, ShortPosition

NOW = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Module-level color palette


def test_color_palette_is_exported_as_module_constants() -> None:
    """The semantic color palette is exposed for cogs/listeners to reuse."""
    assert isinstance(COLOR_SUCCESS, discord.Color)
    assert isinstance(COLOR_ERROR, discord.Color)
    assert isinstance(COLOR_WARNING, discord.Color)
    assert isinstance(COLOR_INFO, discord.Color)
    assert isinstance(COLOR_NEUTRAL, discord.Color)
    # Sanity: each color is distinct so embeds in different categories
    # render with visually distinct chrome.
    palette = {COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING, COLOR_INFO, COLOR_NEUTRAL}
    assert len(palette) == 5


# ---------------------------------------------------------------------------
# build_balance_embed


def _portfolio_snapshot(
    *,
    user_id: str = "user-1",
    cash: Decimal = Decimal("9500.00"),
    net_worth: Decimal = Decimal("12_345.67"),
    month_start_net_worth: Decimal = Decimal("10_000.00"),
    fund_balance: Decimal = Decimal("500.00"),
    long_positions: dict[str, LongPosition] | None = None,
    short_positions: dict[str, ShortPosition] | None = None,
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        user_id=user_id,
        cash_balance=cash,
        net_worth=net_worth,
        month_start_net_worth=month_start_net_worth,
        fund_balance=fund_balance,
        long_positions=long_positions or {},
        short_positions=short_positions or {},
    )


def test_build_balance_embed_returns_discord_embed_with_title_and_color() -> None:
    snapshot = _portfolio_snapshot()
    embed = build_balance_embed(snapshot)
    data = embed.to_dict()
    assert isinstance(embed, discord.Embed)
    assert data["title"] is not None
    assert "balance" in data["title"].lower()
    assert data["color"] == COLOR_NEUTRAL.value


def test_build_balance_embed_renders_cash_with_two_decimal_money_formatting() -> None:
    snapshot = _portfolio_snapshot(cash=Decimal("1234.50"))
    embed = build_balance_embed(snapshot)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$1,234.50" in rendered


def test_build_balance_embed_includes_net_worth_and_fund_balance() -> None:
    snapshot = _portfolio_snapshot(
        net_worth=Decimal("11_111.11"), fund_balance=Decimal("222.22")
    )
    embed = build_balance_embed(snapshot)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$11,111.11" in rendered
    assert "$222.22" in rendered


# ---------------------------------------------------------------------------
# build_daily_embed


def _daily_result(
    *,
    streak: int = 3,
    reward: Decimal = Decimal("500.00"),
    is_streak_bonus: bool = False,
    new_cash_balance: Decimal = Decimal("10_500.00"),
) -> DailyClaimResult:
    return DailyClaimResult(
        user_id="user-1",
        streak=streak,
        reward=reward,
        is_streak_bonus=is_streak_bonus,
        new_cash_balance=new_cash_balance,
        claim_date=NOW,
    )


def test_build_daily_embed_uses_success_color() -> None:
    embed = build_daily_embed(_daily_result())
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


def test_build_daily_embed_renders_reward_and_streak() -> None:
    embed = build_daily_embed(_daily_result(streak=3, reward=Decimal("500.00")))
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") for f in data.get("fields", [])
    )
    assert "$500.00" in rendered
    assert "3" in rendered  # streak counter is rendered somewhere


def test_build_daily_embed_marks_streak_bonus_on_day_7() -> None:
    """Day-7 bonus claim renders an explicit bonus indicator."""
    result = _daily_result(
        streak=0,  # spec: streak resets to 0 after day-7 bonus fires
        reward=Decimal("1500.00"),
        is_streak_bonus=True,
    )
    embed = build_daily_embed(result)
    data = embed.to_dict()
    rendered = (
        (data.get("title") or "")
        + (data.get("description") or "")
        + "".join(
            f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
        )
    )
    assert "bonus" in rendered.lower()


# ---------------------------------------------------------------------------
# build_price_embed


def test_build_price_embed_renders_current_price_high_low_and_owner() -> None:
    stats = PriceStats(
        user_id="user-42",
        current=Decimal("150.25"),
        high_24h=Decimal("160.00"),
        low_24h=Decimal("140.00"),
        all_time_high=Decimal("200.00"),
    )
    embed = build_price_embed(stats)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "$150.25" in rendered
    assert "$160.00" in rendered
    assert "$140.00" in rendered
    assert "user-42" in rendered
    assert data["color"] == COLOR_NEUTRAL.value


def test_build_price_embed_renders_snowflake_id_as_discord_mention() -> None:
    """Numeric Discord snowflakes render as ``<@id>`` mentions for live use."""
    stats = PriceStats(
        user_id="123456789012345678",
        current=Decimal("100.00"),
        high_24h=Decimal("100.00"),
        low_24h=Decimal("100.00"),
        all_time_high=Decimal("100.00"),
    )
    embed = build_price_embed(stats)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "<@123456789012345678>" in rendered


# ---------------------------------------------------------------------------
# build_buy_confirmation_embed


def _buy_result(
    *,
    shares: int = 10,
    price_per_share: Decimal = Decimal("100.00"),
    total_cost: Decimal = Decimal("1000.00"),
) -> BuyResult:
    return BuyResult(
        buyer_id="buyer-1",
        target_id="target-1",
        shares=shares,
        price_per_share=price_per_share,
        total_cost=total_cost,
        old_price=Decimal("99.00"),
        new_price=Decimal("101.00"),
        new_cash_balance=Decimal("9000.00"),
        position_after=LongPosition(
            target_user_id="target-1",
            shares=shares,
            avg_entry=price_per_share,
        ),
    )


def test_build_buy_confirmation_embed_uses_success_color() -> None:
    embed = build_buy_confirmation_embed(_buy_result())
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


def test_build_buy_confirmation_embed_renders_shares_target_and_cost() -> None:
    embed = build_buy_confirmation_embed(
        _buy_result(
            shares=10,
            price_per_share=Decimal("100.00"),
            total_cost=Decimal("1000.00"),
        )
    )
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "10" in rendered
    assert "target-1" in rendered
    assert "$1,000.00" in rendered


# ---------------------------------------------------------------------------
# build_sell_confirmation_embed


def _sell_result(
    *,
    shares: int = 5,
    price_per_share: Decimal = Decimal("110.00"),
    total_revenue: Decimal = Decimal("550.00"),
    position_after: LongPosition | None = None,
) -> SellResult:
    return SellResult(
        seller_id="seller-1",
        target_id="target-1",
        shares=shares,
        price_per_share=price_per_share,
        total_revenue=total_revenue,
        old_price=Decimal("111.00"),
        new_price=Decimal("109.00"),
        new_cash_balance=Decimal("10_550.00"),
        position_after=position_after,
    )


def test_build_sell_confirmation_embed_uses_success_color() -> None:
    embed = build_sell_confirmation_embed(_sell_result())
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


def test_build_sell_confirmation_embed_renders_shares_revenue_and_target() -> None:
    embed = build_sell_confirmation_embed(
        _sell_result(
            shares=5,
            total_revenue=Decimal("550.00"),
        )
    )
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "5" in rendered
    assert "$550.00" in rendered
    assert "target-1" in rendered


# ---------------------------------------------------------------------------
# build_short_confirmation_embed


def _short_result() -> ShortResult:
    return ShortResult(
        shorter_id="shorter-1",
        target_id="target-1",
        shares=10,
        price_per_share=Decimal("100.00"),
        notional=Decimal("1000.00"),
        locked_cash=Decimal("750.00"),
        locked_fund=Decimal("250.00"),
        old_price=Decimal("101.00"),
        new_price=Decimal("99.00"),
        new_cash_balance=Decimal("9250.00"),
        new_fund_balance=Decimal("250.00"),
        position_after=ShortPosition(
            target_user_id="target-1",
            shares=10,
            entry_price=Decimal("100.00"),
            locked_cash=Decimal("750.00"),
            locked_fund=Decimal("250.00"),
            created_at=NOW,
        ),
    )


def test_build_short_confirmation_embed_uses_success_color() -> None:
    embed = build_short_confirmation_embed(_short_result())
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


def test_build_short_confirmation_embed_renders_collateral_split() -> None:
    embed = build_short_confirmation_embed(_short_result())
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "$750.00" in rendered  # locked_cash
    assert "$250.00" in rendered  # locked_fund


def test_build_short_confirmation_embed_mentions_freeze_duration() -> None:
    """Short opens a 30-minute freeze window — surface it in the embed."""
    embed = build_short_confirmation_embed(_short_result())
    data = embed.to_dict()
    rendered = (
        (data.get("description") or "")
        + "".join(
            f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
        )
    ).lower()
    assert "freeze" in rendered or "frozen" in rendered or "30" in rendered


# ---------------------------------------------------------------------------
# build_cover_confirmation_embed


def _cover_result(*, pnl: Decimal = Decimal("50.00")) -> CoverResult:
    return CoverResult(
        coverer_id="shorter-1",
        target_id="target-1",
        shares=10,
        price_per_share=Decimal("95.00"),
        cost=Decimal("950.00"),
        pnl=pnl,
        released_cash=Decimal("750.00"),
        released_fund=Decimal("250.00"),
        old_price=Decimal("96.00"),
        new_price=Decimal("94.00"),
        new_cash_balance=Decimal("10_300.00"),
        new_fund_balance=Decimal("500.00"),
        position_after=None,
    )


def test_build_cover_confirmation_embed_uses_success_color() -> None:
    embed = build_cover_confirmation_embed(_cover_result())
    data = embed.to_dict()
    assert data["color"] == COLOR_SUCCESS.value


def test_build_cover_confirmation_embed_renders_pnl_and_cost() -> None:
    embed = build_cover_confirmation_embed(_cover_result(pnl=Decimal("50.00")))
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    # cost is rendered with money formatting
    assert "$950.00" in rendered
    # pnl is also rendered
    assert "50.00" in rendered


def test_build_cover_confirmation_embed_distinguishes_profit_and_loss() -> None:
    """Per ``CoverResult.pnl`` semantics: positive = profit, negative = loss."""
    profit = build_cover_confirmation_embed(_cover_result(pnl=Decimal("50.00")))
    loss = build_cover_confirmation_embed(_cover_result(pnl=Decimal("-25.00")))
    profit_text = (profit.to_dict().get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "")
        for f in profit.to_dict().get("fields", [])
    )
    loss_text = (loss.to_dict().get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in loss.to_dict().get("fields", [])
    )
    # The renderer must visibly distinguish the two states (sign, word, or
    # icon) so the user can tell at a glance whether they made money.
    assert profit_text != loss_text


# ---------------------------------------------------------------------------
# build_portfolio_embed


def test_build_portfolio_embed_lists_each_long_and_short_position() -> None:
    snapshot = _portfolio_snapshot(
        long_positions={
            "tgt-A": LongPosition(
                target_user_id="tgt-A",
                shares=10,
                avg_entry=Decimal("100.00"),
            )
        },
        short_positions={
            "tgt-B": ShortPosition(
                target_user_id="tgt-B",
                shares=5,
                entry_price=Decimal("200.00"),
                locked_cash=Decimal("500.00"),
                locked_fund=Decimal("500.00"),
                created_at=NOW,
            )
        },
    )
    embed = build_portfolio_embed(snapshot)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "tgt-A" in rendered
    assert "tgt-B" in rendered
    assert data["color"] == COLOR_NEUTRAL.value


def test_build_portfolio_embed_handles_empty_portfolio() -> None:
    """Empty portfolio renders cleanly — no crash, has body content."""
    embed = build_portfolio_embed(_portfolio_snapshot())
    data = embed.to_dict()
    assert data["title"] is not None
    assert data.get("description") or data.get("fields")


# ---------------------------------------------------------------------------
# build_trending_embed


def test_build_trending_embed_renders_ranked_leaderboard() -> None:
    entries = [
        TrendingEntry(
            rank=1, user_id="winner", score=99.9, current_price=Decimal("250.00")
        ),
        TrendingEntry(
            rank=2, user_id="runner", score=80.0, current_price=Decimal("180.00")
        ),
        TrendingEntry(
            rank=3, user_id="third", score=60.0, current_price=Decimal("120.00")
        ),
    ]
    embed = build_trending_embed(entries)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "winner" in rendered
    assert "runner" in rendered
    assert "third" in rendered
    assert "$250.00" in rendered


def test_build_trending_embed_handles_empty_list() -> None:
    embed = build_trending_embed([])
    data = embed.to_dict()
    assert data["title"] is not None
    assert data.get("description") or data.get("fields")


# ---------------------------------------------------------------------------
# build_mystats_embed


def test_build_mystats_embed_renders_engagement_tier_and_score() -> None:
    stats = UserStats(
        user_id="user-1",
        trending_score=42.5,
        engagement_tier="High",
        last_activity=NOW,
    )
    embed = build_mystats_embed(stats)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "High" in rendered
    assert "42" in rendered  # the score appears, formatted somehow
    assert data["color"] == COLOR_NEUTRAL.value


# ---------------------------------------------------------------------------
# build_fund_info_embed


def test_build_fund_info_embed_renders_fund_name_balance_and_apy() -> None:
    fund = HedgeFund(
        fund_id="user-1",
        name="Alex's Fund",
        manager_id="user-1",
        cash_balance=Decimal("1234.56"),
        investors={},
    )
    embed = build_fund_info_embed(
        fund=fund,
        base_apy=0.15,
        effective_apy=0.10,
        has_penalty=True,
    )
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "Alex's Fund" in rendered
    assert "$1,234.56" in rendered
    # APY rendered as percentage
    assert "10" in rendered
    assert data["color"] == COLOR_NEUTRAL.value


def test_build_fund_info_embed_indicates_active_penalty() -> None:
    fund = HedgeFund(
        fund_id="user-1",
        name="Fund 1",
        manager_id="user-1",
        cash_balance=Decimal("100.00"),
        investors={},
    )
    with_penalty = build_fund_info_embed(
        fund=fund,
        base_apy=0.15,
        effective_apy=0.10,
        has_penalty=True,
    )
    without = build_fund_info_embed(
        fund=fund,
        base_apy=0.15,
        effective_apy=0.15,
        has_penalty=False,
    )
    text_with = (with_penalty.to_dict().get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "")
        for f in with_penalty.to_dict().get("fields", [])
    )
    text_without = (without.to_dict().get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "")
        for f in without.to_dict().get("fields", [])
    )
    # Penalty status changes the rendered output.
    assert text_with != text_without


# ---------------------------------------------------------------------------
# build_intro_embed


def test_build_intro_embed_returns_static_intro_with_title_and_body() -> None:
    embed = build_intro_embed()
    data = embed.to_dict()
    assert isinstance(embed, discord.Embed)
    assert data["title"] is not None
    assert data.get("description") or data.get("fields")
    # Intro is an informational embed.
    assert data["color"] == COLOR_INFO.value


# ---------------------------------------------------------------------------
# build_help_embed


def test_build_help_embed_lists_canonical_commands() -> None:
    embed = build_help_embed()
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    # Each canonical slash command appears at least once.
    for cmd in (
        "/balance",
        "/daily",
        "/price",
        "/buy",
        "/sell",
        "/short",
        "/cover",
        "/portfolio",
        "/fund",
        "/trending",
        "/mystats",
        "/optin",
        "/optout",
        "/mystock",
    ):
        assert cmd in rendered, f"Help embed missing {cmd}"
    assert data["color"] == COLOR_INFO.value


# ---------------------------------------------------------------------------
# build_liquidation_notification_embed


def test_build_liquidation_notification_embed_uses_warning_color() -> None:
    event = LiquidationEvent(
        guild_id="guild-1",
        holder_id="holder",
        target_id="target",
        shares=10,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("150.00"),
        collateral_returned=Decimal("0.00"),
        pnl=Decimal("-500.00"),
        timestamp=NOW,
    )
    embed = build_liquidation_notification_embed(event)
    data = embed.to_dict()
    assert data["color"] == COLOR_WARNING.value


def test_build_liquidation_notification_embed_renders_event_details() -> None:
    event = LiquidationEvent(
        guild_id="guild-1",
        holder_id="holder-77",
        target_id="target-99",
        shares=10,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("150.00"),
        collateral_returned=Decimal("0.00"),
        pnl=Decimal("-500.00"),
        timestamp=NOW,
    )
    embed = build_liquidation_notification_embed(event)
    data = embed.to_dict()
    rendered = (data.get("description") or "") + "".join(
        f.get("value", "") + f.get("name", "") for f in data.get("fields", [])
    )
    assert "holder-77" in rendered
    assert "target-99" in rendered
    assert "$100.00" in rendered  # entry
    assert "$150.00" in rendered  # exit


# ---------------------------------------------------------------------------
# build_error_embed


def test_build_error_embed_uses_error_color() -> None:
    err = InsufficientFunds(need=Decimal("100.00"), have=Decimal("10.00"))
    embed = build_error_embed(err)
    data = embed.to_dict()
    assert data["color"] == COLOR_ERROR.value


def test_build_error_embed_renders_user_facing_message_verbatim() -> None:
    err = InsufficientFunds(need=Decimal("100.00"), have=Decimal("10.00"))
    embed = build_error_embed(err)
    data = embed.to_dict()
    # AC8: user_facing_message MUST appear verbatim in the description.
    assert data["description"] == err.user_facing_message


def test_build_error_embed_renders_self_trade_message() -> None:
    err = SelfTrade()
    embed = build_error_embed(err)
    data = embed.to_dict()
    assert data["description"] == err.user_facing_message


def test_build_error_embed_renders_market_closed_message() -> None:
    from datetime import time

    err = MarketClosed(open_at=time(6, 30), close_at=time(4, 30))
    embed = build_error_embed(err)
    data = embed.to_dict()
    assert data["description"] == err.user_facing_message


def test_build_error_embed_accepts_arbitrary_domain_error_subclass() -> None:
    """The signature is ``DomainError`` — any subclass must work."""

    class CustomDomainError(DomainError):
        def __init__(self) -> None:
            super().__init__("Custom user-facing message.")

    embed = build_error_embed(CustomDomainError())
    data = embed.to_dict()
    assert data["description"] == "Custom user-facing message."
    assert data["color"] == COLOR_ERROR.value
