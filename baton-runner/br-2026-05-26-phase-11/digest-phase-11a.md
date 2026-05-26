# Phase 11a exit digest — Discord cogs foundation (review CLEAN)

`feat/phase-11a-cogs-foundation` @ `035e99a`. Gate green: 649 pytest
(+33 from 616), 100% line+branch cov on each new cog file, ruff/format/mypy
clean. No new deps. No domain/application/persistence/tasks/embeds changes.

## Public surface (`src/friendex/adapters/discord_bot/cogs/`)

```python
# _interaction.py — private helper
def guild_id_of(interaction: discord.Interaction) -> str   # asserts guild is not None

class AccountCog(commands.Cog):
    def __init__(self, *, portfolio_service_factory: Callable[[str], PortfolioService],
                 activity_service_factory: Callable[[str], ActivityService]) -> None
    async def balance(self, interaction) -> None   # /balance  ephemeral
    async def optin(self, interaction) -> None     # /optin    ephemeral
    async def optout(self, interaction) -> None    # /optout   ephemeral

class DailyCog(commands.Cog):
    def __init__(self, *, daily_service_factory: Callable[[str], DailyService]) -> None
    async def daily(self, interaction) -> None     # /daily    PUBLIC

class AdminCog(commands.Cog):
    def __init__(self) -> None                                          # no service deps
    async def help(self, interaction) -> None      # /help              ephemeral
    @app_commands.checks.has_permissions(manage_guild=True)
    async def game_intro(self, interaction) -> None  # /game_intro      PUBLIC, admin
```

Test fixtures (`tests/adapters/discord_bot/cogs/conftest.py`):
`fake_interaction(*, user_id, guild_id) -> MagicMock`;
`{portfolio,activity,daily,stats,trading,fund}_service: AsyncMock(spec=Service)`;
`{...}_service_factory: Callable[[str], TService]` (stats/trading/fund prepped for 11b/c).

## Conventions Phase 11b/11c/13/14 MUST honour

1. **Per-guild factory ctor** on every cog (Phase 9 service_factory shape);
   resolve service via `factory(guild_id_of(interaction))`. ONE cog instance per class.
2. **`cogs/_interaction.guild_id_of`** is the routing primitive — import it; do NOT
   re-narrow `interaction.guild` inline. DM slash commands intentionally unsupported.
3. **`DomainError` propagates uncaught** — Phase 13 owns the central handler. Cogs MUST NOT
   `try/except DomainError` and MUST NOT call `build_error_embed`. Template:
   `test_*_propagates_*` with `service.method.side_effect = Err(...)` + `pytest.raises`.
4. **`COLOR_*` reuse; no inline embeds where a builder exists.** `/balance` with
   `snapshot is None` builds a small inline `COLOR_NEUTRAL` embed (documented exception
   for read commands facing a brand-new account; ephemeral + short prose). Apply
   Phase-10 I2 (`allowed_mentions=AllowedMentions.none()`) in 11c's `fund_cog` sends.
5. **Mutation-hardening bar** — every new cog test file MUST include at least one
   test that fails if `ephemeral=` flips or a permission decorator is dropped.
6. **Reply-visibility rule** — personal/read = ephemeral; action/announcement = public
   (omit `ephemeral=`; default in discord.py). Public tests assert `kwargs.get("ephemeral", False) is False`.
7. **No module-level constants in cogs** (Phase 8a). `Decimal` money + UTC-aware
   datetimes preserved (Phase 3.1). `datetime.now(tz=UTC)` at the cog boundary.
8. **Cog tests** call `Cog.command.callback(cog, interaction, ...)` directly — dpytest
   simulates message events, not slash interactions; this is the canonical 11 idiom.

## Carry-forward (non-blocking)

L1 prose glitch in `admin_cog.py:10-11`. L2 `type: ignore[no-untyped-def]` proliferation
in cog tests (Protocol-typed fixtures would clean this up). Pre-existing items unchanged
(8a 2 LOWs; 10 L1/L2; 10 I2 lands in 11c `fund_cog`).
