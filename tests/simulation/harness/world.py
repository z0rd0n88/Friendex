"""SimWorld: the composed simulation environment for one scenario.

Owns the real :class:`Container` over an in-memory SQLite engine, the fake
guild + member stubs, the central error handler (captured off a stub bot so
error paths render exactly as production), and the liquidation-event capture
notifier. Seeds accounts/stocks from the scenario's user specs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from friendex.adapters.config import Settings
from friendex.adapters.container import Container
from friendex.adapters.discord_bot.error_handler import register_error_handler
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    HedgeFund,
    Stock,
    UserAccount,
)
from tests.simulation.harness import stubs

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    import discord
    from discord import app_commands
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from friendex.application.liquidation_events import LiquidationEvent
    from tests.simulation.harness.schema import Scenario

_VALID_TOKEN = "x" * 32


class SimWorld:
    """One scenario's live environment: container, stubs, captured effects."""

    def __init__(
        self,
        scenario: Scenario,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self.scenario = scenario
        self.settings = Settings(
            discord_token=_VALID_TOKEN,
            **scenario.settings_overrides,
        )
        self.container = Container(self.settings, sessionmaker)
        self.guild_id = str(scenario.guild_id)
        self.guild = stubs.make_guild(scenario.guild_id, scenario.guild_name)
        self.members: dict[str, MagicMock] = {
            name: stubs.make_member(spec, self.guild)
            for name, spec in scenario.users.items()
        }
        self.liquidation_events: list[LiquidationEvent] = []
        # Volatile simulation bookkeeping: which VC each user is in (drives
        # the message listener's ping-host detection) and a monotonic
        # message-id counter for stub messages.
        self.voice_channel_of: dict[str, int] = {}
        self.last_message_id: int = 0
        self._message_id_counter: int = 1_000_000

        # Capture the production error handler exactly as `register_with`
        # installs it: `register_error_handler` assigns `bot.tree.on_error`,
        # so a stub bot's tree attribute receives the closure.
        bot_stub = MagicMock(name="Bot")
        register_error_handler(bot_stub, self.settings)
        self.on_tree_error: Callable[
            [discord.Interaction, app_commands.AppCommandError],
            Awaitable[None],
        ] = bot_stub.tree.on_error

        # Bind every background task to this world's single guild and wire
        # the liquidation notifier to an in-memory capture list — the same
        # seams `Container.build_runners` rebinds in production.
        async def iter_guild_ids() -> Iterable[str]:
            return (self.guild_id,)

        for task in self.container.raw_tasks:
            task.bind_guild_id_provider(iter_guild_ids)

        async def capture(event: LiquidationEvent) -> None:
            self.liquidation_events.append(event)

        self.container._liquidation_task.bind_notifier(capture)

    def member(self, name: str) -> MagicMock:
        return self.members[name]

    def next_message_id(self) -> int:
        self._message_id_counter += 1
        return self._message_id_counter

    def user_id(self, name: str) -> str:
        return str(self.scenario.users[name].id)

    def make_interaction(self, actor: str) -> MagicMock:
        return stubs.make_interaction(self.member(actor), self.guild)

    async def seed(self) -> None:
        """Create accounts + stocks declared in the scenario's user specs.

        Mirrors the seeding idiom of the existing integration test: build
        domain aggregates directly and upsert through the container's real
        repositories. Users with ``seed: false`` start with no account (the
        services auto-create on first touch), exercising the cold-start path.
        """
        start = self.scenario.start_at
        default_cash = Decimal(str(self.settings.initial_cash))
        for spec in self.scenario.users.values():
            if spec.seed:
                account = UserAccount(
                    user_id=str(spec.id),
                    cash_balance=spec.cash if spec.cash is not None else default_cash,
                    net_worth=spec.cash if spec.cash is not None else default_cash,
                    month_start_net_worth=(
                        spec.cash if spec.cash is not None else default_cash
                    ),
                    long_positions={},
                    short_positions={},
                    today=ActivityBucket(bucket_start=start),
                    week=ActivityBucket(bucket_start=start),
                    daily=DailyProgress(last_claim=None, streak=0),
                    last_activity=start,
                    opt_in=spec.opted_in,
                    intro_shown=False,
                )
                await self.container._user_repo.upsert(self.guild_id, account)
            if spec.fund_balance is not None:
                fund = HedgeFund(
                    fund_id=str(spec.id),
                    name=f"{spec.name}'s fund",
                    manager_id=str(spec.id),
                    cash_balance=spec.fund_balance,
                    investors={},
                )
                await self.container._fund_repo.upsert(self.guild_id, fund)
            if spec.price is not None:
                stock = Stock(
                    user_id=str(spec.id),
                    current=spec.price,
                    history=[],
                    high_24h=spec.price,
                    low_24h=spec.price,
                    all_time_high=spec.price,
                )
                await self.container._price_repo.upsert(self.guild_id, stock)
