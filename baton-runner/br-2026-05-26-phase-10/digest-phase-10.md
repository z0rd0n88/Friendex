# Phase 10 digest — Discord embed builders

**Status:** CLEAN. **Branch:** `feat/phase-10-embeds`. **HEAD:** `e04f6c8`.
Full review: `baton-pass/phase-10/001-2026-05-26-phase-10-review-clean.md`.

## Public surface (in `src/friendex/adapters/discord_bot/embeds.py`)

5 color constants: `COLOR_SUCCESS` (green), `COLOR_ERROR` (red),
`COLOR_WARNING` (orange), `COLOR_INFO` (blurple), `COLOR_NEUTRAL` (blue).

15 builders:
- `build_balance_embed(snapshot: PortfolioSnapshot)` (uses all 6 fields)
- `build_daily_embed(result: DailyClaimResult)`
- `build_price_embed(stats: PriceStats)`
- `build_buy_confirmation_embed(result: BuyResult)`
- `build_sell_confirmation_embed(result: SellResult)`
- `build_short_confirmation_embed(result: ShortResult)`
- `build_cover_confirmation_embed(result: CoverResult)`
- `build_portfolio_embed(snapshot: PortfolioSnapshot)`
- `build_trending_embed(entries: Sequence[TrendingEntry])`
- `build_mystats_embed(stats: UserStats)`
- `build_fund_info_embed(*, fund, base_apy, effective_apy, has_penalty)` — **kw-only**
- `build_intro_embed()` / `build_help_embed()`
- `build_liquidation_notification_embed(event: LiquidationEvent)`
- `build_error_embed(error: DomainError)`

## Conventions Phase 11 MUST honor

- Reuse `COLOR_*` constants; do not redefine `discord.Color.*` in cogs.
- `build_fund_info_embed` is keyword-only; cog passes APYs computed from `Settings.hedge_fund_base_apy` + `compute_effective_apy(...)`.
- `embeds.py` is the ONLY `discord`-importing source file. Phase-9 invariant: `adapters/tasks/` stays discord-free. Cogs/listeners are the next allowed discord-importing layers.
- `discord.Embed` has no `allowed_mentions`. Every send echoing user input (notably `fund.name`) MUST pass `allowed_mentions=discord.AllowedMentions.none()`.
- Money: `_money` (`$1,234.56`) for non-negative; `_signed_money` (`+$50.00`/`-$25.00`) for signed. Don't pass negative Decimals through `_money` (renders `$-50.00`).
- Datetimes render via `_relative_timestamp` → `<t:UNIX:R>` (Phase 3.1 UTC-aware).
- `build_balance_embed` consumes `PortfolioSnapshot` — call `PortfolioService.portfolio_snapshot`, never re-derive `net_worth`.

Carry-forward (non-blocking): L1 stricter AC8 mutation, L2 negative-`_money` guard, I2 cog-layer mention suppression for fund names.
