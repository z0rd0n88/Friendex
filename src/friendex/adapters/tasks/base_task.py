"""Abstract base for background tasks (Phase 9).

:class:`BackgroundTask` is the thin abstraction every Phase 9 task wrapper
inherits from. It captures **one** load-bearing contract that the application
services never make: any exception raised by a tick's work coroutine is
swallowed and logged, so a transient service-layer failure cannot cancel the
loop that drives the cadence.

**Cadence is declared, not enforced here.** Each concrete task exposes its
desired cadence as the ``interval_minutes`` (or ``interval_hours``) class
attribute; the Phase-14 composition layer wraps the task's :meth:`_run` body
in a ``discord.ext.tasks.loop(...)`` of the appropriate length and binds the
resulting ``Loop`` to :attr:`_loop`. Keeping the wrapper out of the task
module lets the liquidation task (per Phase 9 AC3) avoid importing the
``discord`` package altogether — only the composition layer touches discord.

**Lifecycle.** :meth:`start` and :meth:`stop` operate on :attr:`_loop` once
the composition layer has bound it. Calling either before binding raises
:class:`AttributeError` — the task is not runnable on its own.

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
    from collections.abc import Awaitable


_log = structlog.get_logger(__name__)


class BackgroundTask(ABC):
    """Abstract base for every Phase 9 background task wrapper.

    Subclasses implement :meth:`_run` (the per-tick body) and declare their
    desired cadence on either :attr:`interval_minutes` or
    :attr:`interval_hours`. The composition layer (Phase 14) reads the
    cadence and wraps :meth:`_run` in a ``discord.ext.tasks.loop``.
    """

    #: Cadence in minutes. Subclasses override exactly one of
    #: :attr:`interval_minutes` / :attr:`interval_hours` (the other stays 0).
    interval_minutes: int = 0
    #: Cadence in hours. See :attr:`interval_minutes`.
    interval_hours: int = 0

    _loop: Any  # bound by the composition layer; see module docstring.

    @abstractmethod
    async def _run(self) -> None:
        """Per-tick body — subclasses implement."""

    def start(self) -> None:
        """Start the underlying ``tasks.loop``.

        The composition layer must bind :attr:`_loop` first; calling this
        method before then raises :class:`AttributeError`. Idempotent on an
        already-running loop.
        """
        if not self._loop.is_running():
            self._loop.start()

    def stop(self) -> None:
        """Cancel the underlying ``tasks.loop``.

        Signals the scheduler to stop after the current iteration. Safe to
        call when the loop is not running.
        """
        if self._loop.is_running():
            self._loop.cancel()

    async def _safe_run(self, awaitable: Awaitable[Any]) -> None:
        """Await ``awaitable`` and swallow + log any :class:`Exception`.

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
            _log.error(
                "background_task_iteration_failed",
                task=type(self).__name__,
                error=str(exc),
                error_type=type(exc).__name__,
            )
