# Phase 11c exit digest — trading + fund cogs (review CLEAN)

`feat/phase-11c-cogs-trade` @ `bced465`. Gate green: 703 pytest (+34),
ruff/format/mypy clean, 100 % line+branch cov per new cog. No new deps;
no edits to 11a/11b cogs, embeds, services, fixtures.

## Public surface (`src/friendex/adapters/discord_bot/cogs/`)

```python
class TradingCog(commands.Cog):
    def __init__(self, *, trading_service_factory: Callable[[str], TradingService]) -> None
    async def buy / sell / short / cover(self, interaction, user, shares: Range[int,1,None]) -> None  # all PUBLIC

class FundGroup(app_commands.Group):  # name="fund"
    def __init__(self, *, fund_service_factory: Callable[[str], FundService], settings: Settings) -> None
    async def create(name=None)        # PUBLIC      — confirms via build_fund_info_embed
    async def info(user=None)          # EPHEMERAL   — None-fund fallback = inline COLOR_NEUTRAL embed
    async def withdraw(amount: float)  # PUBLIC      — Decimal(str(amount)), datetime.now(tz=UTC)
    async def send_events(amount)      # PUBLIC      — penalty-exempt path
    async def invest(user, amount)     # uncaught NotImplementedError per §Open-Q5

class FundCog(commands.Cog):
    self.group: FundGroup   # Phase 13: bot.tree.add_command(fund_cog.group)
```

Trading service calls: positional `(actor_id, target_id, shares)` (8c).
Fund: `fund_info(user_id=…)` kw; `create_or_rename(uid, name=…)`; `withdraw/send_to_events/invest` positional. Per 8e `fund_id == manager.id`, so `/fund invest user` maps to that user's fund.

## Conventions Phase 12/13/14 MUST honour

1. **`FundGroup` is the class; `FundCog.group` is the instance.** Phase 13 wires `bot.tree.add_command(fund_cog.group)` — NOT each subcommand. The cog registers zero `@app_commands.command`s.
2. **Tree-wide error handler is Phase 13's job.** 8 uncaught `DomainError` paths + 1 `NotImplementedError`. Phase 13 registers `tree.error` rendering `build_error_embed(error)` for DomainError; generic op-facing message otherwise.
3. **I2 uniform** — `allowed_mentions=AllowedMentions.none()` on every send in both cogs (fund.name is user input). Phase 12 follows for echoed inputs.
4. All 11a/11b conventions still apply: kw-only factory ctor, `guild_id_of`, `COLOR_*` from embeds, callback-direct tests, `Decimal(str(amount))`, `datetime.now(tz=UTC)` at cog boundary, no module-level constants.

## Carry-forward (non-blocking)

LOW-1 dyadic-literal money mutation gap (100.50/75.25 exact in binary; add `0.1`). LOW-2 `/fund info` APY assertion weak when penalty=None. LOW-3 ARCH.md stale (run `.githooks/gen_arch.py`). Phase 10 I2 complete; 11b L1 + prior carry-forwards untouched.
