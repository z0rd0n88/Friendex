"""Tests for :class:`TaskRunner` lifecycle (start / stop / tick delegation).

:class:`TaskRunner` owns the ``discord.ext.tasks.Loop`` and is always valid
from construction — there is no dead zone between building the runner and
calling ``start()``. These tests verify:

* ``start()`` delegates to the loop when not running (idempotent if running).
* ``stop()`` cancels the loop when running (safe if not running).
* ``_tick`` delegates to the wrapped task's ``_run``.

``_FakeLoop`` is injected by direct attribute assignment after construction so
the real ``discord.ext.tasks.Loop`` (which starts an asyncio task) is never
triggered in unit tests.
"""

from __future__ import annotations

import pytest

from friendex.adapters.tasks.base_task import BackgroundTask
from friendex.adapters.tasks.task_runner import TaskRunner


# ---------------------------------------------------------------------------
# Helpers


class _MinuteTask(BackgroundTask):
    """Minimal concrete task with a valid 1-minute cadence for runner construction."""

    interval_minutes = 1
    ticks: int = 0

    async def _run(self) -> None:
        self.ticks += 1


class _FakeLoop:
    """Stand-in for ``discord.ext.tasks.Loop`` for lifecycle assertions."""

    def __init__(self, *, running_initially: bool = False) -> None:
        self.running = running_initially
        self.started = False
        self.cancelled = False

    def is_running(self) -> bool:
        return self.running

    def start(self) -> None:
        self.started = True
        self.running = True

    def cancel(self) -> None:
        self.cancelled = True
        self.running = False


def _runner_with_fake_loop(*, running: bool = False) -> tuple[TaskRunner, _FakeLoop]:
    """Build a runner whose loop is replaced with a controllable fake."""
    runner = TaskRunner(_MinuteTask())
    fake = _FakeLoop(running_initially=running)
    runner._loop = fake  # type: ignore[assignment]
    return runner, fake


# ---------------------------------------------------------------------------
# Construction


def test_task_runner_holds_task() -> None:
    """The wrapped task is accessible as ``runner._task``."""
    task = _MinuteTask()
    runner = TaskRunner(task)
    assert runner._task is task


# ---------------------------------------------------------------------------
# start()


def test_start_starts_loop_when_not_running() -> None:
    """``start()`` calls ``_loop.start()`` when the loop is not running."""
    runner, fake = _runner_with_fake_loop(running=False)
    runner.start()
    assert fake.started is True


def test_start_is_idempotent_when_loop_already_running() -> None:
    """``start()`` is a no-op if the loop is already running."""
    runner, fake = _runner_with_fake_loop(running=True)
    runner.start()
    assert fake.started is False


# ---------------------------------------------------------------------------
# stop()


def test_stop_cancels_loop_when_running() -> None:
    """``stop()`` calls ``_loop.cancel()`` when the loop is running."""
    runner, fake = _runner_with_fake_loop(running=True)
    runner.stop()
    assert fake.cancelled is True


def test_stop_is_safe_when_loop_not_running() -> None:
    """``stop()`` is a no-op when the loop has not been started."""
    runner, fake = _runner_with_fake_loop(running=False)
    runner.stop()
    assert fake.cancelled is False


# ---------------------------------------------------------------------------
# _tick delegation


async def test_tick_delegates_to_task_run() -> None:
    """``_tick`` increments the task's counter, proving delegation to ``_run``."""
    task = _MinuteTask()
    runner = TaskRunner(task)
    assert task.ticks == 0
    await runner._tick()
    assert task.ticks == 1
    await runner._tick()
    assert task.ticks == 2
