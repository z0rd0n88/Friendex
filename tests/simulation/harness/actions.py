"""Action executors: dispatch one timeline entry against the SimWorld.

Three families:

* **Commands** — invoke the cog callback directly (the sanctioned slash-only
  idiom, see ``tests/integration/test_full_command_flow.py``). Any exception
  is wrapped in :class:`app_commands.CommandInvokeError` — exactly what
  discord.py does in production — and routed through the captured
  ``on_tree_error`` handler so the error *rendering* is exercised too.
* **Events** — call the listener coroutines with stub gateway objects.
* **Tasks** — await the background task's ``_run()`` for a single tick
  (tasks are plain coroutines; the discord loop machinery is bypassed by
  design — see ``adapters/tasks/base_task.py``).

``game_intro``'s ``manage_guild`` gate is emulated at the dispatch layer:
discord.py evaluates permission checks *before* the callback and dispatches
:class:`app_commands.MissingPermissions` (unwrapped) to ``tree.on_error``;
direct callback invocation would silently skip the check, so the executor
replays that dispatch when the actor lacks the permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar
from unittest.mock import MagicMock

from discord import app_commands

from friendex.adapters.discord_bot.cogs.account_cog import AccountCog
from friendex.adapters.discord_bot.cogs.admin_cog import AdminCog
from friendex.adapters.discord_bot.cogs.daily_cog import DailyCog
from friendex.adapters.discord_bot.cogs.fund_cog import FundCog, FundGroup
from friendex.adapters.discord_bot.cogs.portfolio_cog import PortfolioCog
from friendex.adapters.discord_bot.cogs.stats_cog import StatsCog
from friendex.adapters.discord_bot.cogs.trading_cog import TradingCog
from friendex.adapters.discord_bot.listeners.lifecycle_listener import (
    LifecycleListener,
)
from friendex.adapters.discord_bot.listeners.member_listener import MemberListener
from friendex.adapters.discord_bot.listeners.message_listener import MessageListener
from friendex.adapters.discord_bot.listeners.reaction_listener import ReactionListener
from friendex.adapters.discord_bot.listeners.voice_listener import VoiceListener
from friendex.adapters.tasks.activity_tick_task import ActivityTickTask
from friendex.adapters.tasks.daily_reset_task import DailyResetTask
from friendex.adapters.tasks.freeze_check_task import FreezeCheckTask
from friendex.adapters.tasks.inactivity_decay_task import InactivityDecayTask
from friendex.adapters.tasks.liquidation_task import LiquidationTask
from friendex.adapters.tasks.monthly_rollover_task import MonthlyRolloverTask
from friendex.adapters.tasks.vc_boost_task import VcBoostTask
from friendex.adapters.tasks.weekly_reset_task import WeeklyResetTask
from friendex.domain.errors import PersistenceError
from tests.simulation.harness import stubs

if TYPE_CHECKING:
    from friendex.adapters.tasks.base_task import BackgroundTask
    from tests.simulation.harness.schema import Action
    from tests.simulation.harness.world import SimWorld

_C = TypeVar("_C")

_TASK_CLASSES: dict[str, type[BackgroundTask]] = {
    "activity_tick": ActivityTickTask,
    "daily_reset": DailyResetTask,
    "freeze_check": FreezeCheckTask,
    "inactivity_decay": InactivityDecayTask,
    "liquidation": LiquidationTask,
    "monthly_rollover": MonthlyRolloverTask,
    "vc_boost": VcBoostTask,
    "weekly_reset": WeeklyResetTask,
}


@dataclass(frozen=True, kw_only=True)
class ActionOutcome:
    """What one executed action produced, for the expectation engine."""

    error: BaseException | None = None
    interaction: MagicMock | None = None
    liquidations_before: int = 0


def _cog_of(world: SimWorld, cog_type: type[_C]) -> _C:
    for cog in world.container.cogs:
        if isinstance(cog, cog_type):
            return cog
    raise AssertionError(f"{cog_type.__name__} not in container.cogs")


def _listener_of(world: SimWorld, listener_type: type[_C]) -> _C:
    for listener in world.container.listeners:
        if isinstance(listener, listener_type):
            return listener
    raise AssertionError(f"{listener_type.__name__} not in container.listeners")


async def execute(world: SimWorld, action: Action) -> ActionOutcome:
    """Execute one timeline action and capture its outcome."""
    liquidations_before = len(world.liquidation_events)
    if action.kind == "command":
        outcome = await _execute_command(world, action)
    elif action.kind == "event":
        outcome = await _execute_event(world, action)
    else:
        outcome = await _execute_task(world, action)
    return ActionOutcome(
        error=outcome.error,
        interaction=outcome.interaction,
        liquidations_before=liquidations_before,
    )


# ---------------------------------------------------------------------------
# Commands


async def _execute_command(world: SimWorld, action: Action) -> ActionOutcome:
    assert action.actor is not None  # enforced by the schema
    member = world.member(action.actor)
    interaction = world.make_interaction(action.actor)

    if action.name == "game_intro" and not member.guild_permissions.manage_guild:
        check_error = app_commands.MissingPermissions(["manage_guild"])
        await world.on_tree_error(interaction, check_error)
        return ActionOutcome(error=check_error, interaction=interaction)

    try:
        await _invoke_command(world, action, interaction)
    except Exception as exc:
        wrapped = app_commands.CommandInvokeError(MagicMock(name="Command"), exc)
        await world.on_tree_error(interaction, wrapped)
        return ActionOutcome(error=exc, interaction=interaction)
    return ActionOutcome(error=None, interaction=interaction)


async def _invoke_command(
    world: SimWorld,
    action: Action,
    interaction: MagicMock,
) -> None:
    args = action.args
    name = action.name

    def target() -> MagicMock:
        return world.member(str(args["user"]))

    def optional_target() -> MagicMock | None:
        return world.member(str(args["user"])) if "user" in args else None

    if name == "balance":
        await AccountCog.balance.callback(_cog_of(world, AccountCog), interaction)
    elif name == "optin":
        await AccountCog.optin.callback(_cog_of(world, AccountCog), interaction)
    elif name == "optout":
        await AccountCog.optout.callback(_cog_of(world, AccountCog), interaction)
    elif name == "daily":
        await DailyCog.daily.callback(_cog_of(world, DailyCog), interaction)
    elif name in {"buy", "sell", "short", "cover"}:
        trading = _cog_of(world, TradingCog)
        callback = getattr(TradingCog, name).callback
        await callback(trading, interaction, user=target(), shares=int(args["shares"]))
    elif name == "portfolio":
        await PortfolioCog.portfolio.callback(
            _cog_of(world, PortfolioCog), interaction, user=optional_target()
        )
    elif name == "trending":
        await StatsCog.trending.callback(_cog_of(world, StatsCog), interaction)
    elif name == "mystats":
        await StatsCog.mystats.callback(_cog_of(world, StatsCog), interaction)
    elif name == "price":
        await StatsCog.price.callback(
            _cog_of(world, StatsCog), interaction, user=target()
        )
    elif name == "mystock":
        await StatsCog.mystock.callback(_cog_of(world, StatsCog), interaction)
    elif name == "help":
        await AdminCog.help.callback(_cog_of(world, AdminCog), interaction)
    elif name == "game_intro":
        await AdminCog.game_intro.callback(_cog_of(world, AdminCog), interaction)
    elif name.startswith("fund_"):
        group = _cog_of(world, FundCog).group
        await _invoke_fund(group, name, args, interaction, world)
    else:  # pragma: no cover — schema validation rejects unknown names
        raise AssertionError(f"unhandled command {name!r}")


async def _invoke_fund(
    group: FundGroup,
    name: str,
    args: dict[str, object],
    interaction: MagicMock,
    world: SimWorld,
) -> None:
    if name == "fund_create":
        fund_name = str(args["name"]) if "name" in args else None
        await FundGroup.create.callback(group, interaction, name=fund_name)
    elif name == "fund_info":
        user = world.member(str(args["user"])) if "user" in args else None
        await FundGroup.info.callback(group, interaction, user=user)
    elif name == "fund_withdraw":
        await FundGroup.withdraw.callback(
            group, interaction, amount=float(str(args["amount"]))
        )
    elif name == "fund_send_events":
        await FundGroup.send_events.callback(
            group, interaction, amount=float(str(args["amount"]))
        )
    elif name == "fund_invest":
        await FundGroup.invest.callback(
            group,
            interaction,
            user=world.member(str(args["user"])),
            amount=float(str(args["amount"])),
        )
    else:  # pragma: no cover
        raise AssertionError(f"unhandled fund command {name!r}")


# ---------------------------------------------------------------------------
# Events


async def _execute_event(world: SimWorld, action: Action) -> ActionOutcome:
    name = action.name
    try:
        if name == "message":
            await _event_message(world, action)
        elif name == "reaction":
            await _event_reaction(world, action)
        elif name in {"voice_join", "voice_leave", "voice_switch"}:
            await _event_voice(world, action)
        elif name == "member_timeout":
            await _event_member_timeout(world, action)
        elif name == "member_ban":
            listener = _listener_of(world, MemberListener)
            await listener.on_member_ban(
                world.guild, world.member(str(action.args["target"]))
            )
        elif name == "guild_remove":
            lifecycle = LifecycleListener(
                on_guild_remove=world.container.on_guild_remove
            )
            await lifecycle.on_guild_remove(world.guild)
        elif name in {"raise_unexpected", "raise_persistence"}:
            return await _event_synthetic_error(world, action)
        else:  # pragma: no cover
            raise AssertionError(f"unhandled event {name!r}")
    except Exception as exc:
        return ActionOutcome(error=exc)
    return ActionOutcome(error=None)


async def _event_message(world: SimWorld, action: Action) -> None:
    args = action.args
    author_name = str(args["author"])
    author = world.member(author_name)

    author_is_bot = bool(args.get("author_bot", False))
    if author_is_bot:
        author = MagicMock(name="BotAuthor")
        author.bot = True
        author.id = 999_000_001

    voice_channel = args.get("voice_channel", world.voice_channel_of.get(author_name))
    if voice_channel is not None:
        author.voice = stubs.make_voice_state(int(str(voice_channel)))
    else:
        author.voice = None

    role_mentions: tuple[MagicMock, ...] = ()
    if "mention_role" in args:
        role_member_names = args.get("role_members", [])
        assert isinstance(role_member_names, list)
        role_members = tuple(world.member(str(n)) for n in role_member_names)
        role_mentions = (stubs.make_role(int(str(args["mention_role"])), role_members),)

    message = stubs.make_message(
        author=author,
        guild=world.guild,
        channel_id=int(str(args.get("channel", 100))),
        message_id=world.next_message_id(),
        has_attachment=bool(args.get("attachment", False)),
        is_reply=bool(args.get("reply", False)),
        role_mentions=role_mentions,
    )
    world.last_message_id = message.id
    listener = _listener_of(world, MessageListener)
    await listener.on_message(message)


async def _event_reaction(world: SimWorld, action: Action) -> None:
    args = action.args
    reactor = world.member(str(args["reactor"]))
    if bool(args.get("message_author_bot", False)):
        message_author = MagicMock(name="BotAuthor")
        message_author.bot = True
        message_author.id = 999_000_002
    else:
        message_author = world.member(str(args["message_author"]))
    message = stubs.make_message(
        author=message_author,
        guild=world.guild,
        channel_id=int(str(args.get("channel", 100))),
        message_id=world.next_message_id(),
    )
    listener = _listener_of(world, ReactionListener)
    await listener.on_reaction_add(stubs.make_reaction(message=message), reactor)


async def _event_voice(world: SimWorld, action: Action) -> None:
    args = action.args
    user_name = str(args["user"])
    member = world.member(user_name)
    listener = _listener_of(world, VoiceListener)

    if action.name == "voice_join":
        before = stubs.make_voice_state(None)
        after = stubs.make_voice_state(int(str(args["channel"])))
        world.voice_channel_of[user_name] = int(str(args["channel"]))
    elif action.name == "voice_leave":
        before = stubs.make_voice_state(int(str(args["channel"])))
        after = stubs.make_voice_state(None)
        world.voice_channel_of.pop(user_name, None)
    else:  # voice_switch
        before = stubs.make_voice_state(int(str(args["from"])))
        after = stubs.make_voice_state(int(str(args["to"])))
        world.voice_channel_of[user_name] = int(str(args["to"]))

    await listener.on_voice_state_update(member, before, after)


async def _event_member_timeout(world: SimWorld, action: Action) -> None:
    from datetime import UTC, datetime, timedelta

    member = world.member(str(action.args["target"]))
    before = MagicMock(name="MemberBefore")
    before.timed_out_until = None
    after = MagicMock(name="MemberAfter")
    after.id = member.id
    after.bot = False
    after.guild = world.guild
    after.timed_out_until = datetime.now(tz=UTC) + timedelta(hours=1)
    listener = _listener_of(world, MemberListener)
    await listener.on_member_update(before, after)


async def _event_synthetic_error(world: SimWorld, action: Action) -> ActionOutcome:
    """Route a synthetic error through the central handler.

    No organic user action reaches the handler's PersistenceError or
    CRITICAL "Unexpected error" branches (DomainError covers every reachable
    game-rule violation), so the simulation injects them directly — the
    assertion surface is the *handler's rendering*, which is production code.
    """
    actor = action.actor or next(iter(world.scenario.users))
    interaction = world.make_interaction(actor)
    inner: Exception
    if action.name == "raise_persistence":
        inner = PersistenceError(operation="simulated_op", detail="simulated failure")
    else:
        inner = ValueError("simulated unexpected failure")
    wrapped = app_commands.CommandInvokeError(MagicMock(name="Command"), inner)
    await world.on_tree_error(interaction, wrapped)
    return ActionOutcome(error=inner, interaction=interaction)


# ---------------------------------------------------------------------------
# Tasks


async def _execute_task(world: SimWorld, action: Action) -> ActionOutcome:
    task_class = _TASK_CLASSES[action.name]
    task = next(t for t in world.container.raw_tasks if isinstance(t, task_class))
    try:
        await task._run()
    except Exception as exc:
        return ActionOutcome(error=exc)
    return ActionOutcome(error=None)
