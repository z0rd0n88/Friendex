"""Composition root for Friendex — the single DI :class:`Container` (Phase 13).

This module wires the whole hexagonal graph together in one place:

1. Construct every SQL repository over the shared ``async_sessionmaker``.
2. Construct one shared :class:`~friendex.application.lock_manager.LockManager`
   (Phase 7 contract: process-local singleton; **never** per-call).
3. Expose **per-guild service factories** (``Callable[[str], TService]``) for
   every application service whose ctor takes ``guild_id`` (Phase 8/9 cog +
   listener convention). Each factory closes over the shared repos +
   LockManager + sessionmaker and produces a fresh service when called with
   a string ``guild_id``.
4. Construct **single-instance** background tasks for all 8 Phase-9 tasks.
   Tasks are **not started** here — Phase 14's composition layer binds
   ``_loop`` and calls :meth:`start` after the event loop is running.
5. Construct all 7 cogs and all 4 listeners with the appropriate factories.
6. Expose :meth:`register_with` to add every cog/listener to a passed
   :class:`discord.ext.commands.Bot` and install the central error handler
   on ``bot.tree.on_error``.

**Volatile per-guild stores.** The Phase 12 voice listener takes a
``voice_session_store_factory: Callable[[str], VoiceSessionStore]``. The
ActivityService and VoicePingService each take one
:class:`VoiceSessionStore` / :class:`VoicePingSessionStore` instance per
guild — so the container owns one ``dict[guild_id, store]`` per kind and
lazily constructs the per-guild store on first request, returning the same
instance on subsequent calls (mirroring the original bot's volatile dicts).

**LiquidationService composition.** ``LiquidationService(*, trading_service, ...)``
depends on a :class:`TradingService` for the **same** ``guild_id`` (per
Phase 8f's design (a): the private ``_cover_internal(force=True)`` is called
on the trading service whose lock the liquidation already holds). The
factory therefore builds the trading service first and threads it into the
liquidation service.

**Notifier and ``iter_guild_ids``.** The Phase-9 :class:`LiquidationTask`
needs a notifier ``Callable[[LiquidationEvent], Awaitable[None]]`` and
every task needs ``Callable[[], Awaitable[Iterable[str]]]`` to walk the
live guild set. Both are wired by :meth:`build_runners` at startup:
:func:`_make_liquidation_notifier` produces the real Discord-embed
dispatcher (closed over the bot) and the real ``iter_guild_ids`` walks
``bot.guilds`` on every tick. Construction-time defaults are unused
no-ops — :meth:`build_runners` MUST be called before any task starts,
and it rebinds both seams via :meth:`BackgroundTask.bind_guild_id_provider`
and :meth:`LiquidationTask.bind_notifier`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog

from friendex.adapters.discord_bot.cogs.account_cog import AccountCog
from friendex.adapters.discord_bot.cogs.admin_cog import AdminCog
from friendex.adapters.discord_bot.cogs.daily_cog import DailyCog
from friendex.adapters.discord_bot.cogs.fund_cog import FundCog
from friendex.adapters.discord_bot.cogs.portfolio_cog import PortfolioCog
from friendex.adapters.discord_bot.cogs.stats_cog import StatsCog
from friendex.adapters.discord_bot.cogs.trading_cog import TradingCog
from friendex.adapters.discord_bot.embeds import (
    build_liquidation_notification_embed,
)
from friendex.adapters.discord_bot.error_handler import register_error_handler
from friendex.adapters.discord_bot.listeners.lifecycle_listener import (
    LifecycleListener,
)
from friendex.adapters.discord_bot.listeners.member_listener import MemberListener
from friendex.adapters.discord_bot.listeners.message_listener import MessageListener
from friendex.adapters.discord_bot.listeners.reaction_listener import ReactionListener
from friendex.adapters.discord_bot.listeners.voice_listener import VoiceListener
from friendex.adapters.persistence.cooldown_repo import SqlTradeCooldownRepository
from friendex.adapters.persistence.fund_repo import SqlFundRepository
from friendex.adapters.persistence.penalty_repo import SqlPenaltyRepository
from friendex.adapters.persistence.price_repo import SqlPriceRepository
from friendex.adapters.persistence.system_state_repo import SqlSystemStateRepository
from friendex.adapters.persistence.unit_of_work import SqlUnitOfWork
from friendex.adapters.persistence.user_repo import SqlUserRepository
from friendex.adapters.tasks.activity_tick_task import ActivityTickTask
from friendex.adapters.tasks.daily_reset_task import DailyResetTask
from friendex.adapters.tasks.freeze_check_task import FreezeCheckTask
from friendex.adapters.tasks.inactivity_decay_task import InactivityDecayTask
from friendex.adapters.tasks.liquidation_task import LiquidationTask
from friendex.adapters.tasks.monthly_rollover_task import MonthlyRolloverTask
from friendex.adapters.tasks.vc_boost_task import VcBoostTask
from friendex.adapters.tasks.weekly_reset_task import WeeklyResetTask
from friendex.application.activity_service import ActivityService
from friendex.application.daily_service import DailyService
from friendex.application.discipline_service import DisciplineService
from friendex.application.fund_service import FundService
from friendex.application.liquidation_service import LiquidationService
from friendex.application.lock_manager import LockManager
from friendex.application.portfolio_service import PortfolioService
from friendex.application.price_tick_service import PriceTickService
from friendex.application.stats_service import StatsService
from friendex.application.trading_service import TradingService
from friendex.application.voice_ping_service import VoicePingService
from friendex.application.voice_session_store import (
    VoicePingSessionStore,
    VoiceSessionStore,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from discord.ext import commands
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from friendex.adapters.config import Settings
    from friendex.adapters.tasks.base_task import BackgroundTask
    from friendex.adapters.tasks.task_runner import TaskRunner
    from friendex.application.liquidation_events import LiquidationEvent


logger = structlog.get_logger(__name__)


def _make_liquidation_notifier(
    bot: commands.Bot,
) -> Callable[[LiquidationEvent], Awaitable[None]]:
    """Build the real :class:`LiquidationTask` notifier (Phase 14).

    Closes over ``bot`` so the per-event coroutine can resolve the target
    guild via :meth:`commands.Bot.get_guild` and dispatch the embed to its
    ``system_channel``. A missing guild (``None`` return) or missing
    ``system_channel`` is logged at WARNING and the event is dropped — both
    states are operationally unusual but recoverable, so we never raise.

    Every send passes :meth:`discord.AllowedMentions.none` per the
    project-wide reply-hardening rule (Phase 13 digest item 5).
    """

    async def notify(event: LiquidationEvent) -> None:
        guild = bot.get_guild(int(event.guild_id))
        if guild is None:
            logger.warning(
                "liquidation_notifier_guild_missing",
                guild_id=event.guild_id,
            )
            return
        channel = guild.system_channel
        if channel is None:
            logger.warning(
                "liquidation_notifier_system_channel_missing",
                guild_id=event.guild_id,
            )
            return
        await channel.send(
            embed=build_liquidation_notification_embed(event),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    return notify


class Container:
    """Process-wide composition root.

    Parameters
    ----------
    settings :
        The validated :class:`~friendex.adapters.config.Settings`.
    sessionmaker :
        An async sessionmaker bound to the engine the container will share
        across every repo.
    """

    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._runners_built: bool = False

        # --- Repositories -------------------------------------------------
        self._user_repo = SqlUserRepository(sessionmaker)
        self._fund_repo = SqlFundRepository(sessionmaker)
        self._price_repo = SqlPriceRepository(sessionmaker)
        self._cooldown_repo = SqlTradeCooldownRepository(sessionmaker)
        self._penalty_repo = SqlPenaltyRepository(sessionmaker)
        self._system_state_repo = SqlSystemStateRepository(sessionmaker)

        # --- Unit of work (atomicity envelope) ---------------------------
        # One ``SqlUnitOfWork`` shared across every per-guild factory: the
        # adapter is stateless other than its sessionmaker reference, and
        # repositories pick up the in-flight session via ``contextvars``
        # rather than holding it directly, so the same instance is safe
        # to thread into every TradingService / FundService that the
        # factories build.
        self._unit_of_work = SqlUnitOfWork(sessionmaker)

        # --- Concurrency primitives --------------------------------------
        self._lock_manager = LockManager()

        # --- Volatile per-guild stores (lazy, per Phase 8a/12b contract).
        self._voice_sessions: dict[str, VoiceSessionStore] = {}
        self._ping_sessions: dict[str, VoicePingSessionStore] = {}

        # --- Per-guild service factories ---------------------------------
        # Each factory closes over the shared repos + LockManager + per-guild
        # store dict, returning a fresh service per call (mirrors Phase 9
        # service_factory convention; cogs/listeners must not cache).
        self.activity_service_factory = self._make_activity_factory()
        self.voice_ping_service_factory = self._make_voice_ping_factory()
        self.price_tick_service_factory = self._make_price_tick_factory()
        self.trading_service_factory = self._make_trading_factory()
        self.portfolio_service_factory = self._make_portfolio_factory()
        self.stats_service_factory = self._make_stats_factory()
        self.fund_service_factory = self._make_fund_factory()
        self.daily_service_factory = self._make_daily_factory()
        self.liquidation_service_factory = self._make_liquidation_factory()
        self.discipline_service_factory = self._make_discipline_factory()
        self.voice_session_store_factory = self._voice_session_store_for

        # --- Tasks (single-instance; wrapped in TaskRunner by build_runners) --
        # Constructed with unused construction-time defaults; build_runners(bot)
        # rebinds the real ``iter_guild_ids`` closure via
        # ``BackgroundTask.bind_guild_id_provider`` (and the real
        # :class:`LiquidationTask` notifier via ``bind_notifier``) before any
        # task starts. The inline defaults below are unreachable in production
        # (build_runners is mandatory) and only satisfy the typed kwargs.
        async def _unset_guild_ids() -> Iterable[str]:
            return ()

        async def _unset_notifier(_event: LiquidationEvent) -> None:
            return None

        self._vc_boost_task = VcBoostTask(
            service_factory=self.price_tick_service_factory,
            iter_guild_ids=_unset_guild_ids,
        )
        # Stored reference so build_runners can inject the live notifier
        # without scanning raw_tasks by isinstance.
        self._liquidation_task = LiquidationTask(
            service_factory=self.liquidation_service_factory,
            iter_guild_ids=_unset_guild_ids,
            notifier=_unset_notifier,
        )
        self.raw_tasks: tuple[BackgroundTask, ...] = (
            ActivityTickTask(
                service_factory=self.price_tick_service_factory,
                iter_guild_ids=_unset_guild_ids,
            ),
            DailyResetTask(
                service_factory=self.activity_service_factory,
                iter_guild_ids=_unset_guild_ids,
                system_state_repo=self._system_state_repo,
            ),
            FreezeCheckTask(
                service_factory=self.trading_service_factory,
                iter_guild_ids=_unset_guild_ids,
            ),
            InactivityDecayTask(
                service_factory=self.price_tick_service_factory,
                iter_guild_ids=_unset_guild_ids,
            ),
            self._liquidation_task,
            MonthlyRolloverTask(
                portfolio_service_factory=self.portfolio_service_factory,
                fund_service_factory=self.fund_service_factory,
                iter_guild_ids=_unset_guild_ids,
                system_state_repo=self._system_state_repo,
            ),
            self._vc_boost_task,
            WeeklyResetTask(
                service_factory=self.activity_service_factory,
                iter_guild_ids=_unset_guild_ids,
                system_state_repo=self._system_state_repo,
            ),
        )

        # --- Cogs ---------------------------------------------------------
        self.cogs: tuple[commands.Cog, ...] = (
            AccountCog(
                portfolio_service_factory=self.portfolio_service_factory,
                activity_service_factory=self.activity_service_factory,
            ),
            AdminCog(),
            DailyCog(daily_service_factory=self.daily_service_factory),
            FundCog(
                fund_service_factory=self.fund_service_factory,
            ),
            PortfolioCog(portfolio_service_factory=self.portfolio_service_factory),
            StatsCog(stats_service_factory=self.stats_service_factory),
            TradingCog(trading_service_factory=self.trading_service_factory),
        )

        # --- Listeners ----------------------------------------------------
        self.listeners: tuple[commands.Cog, ...] = (
            MessageListener(
                activity_service_factory=self.activity_service_factory,
                voice_ping_service_factory=self.voice_ping_service_factory,
                settings=self._settings,
            ),
            VoiceListener(
                activity_service_factory=self.activity_service_factory,
                voice_ping_service_factory=self.voice_ping_service_factory,
                voice_session_store_factory=self.voice_session_store_factory,
                vc_boost_task=self._vc_boost_task,
            ),
            ReactionListener(activity_service_factory=self.activity_service_factory),
            MemberListener(discipline_service_factory=self.discipline_service_factory),
        )

    # ------------------------------------------------------------------
    # Per-guild store accessors
    # ------------------------------------------------------------------

    def _voice_session_store_for(self, guild_id: str) -> VoiceSessionStore:
        store = self._voice_sessions.get(guild_id)
        if store is None:
            store = VoiceSessionStore()
            self._voice_sessions[guild_id] = store
        return store

    def _ping_session_store_for(self, guild_id: str) -> VoicePingSessionStore:
        store = self._ping_sessions.get(guild_id)
        if store is None:
            store = VoicePingSessionStore()
            self._ping_sessions[guild_id] = store
        return store

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        """Evict the departing guild's volatile per-guild stores.

        Wave 1 (#82 M2): the container lazily seeds ``_voice_sessions`` and
        ``_ping_sessions`` on first access — but never garbage-collects
        them. A bot left running across thousands of guild adds/removes
        (e.g. brief Lyra-style test deployments) would accumulate dead
        entries forever. Popping on ``on_guild_remove`` keeps the maps
        bounded by the live guild set.

        Using ``dict.pop(key, default)`` makes the eviction idempotent — a
        guild that was never seeded (e.g. removed before any voice
        activity ran) is a quiet no-op rather than a ``KeyError``.
        """
        guild_id = str(guild.id)
        self._voice_sessions.pop(guild_id, None)
        self._ping_sessions.pop(guild_id, None)

    # ------------------------------------------------------------------
    # Factory builders — each closes over ``self`` and returns a callable
    # ------------------------------------------------------------------

    def _make_activity_factory(self) -> Callable[[str], ActivityService]:
        def factory(guild_id: str) -> ActivityService:
            return ActivityService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
                voice_sessions=self._voice_session_store_for(guild_id),
            )

        return factory

    def _make_voice_ping_factory(self) -> Callable[[str], VoicePingService]:
        def factory(guild_id: str) -> VoicePingService:
            return VoicePingService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
                ping_sessions=self._ping_session_store_for(guild_id),
            )

        return factory

    def _make_price_tick_factory(self) -> Callable[[str], PriceTickService]:
        def factory(guild_id: str) -> PriceTickService:
            return PriceTickService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
                voice_sessions=self._voice_session_store_for(guild_id),
            )

        return factory

    def _make_trading_factory(self) -> Callable[[str], TradingService]:
        def factory(guild_id: str) -> TradingService:
            return TradingService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                fund_repo=self._fund_repo,
                cooldown_repo=self._cooldown_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
                unit_of_work=self._unit_of_work,
            )

        return factory

    def _make_portfolio_factory(self) -> Callable[[str], PortfolioService]:
        def factory(guild_id: str) -> PortfolioService:
            return PortfolioService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                fund_repo=self._fund_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
            )

        return factory

    def _make_stats_factory(self) -> Callable[[str], StatsService]:
        def factory(guild_id: str) -> StatsService:
            return StatsService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                settings=self._settings,
            )

        return factory

    def _make_fund_factory(self) -> Callable[[str], FundService]:
        def factory(guild_id: str) -> FundService:
            return FundService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                fund_repo=self._fund_repo,
                penalty_repo=self._penalty_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
                unit_of_work=self._unit_of_work,
            )

        return factory

    def _make_daily_factory(self) -> Callable[[str], DailyService]:
        def factory(guild_id: str) -> DailyService:
            return DailyService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
            )

        return factory

    def _make_liquidation_factory(self) -> Callable[[str], LiquidationService]:
        # Liquidation needs a per-guild TradingService instance (Phase 8f
        # design (a)): the trading service exposes ``_cover_internal``
        # which the liquidation service calls while holding the lock.
        trading_factory = self.trading_service_factory

        def factory(guild_id: str) -> LiquidationService:
            return LiquidationService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                fund_repo=self._fund_repo,
                cooldown_repo=self._cooldown_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
                trading_service=trading_factory(guild_id),
                unit_of_work=self._unit_of_work,
            )

        return factory

    def _make_discipline_factory(self) -> Callable[[str], DisciplineService]:
        def factory(guild_id: str) -> DisciplineService:
            return DisciplineService(
                guild_id=guild_id,
                user_repo=self._user_repo,
                price_repo=self._price_repo,
                lock_manager=self._lock_manager,
                settings=self._settings,
            )

        return factory

    # ------------------------------------------------------------------
    # Bot integration
    # ------------------------------------------------------------------

    def build_runners(self, bot: commands.Bot) -> tuple[TaskRunner, ...]:
        """Inject live bot callables and wrap each task in a :class:`TaskRunner`.

        **One-shot contract**: must be called exactly once, from ``setup_hook``.
        A second call raises :class:`RuntimeError` — calling it again would
        create orphan runners sharing the same task instances.

        Steps:

        1. Replace each task's ``_iter_guild_ids`` with a closure that walks
           ``bot.guilds`` on every tick (so newly-added guilds participate in
           the next sweep without a restart).
        2. Replace :class:`LiquidationTask`'s notifier with the real Discord
           embed dispatcher.
        3. Wrap each task in a :class:`TaskRunner`, which builds its
           ``discord.ext.tasks.Loop`` from the task's declared cadence.

        Returns a tuple of ready-to-start runners — each valid immediately.
        """
        if self._runners_built:
            raise RuntimeError("build_runners() must only be called once")
        self._runners_built = True

        from friendex.adapters.tasks.task_runner import TaskRunner

        async def iter_guild_ids() -> Iterable[str]:
            # Generator expression rather than a materialised list — each task
            # tick only iterates the result once, so allocating a fresh list on
            # every cadence is wasted work (8 tasks times ticks-per-minute on
            # big bots adds up). Issue #84 L (dead-code sweep).
            return (str(g.id) for g in bot.guilds)

        # Wave 1 (#82 H15 / #84 H): the public ``bind_guild_id_provider`` and
        # ``bind_notifier`` setters replace direct attribute mutation on the
        # task instances. The task classes declare these as typed seams so
        # mypy can follow the injection without ``# type: ignore``.
        for task in self.raw_tasks:
            task.bind_guild_id_provider(iter_guild_ids)
        self._liquidation_task.bind_notifier(_make_liquidation_notifier(bot))

        return tuple(TaskRunner(task) for task in self.raw_tasks)

    async def register_with(self, bot: commands.Bot) -> None:
        """Attach every cog + listener to ``bot`` and install the error handler.

        :meth:`commands.Bot.add_cog` is the sanctioned discord.py entry point;
        it dispatches both ``app_commands`` (slash commands defined on the
        cog) and ``commands.Cog.listener()`` decorators. Listeners are added
        via the same call — they are :class:`commands.Cog` subclasses by
        Phase-12 convention.

        Wave 1 (#82 M2 + review LOW-3): a thin :class:`LifecycleListener`
        is also added so the bot's ``on_guild_remove`` event evicts the
        departing guild's volatile per-guild stores from the container's
        maps. The listener lives under
        ``adapters/discord_bot/listeners/lifecycle_listener.py`` — keeping
        the bridge in its own Cog (rather than smuggling it onto an
        existing listener) preserves the Phase 12 listener taxonomy. The
        listener takes the container's cleanup method as a bare callback
        so the listener module never imports :class:`Container` (the
        composition root imports listeners, not the reverse).
        """
        for cog in self.cogs:
            await bot.add_cog(cog)
        for listener in self.listeners:
            await bot.add_cog(listener)
        await bot.add_cog(LifecycleListener(on_guild_remove=self.on_guild_remove))
        register_error_handler(bot, self._settings)
