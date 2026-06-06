"""Abstract base for background tasks (Phase 9).

:class:`BackgroundTask` is the thin abstraction every Phase 9 task wrapper
inherits from. It captures **one** load-bearing contract that the application
services never make: any exception raised by a tick is swallowed and logged,
so a transient service-layer failure cannot cancel the loop that drives the
cadence.

**Where ``_safe_run`` lives.** The error boundary is enforced by
:class:`~friendex.adapters.tasks.task_runner.TaskRunner`, which calls
``await self._task._safe_run(self._task._run())`` on every tick. Concrete
:meth:`_run` implementations raise normally — they do not call ``_safe_run``
themselves for the outermost wrap. Tasks that need per-operation failure
isolation within a single tick (e.g. per-guild fan-out, independent service
calls) may still call ``_safe_run`` on those inner coroutines directly.

**Cadence is declared, not enforced here.** Each concrete task exposes its
desired cadence as the ``interval_minutes`` (or ``interval_hours``) class
attribute; :class:`~friendex.adapters.tasks.task_runner.TaskRunner` reads the
cadence at construction time and wraps the task in a
``discord.ext.tasks.loop``. Keeping the discord import out of this module lets
the liquidation task (Phase 9 AC3) remain discord-free — only
:class:`~friendex.adapters.tasks.task_runner.TaskRunner` touches discord.

**Lifecycle.** :class:`~friendex.adapters.tasks.task_runner.TaskRunner` owns
``start()`` and ``stop()``. A bare :class:`BackgroundTask` has no lifecycle
methods — it is always a pure-logic object, valid from construction.

**Design notes**

* The base is :class:`abc.ABC` so accidental direct instantiation fails fast.
* :meth:`_safe_run` accepts a coroutine (not a callable returning one) so the
  call site reads ``await self._safe_run(service.method(...))`` — the closure
  is built at the call site where the bound arguments are visible.
* The catch is :class:`Exception`, not :class:`BaseException` — keyboard
  interrupts, system exits, and cancellation propagate normally so the host
  process can still shut down cleanly. Service layers raise
  :class:`~friendex.domain.errors.DomainError` subclasses and stdlib
  exceptions; both inherit from :class:`Exception` and are caught here.
* Logging uses :mod:`structlog`: the task class name and exception type are
  bound on the log record so a single sink can correlate the failure with
  its source loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Iterable


_log = structlog.get_logger(__name__)


class BackgroundTask(ABC):
    """Abstract base for every Phase 9 background task wrapper.

    Subclasses implement :meth:`_run` (the per-tick body) and declare their
    desired cadence on either :attr:`interval_minutes` or
    :attr:`interval_hours`. :class:`~friendex.adapters.tasks.task_runner.TaskRunner`
    reads the cadence and wraps the tick in a ``discord.ext.tasks.loop``.
    """

    #: Cadence in minutes. Subclasses override exactly one of
    #: :attr:`interval_minutes` / :attr:`interval_hours` (the other stays 0).
    interval_minutes: int = 0
    #: Cadence in hours. See :attr:`interval_minutes`.
    interval_hours: int = 0

    #: Injected by :meth:`~friendex.adapters.container.Container.build_runners`
    #: before the first tick. Declared here so the attribute exists on the type
    #: and external assignment does not require ``# type: ignore[attr-defined]``.
    _iter_guild_ids: Callable[[], Awaitable[Iterable[str]]]

    def bind_guild_id_provider(
        self, provider: Callable[[], Awaitable[Iterable[str]]]
    ) -> None:
        """Public seam for installing the live ``iter_guild_ids`` closure.

        Wave 1 (#82 H15 / #84 H): replaces direct
        ``task._iter_guild_ids = fn`` mutation from the container. Keeping
        the assignment behind a method gives the container a typed seam
        the static checker can follow, and gives subclasses a single place
        to override if they ever need to do post-bind work (e.g. cache
        warmup) before the first tick.
        """
        self._iter_guild_ids = provider

    @abstractmethod
    async def _run(self) -> None:
        """Per-tick body — subclasses implement."""

    async def _safe_run(self, awaitable: Awaitable[Any]) -> None:
        """Await ``awaitable`` and swallow + log any :class:`Exception`.

        Called by :class:`~friendex.adapters.tasks.task_runner.TaskRunner`
        around the outermost :meth:`_run` coroutine on every tick. Task helper
        methods may also call it directly when they need per-sub-operation
        failure isolation (e.g. independent service calls within a single tick).

        The contract: this helper NEVER re-raises. A service-layer failure on
        one tick must not cancel the loop that drives the cadence.
        :class:`BaseException` subclasses (:class:`asyncio.CancelledError`,
        :class:`KeyboardInterrupt`, :class:`SystemExit`) are intentionally
        NOT caught so process shutdown and task cancellation still work.

        Accepts any :class:`~collections.abc.Awaitable` (coroutine, task,
        future, custom ``__await__``) so the injected notifier callback in
        :class:`LiquidationTask` is not restricted to returning a
        ``Coroutine`` specifically.
        """
        try:
            await awaitable
        except Exception as exc:
            # ``exc_info=True`` (structlog convention: read ``sys.exc_info()``)
            # captures the full traceback on the log record instead of the
            # bare ``str(exc)`` — operations need the call stack to debug a
            # transient per-tick failure (Wave 1 #84 M).
            _log.error(
                "background_task_iteration_failed",
                task=type(self).__name__,
                error=str(exc),
                error_type=type(exc).__name__,
                exc_info=True,
            )

    async def for_each_guild(
        self,
        coro_factory: Callable[[str], Coroutine[Any, Any, None]],
    ) -> None:
        """Fan out ``coro_factory`` over every guild, isolating each call.

        Encapsulates the canonical per-guild fan-out pattern so task
        ``_run`` bodies do not need to repeat the ``for guild_id … await
        self._safe_run(...)`` boilerplate. Making the isolation wrapper the
        easy default means the unsafe bare-iteration path requires deliberate
        effort (Wave 1 #82 Item 7).

        Each coroutine produced by ``coro_factory(guild_id)`` is awaited
        inside :meth:`_safe_run`, so a failure on one guild is swallowed and
        logged — it does not abort the sweep over the remaining guilds.

        The guild IDs are obtained by calling the bound
        ``_iter_guild_ids`` provider (installed via
        :meth:`bind_guild_id_provider` by the container).

        Args:
            coro_factory: Async callable that accepts a ``guild_id`` string
                and returns a coroutine representing the per-guild work unit.
                The coroutine is constructed at the call site where the bound
                arguments (service, state, etc.) are visible — this matches
                the :meth:`_safe_run` convention of accepting a coroutine
                rather than a callable.
        """
        for guild_id in await self._iter_guild_ids():
            await self._safe_run(coro_factory(guild_id))
