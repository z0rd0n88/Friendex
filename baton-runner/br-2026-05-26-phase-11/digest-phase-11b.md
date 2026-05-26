# Phase 11b exit digest — read-only cogs (portfolio, stats) (review CLEAN)

`feat/phase-11b-cogs-read` @ `924efe2`. Gate green: 669 pytest (+20),
100 % line+branch cov per new cog, ruff/format/mypy clean. No new deps;
no edits to 11a cogs, embeds, services, or fixtures.

## Public surface (`src/friendex/adapters/discord_bot/cogs/`)

```python
class PortfolioCog(commands.Cog):
    def __init__(self, *, portfolio_service_factory: Callable[[str], PortfolioService]) -> None
    async def portfolio(self, interaction, user: discord.Member | None = None) -> None
        # /portfolio [user]  EPHEMERAL  default user=invoker; None-snapshot → inline COLOR_NEUTRAL embed

class StatsCog(commands.Cog):
    def __init__(self, *, stats_service_factory: Callable[[str], StatsService]) -> None
    async def trending(self, interaction) -> None                          # /trending PUBLIC
    async def mystats(self,  interaction) -> None                          # /mystats  EPHEMERAL
    async def price(self,    interaction, user: discord.Member) -> None    # /price <user> EPHEMERAL
    async def mystock(self,  interaction) -> None                          # /mystock  EPHEMERAL — own command, shares build_price_embed with /price
```

No new helpers — both cogs re-use `_interaction.guild_id_of` from 11a. All
four `None`-return paths render inline `COLOR_NEUTRAL` embed (no builder).

## Conventions Phase 11c MUST honour

1. All 11a conventions still apply (kw-only factory ctor, `guild_id_of`,
   `DomainError` propagates, `COLOR_*` reuse, mutation-hardening,
   reply-visibility, no module-level constants, callback-direct tests).
2. `AllowedMentions.none()` lands in 11c `fund_cog` sends (Phase 10 I2 +
   11a §4 carry-forward; 11b did not apply it).
3. Service call style is kw-named (`service.method(user_id=...)`).
4. "Self-alias" shape (`/mystock`): two separate `@app_commands.command`s
   sharing a builder — not a default-argument variant.
5. `None`-return read fallback uses inline `COLOR_NEUTRAL`; `build_error_embed` is reserved for the Phase 13 handler.

## Carry-forward (non-blocking)

L1: positional-vs-kw inconsistency on `portfolio_snapshot` between
`AccountCog.balance` and `PortfolioCog.portfolio`. 11a L1/L2 + 10 L1/L2 +
8a 2 LOWs + 8c M2 untouched.
