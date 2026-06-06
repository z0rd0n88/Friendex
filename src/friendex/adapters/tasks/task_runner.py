"""``TaskRunner`` — discord lifecycle wrapper for a :class:`BackgroundTask`.

Owns the ``discord.ext.tasks.Loop`` for one task. Constructed once; valid
from construction — no post-init binding step needed.

This is the **only** module in ``adapters/tasks/`` that imports ``discord``.
Every concrete task class stays discord-free so tests can exercise tick logic
without a discord event loop.

**Restart-on-error (Wave 1 #82 M3).** :class:`BackgroundTask` swallows per-tick
exceptions inside ``_safe_run``, so the loop body itself should never raise.
As a defence-in-depth measure, the runner installs an ``error`` callback on
its loop: if anything ever does escape (a bug in the swallow layer, a
callback raising outside ``_tick``, an asyncio system error), the runner
logs the failure with a traceback, sleeps for an exponential backoff
interval (capped at 5 minutes), and calls ``loop.restart()``. The loop
survives a crash instead of going silent until process restart.

**Startup stagger (Wave 1 #82 M4).** Every task is started at almost the same
instant during ``setup_hook`` -- so without staggering, all of them hit the
SQLite database simultaneously on their first tick. A ``before_loop`` hook
sleeps for a small random offset (0-2 s, uniform) before the first iteration
so the cohort spreads out over a couple of seconds without measurable startup
delay.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, cast

import structlog
from discord.ext import tasks as discord_tasks

from friendex.adapters.tasks.base_task import BackgroundTask  # noqa: TC001

_log = structlog.get_logger(__name__)

# Backoff parameters for restart-on-error. The exponent grows by a factor of
# two per consecutive failure, capped at 5 minutes (300 s). A clean tick
# resets the counter to zero (see :meth:`TaskRunner._tick`) so an isolated
# failure months after a pile-up is not still rate-limited at the cap —
# the cap only applies while failures are actually consecutive.
_BACKOFF_BASE_SECONDS: float = 1.0
_BACKOFF_CAP_SECONDS: float = 300.0

# Startup stagger window — small enough to be invisible to operators but big
# enough to spread N tasks across a couple of asyncio event-loop ticks.
_STARTUP_STAGGER_MAX_SECONDS: float = 2.0


class TaskRunner:
    """Wraps a :class:`BackgroundTask` with its ``discord.ext.tasks.Loop``.

    The loop is built from the task's declared :attr:`~BackgroundTask.interval_minutes`
    / :attr:`~BackgroundTask.interval_hours` at construction time. Calling
    :meth:`start` or :meth:`stop` is safe immediately — there is no dead zone.

    Args:
        task: The :class:`BackgroundTask` whose ``_run`` is delegated to on
            every tick.
        stagger_seconds: Upper bound (in seconds) of the uniform random
            startup stagger applied by the ``before_loop`` hook. Defaults to
            :data:`_STARTUP_STAGGER_MAX_SECONDS` (2.0 s). Pass
            ``Settings.task_startup_stagger_seconds`` here so operators can
            tune the window without a code change (Wave 1 #82 M4). Set to
            ``0.0`` in tests for determinism.

    Raises
    ------
    ValueError
        If both ``task.interval_minutes`` and ``task.interval_hours`` are zero.
    """

    def __init__(
        self,
        task: BackgroundTask,
        stagger_seconds: float = _STARTUP_STAGGER_MAX_SECONDS,
    ) -> None:
        if task.interval_minutes == 0 and task.interval_hours == 0:
            raise ValueError(
                f"{type(task).__name__}: at least one of interval_minutes or"
                " interval_hours must be non-zero"
            )
        self._task = task
        self._stagger_seconds = stagger_seconds
        self._consecutive_failures: int = 0
        loop_decorator = discord_tasks.loop(
            minutes=task.interval_minutes,
            hours=task.interval_hours,
        )
        self._loop = loop_decorator(self._tick)
        # Register defence-in-depth restart-on-error and startup-stagger
        # callbacks via ``discord.ext.tasks.Loop``'s built-in ``error`` /
        # ``before_loop`` hooks.
        #
        # ``Loop.error`` is generically typed against a ``CFT`` TypeVar that
        # binds the coroutine type registered at class-construction (i.e. the
        # type of ``_tick``). The runtime contract only requires the supplied
        # callable take ``(exception)`` — but mypy cannot model that the
        # bound method's signature is independent of ``CFT``. A ``cast`` to
        # ``Any`` at the registration site is cleaner than a narrowly-scoped
        # ``# type: ignore[type-var]`` because it makes the intent explicit:
        # we are deliberately registering a callable whose signature mypy
        # cannot match against the generic, and the runtime semantics are
        # well-defined.
        cast("Any", self._loop).error(self._on_loop_error)
        self._loop.before_loop(self._before_first_tick)

    async def _tick(self) -> None:
        """Delegate one loop iteration to the wrapped task via ``_safe_run``.

        ``_safe_run`` is the single error boundary for the loop — subclass
        ``_run`` implementations raise normally; this layer swallows and logs.

        A clean tick resets ``_consecutive_failures`` so an isolated crash
        long after a pile-up is not still rate-limited at the 5-minute cap;
        the cap only applies while failures are *actually* consecutive
        (Wave 1 PR #89 fix-up M-1).
        """
        await self._task._safe_run(self._task._run())
        self._consecutive_failures = 0

    async def _before_first_tick(self) -> None:
        """Sleep for a small random offset before the loop's first iteration.

        Spreads concurrent task cohorts across a couple of seconds so they
        don't all hit SQLite simultaneously on bot startup (Wave 1 #82 M4).

        The stagger window is ``self._stagger_seconds`` (set at construction
        from :attr:`Settings.task_startup_stagger_seconds` by the container,
        or from the ``stagger_seconds`` constructor kwarg for tests). Set it
        to ``0.0`` to disable startup jitter deterministically.
        """
        delay = random.uniform(0.0, self._stagger_seconds)
        await asyncio.sleep(delay)

    async def _on_loop_error(self, exc: BaseException) -> None:
        """Restart the loop with exponential backoff after an unhandled exception.

        Defence-in-depth: ``_safe_run`` should swallow every per-tick
        exception, but if something escapes (e.g. an asyncio system error or
        a bug in the swallow layer), we log with traceback, back off
        exponentially, and call ``loop.restart()`` so the loop survives a
        crash instead of going silent (Wave 1 #82 M3).

        The annotation widens to :class:`BaseException` to match the
        ``ET`` TypeVar in :meth:`discord.ext.tasks.Loop.error`, but in
        practice ``discord.ext.tasks`` only ever dispatches non-cancellation
        exceptions here -- :class:`asyncio.CancelledError` and other
        :class:`BaseException` subclasses propagate out of the loop body
        before reaching this hook.
        """
        self._consecutive_failures += 1
        delay = min(
            _BACKOFF_BASE_SECONDS * (2 ** (self._consecutive_failures - 1)),
            _BACKOFF_CAP_SECONDS,
        )
        _log.error(
            "task_runner_loop_crashed",
            task=type(self._task).__name__,
            error=str(exc),
            error_type=type(exc).__name__,
            consecutive_failures=self._consecutive_failures,
            restart_in_seconds=delay,
            exc_info=True,
        )
        await asyncio.sleep(delay)
        self._loop.restart()

    def start(self) -> None:
        """Start the underlying loop. Idempotent if already running."""
        if not self._loop.is_running():
            self._loop.start()

    def stop(self) -> None:
        """Cancel the underlying loop. Safe if not running."""
        if self._loop.is_running():
            self._loop.cancel()
