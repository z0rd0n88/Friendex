"""``TaskRunner`` — discord lifecycle wrapper for a :class:`BackgroundTask`.

Owns the ``discord.ext.tasks.Loop`` for one task. Constructed once; valid
from construction — no post-init binding step needed.

This is the **only** module in ``adapters/tasks/`` that imports ``discord``.
Every concrete task class stays discord-free so tests can exercise tick logic
without a discord event loop.
"""

from __future__ import annotations

from discord.ext import tasks as discord_tasks

from friendex.adapters.tasks.base_task import BackgroundTask  # noqa: TC001


class TaskRunner:
    """Wraps a :class:`BackgroundTask` with its ``discord.ext.tasks.Loop``.

    The loop is built from the task's declared :attr:`~BackgroundTask.interval_minutes`
    / :attr:`~BackgroundTask.interval_hours` at construction time. Calling
    :meth:`start` or :meth:`stop` is safe immediately — there is no dead zone.

    Raises
    ------
    ValueError
        If both ``task.interval_minutes`` and ``task.interval_hours`` are zero.
    """

    def __init__(self, task: BackgroundTask) -> None:
        if task.interval_minutes == 0 and task.interval_hours == 0:
            raise ValueError(
                f"{type(task).__name__}: at least one of interval_minutes or"
                " interval_hours must be non-zero"
            )
        self._task = task
        loop_decorator = discord_tasks.loop(
            minutes=task.interval_minutes,
            hours=task.interval_hours,
        )
        self._loop = loop_decorator(self._tick)

    async def _tick(self) -> None:
        """Delegate one loop iteration to the wrapped task via ``_safe_run``.

        ``_safe_run`` is the single error boundary for the loop — subclass
        ``_run`` implementations raise normally; this layer swallows and logs.
        """
        await self._task._safe_run(self._task._run())

    def start(self) -> None:
        """Start the underlying loop. Idempotent if already running."""
        if not self._loop.is_running():
            self._loop.start()

    def stop(self) -> None:
        """Cancel the underlying loop. Safe if not running."""
        if self._loop.is_running():
            self._loop.cancel()
