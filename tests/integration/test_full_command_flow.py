"""End-to-end command flow over a real :class:`Container` + in-memory SQLite.

This is the Phase 14 integration smoke test: build the bot via
:func:`build_bot`, wire it to a real :class:`Container` whose repositories
sit on top of an in-memory ``sqlite+aiosqlite:///:memory:`` engine, then
drive ``/daily`` -> ``/buy`` -> ``/portfolio`` and assert each emits the
expected embed *and* the database reflects the trade.

**Why direct cog-callback invocation instead of ``dpytest``.** ``dpytest``
simulates message events (the prefix-command surface); Friendex is
slash-only (:mod:`discord.app_commands`). Driving a real
:class:`app_commands.Command` end-to-end requires a live gateway connection
or a dpytest extension that does not exist for our discord.py version.
STATE.md AC5 explicitly permits the fallback to direct cog-callback
invocation against a stub :class:`discord.Interaction` — the same idiom
the Phase 11 cog tests use. We follow that path here; the load-bearing
property is the *full container → service → repository → DB* round trip,
which is independent of how the cog callback is reached.

**Real container, no application mocks.** Every test in this module
constructs a real :class:`Container` over a real (in-memory) async engine.
The cogs are pulled off ``container.cogs`` rather than constructed
locally; this proves the container wires every dependency end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
import pytest_asyncio
from freezegun import freeze_time

from friendex.adapters.config import Settings
from friendex.adapters.container import Container
from friendex.adapters.discord_bot.bot import build_bot
from friendex.adapters.discord_bot.cogs.daily_cog import DailyCog
from friendex.adapters.discord_bot.cogs.portfolio_cog import PortfolioCog
from friendex.adapters.discord_bot.cogs.trading_cog import TradingCog
from friendex.adapters.discord_bot.embeds import COLOR_NEUTRAL, COLOR_SUCCESS
from friendex.adapters.persistence.db import (
    Base,
    build_engine,
    build_sessionmaker,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )

    from friendex.adapters.tasks.task_runner import TaskRunner


_VALID_TOKEN = "x" * 32
# A weekday inside market hours (Mon 12:00 UTC) so /buy isn't blocked by
# the trading-service market-hours guard.
_MARKET_OPEN = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures — real container over in-memory SQLite


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """A fresh in-memory SQLite engine with the full schema created."""
    eng = build_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return build_sessionmaker(engine)


@pytest.fixture
def settings() -> Settings:
    return Settings(discord_token=_VALID_TOKEN)


@pytest.fixture
def container(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> Container:
    return Container(settings, sessionmaker)


def _stub_member(user_id: int) -> MagicMock:
    """Stub :class:`discord.Member` exposing just the integer ``id``."""
    member = MagicMock(name="Member", spec=discord.Member)
    member.id = user_id
    return member


def _stub_interaction(*, user_id: int, guild_id: int) -> MagicMock:
    """Stub :class:`discord.Interaction` carrying the slots cogs read.

    Matches the conftest fixture used by the Phase 11 cog tests; duplicated
    here so the integration package has no cross-package dependency.
    """
    interaction = MagicMock(name="Interaction")
    interaction.response.send_message = AsyncMock(name="response.send_message")
    interaction.response.defer = AsyncMock(name="response.defer")
    interaction.followup.send = AsyncMock(name="followup.send")
    interaction.user.id = user_id
    interaction.guild.id = guild_id
    return interaction


def _embed_from_send(interaction: MagicMock) -> discord.Embed:
    """Pull the ``embed=`` kwarg off the most recent ``followup.send`` call.

    Wave 1 (#82 H13) routed every cog reply through ``followup.send`` after
    a ``response.defer(...)``; the integration helper follows.
    """
    assert interaction.followup.send.await_count >= 1
    kwargs = interaction.followup.send.await_args.kwargs
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    return embed


def _cog_of_type(container: Container, cog_type: type) -> object:
    """Return the cog of the given class from the container's tuple."""
    for cog in container.cogs:
        if isinstance(cog, cog_type):
            return cog
    raise AssertionError(f"{cog_type.__name__} not present in container.cogs")


# ---------------------------------------------------------------------------
# Bot factory smoke — proves build_bot composes with a real container.


async def test_build_bot_wires_real_container(
    settings: Settings,
    container: Container,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build the bot, run ``setup_hook`` over the real container.

    Patches ``tree.sync`` + each runner's ``start`` so no network or live loop
    runs; the assertion is purely that the wiring composes — cogs are
    registered, runners are started, no exception escapes.
    """
    bot = build_bot(settings, container)
    bot.tree.sync = AsyncMock(name="tree.sync")  # type: ignore[method-assign]
    bot._connection._guilds = {}
    bot.add_cog = AsyncMock(name="add_cog")  # type: ignore[method-assign]

    captured: list[TaskRunner] = []
    real_br = container.build_runners

    def stub(bot):  # type: ignore[return]
        runners = real_br(bot)
        for r in runners:
            # Replace the bound ``start`` method with a MagicMock so the test
            # can assert the call without actually firing the task loop.
            r.start = MagicMock()  # type: ignore[method-assign]
        captured.extend(runners)
        return runners

    monkeypatch.setattr(container, "build_runners", stub)

    await bot.setup_hook()

    # 7 cogs + 4 listeners + 1 Wave-1 _GuildLifecycleCog = 12 add_cog calls.
    assert bot.add_cog.await_count == 12
    assert len(captured) == 8
    for runner in captured:
        # ``runner.start`` was monkey-patched to MagicMock above; the static
        # type comes from ``TaskRunner.start`` so we cast to access ``call_count``.
        assert cast("MagicMock", runner.start).call_count == 1
    bot.tree.sync.assert_awaited()


# ---------------------------------------------------------------------------
# End-to-end command flow: /daily -> /buy -> /portfolio


async def test_full_command_flow_daily_buy_portfolio(
    container: Container,
) -> None:
    """Drive three cog callbacks over a shared in-memory DB and assert the chain."""
    daily_cog = _cog_of_type(container, DailyCog)
    trading_cog = _cog_of_type(container, TradingCog)
    portfolio_cog = _cog_of_type(container, PortfolioCog)

    guild_id = 1234567890
    actor_id = 1111
    target_id = 2222

    # Step 1: actor opts in to be tradeable (not strictly needed for /buy
    # since the buyer is the actor, but /buy requires the *target* to be
    # opted in — so we opt the target in by going through the daily +
    # account flow which auto-creates accounts. The simpler path is to
    # directly opt the target in via the user repo, since the AccountCog
    # /optin path requires a fully-mocked interaction. Reuse the container's
    # user repo directly — it's a real port over the same sessionmaker.
    user_repo = container._user_repo
    from friendex.domain.models import (
        ActivityBucket,
        DailyProgress,
        UserAccount,
    )

    # The trading service auto-creates accounts via ``_get_or_create_user``
    # so we only need the *target* opted in. Opting in requires the account
    # to exist with ``opt_in=True``; build it directly to keep the test focused.
    target_account = UserAccount(
        user_id=str(target_id),
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=_MARKET_OPEN),
        week=ActivityBucket(bucket_start=_MARKET_OPEN),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=_MARKET_OPEN,
        opt_in=True,
        intro_shown=False,
    )
    await user_repo.upsert(str(guild_id), target_account)

    with freeze_time(_MARKET_OPEN):
        # --- /daily ----------------------------------------------------
        daily_interaction = _stub_interaction(user_id=actor_id, guild_id=guild_id)
        await DailyCog.daily.callback(daily_cog, daily_interaction)

        daily_embed = _embed_from_send(daily_interaction)
        daily_data = daily_embed.to_dict()
        assert daily_data["color"] == COLOR_SUCCESS.value
        # First claim: streak 1, reward $500, balance 10,500.
        assert "$500.00" in (daily_data.get("description") or "")

        # Actor account is now persisted with cash_balance 10500.
        post_daily = await user_repo.get(str(guild_id), str(actor_id))
        assert post_daily is not None
        assert post_daily.cash_balance == Decimal("10500.00")

        # --- /buy ------------------------------------------------------
        buy_interaction = _stub_interaction(user_id=actor_id, guild_id=guild_id)
        target_member = _stub_member(target_id)
        await TradingCog.buy.callback(
            trading_cog,
            buy_interaction,
            user=target_member,
            shares=1,
        )

        buy_embed = _embed_from_send(buy_interaction)
        buy_data = buy_embed.to_dict()
        assert buy_data["color"] == COLOR_SUCCESS.value
        # Initial price is $100 per Settings.initial_price; cost = $100.00.
        assert "$100.00" in (buy_data.get("description") or "")

        # Actor's long position is persisted: 1 share of target.
        post_buy = await user_repo.get(str(guild_id), str(actor_id))
        assert post_buy is not None
        assert str(target_id) in post_buy.long_positions
        pos = post_buy.long_positions[str(target_id)]
        assert pos.shares == 1
        # Cash dropped by $100.
        assert post_buy.cash_balance == Decimal("10400.00")

        # --- /portfolio ------------------------------------------------
        portfolio_interaction = _stub_interaction(user_id=actor_id, guild_id=guild_id)
        await PortfolioCog.portfolio.callback(
            portfolio_cog,
            portfolio_interaction,
            user=None,
        )

        portfolio_embed = _embed_from_send(portfolio_interaction)
        portfolio_data = portfolio_embed.to_dict()
        assert portfolio_data["color"] == COLOR_NEUTRAL.value
        # The long position must appear in the embed — search via the
        # rendered "Longs" field.
        fields = portfolio_data.get("fields", [])
        longs_field = next(f for f in fields if f.get("name") == "Longs")
        assert str(target_id) in longs_field["value"]
        # Ephemeral on read-commands per CLAUDE.md visibility table.
        # Wave 1 (#82 H13): replies route through ``followup.send`` after a
        # ``response.defer(ephemeral=True)``.
        send_kwargs = portfolio_interaction.followup.send.await_args.kwargs
        assert send_kwargs.get("ephemeral") is True
