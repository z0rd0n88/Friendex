"""Behavioural tests for :class:`BackgroundTask` (Phase 9 AC0).

The base owns ONE load-bearing contract: every exception raised by the wrapped
service inside :meth:`BackgroundTask._safe_run` is **swallowed** and logged via
structlog — never re-raised — so the ``discord.ext.tasks`` scheduler never sees
a crash that would cancel the loop on the user's behalf. This is the single
quality the task layer guarantees over what the application services raise.

The test instantiates a trivial concrete subclass (the abstract base is not
directly instantiable by contract) and drives ``_safe_run`` with a coroutine
that raises arbitrary exception types.
"""

from __future__ import annotations

import pytest

from friendex.adapters.tasks.base_task import BackgroundTask


class _NoOpTask(BackgroundTask):
    """Concrete subclass that only implements the bare scaffold."""

    async def _run(self) -> None:  # pragma: no cover - exercised indirectly
        return None


async def test_safe_run_swallows_runtime_error() -> None:
    """A :class:`RuntimeError` from the wrapped coroutine is swallowed."""
    task = _NoOpTask()

    async def boom() -> None:
        raise RuntimeError("boom")

    # MUST NOT raise.
    await task._safe_run(boom())


async def test_safe_run_swallows_value_error() -> None:
    """A :class:`ValueError` from the wrapped coroutine is swallowed."""
    task = _NoOpTask()

    async def boom() -> None:
        raise ValueError("bad value")

    await task._safe_run(boom())


async def test_safe_run_swallows_generic_exception() -> None:
    """A bare :class:`Exception` from the wrapped coroutine is swallowed."""
    task = _NoOpTask()

    async def boom() -> None:
        raise Exception("generic")

    await task._safe_run(boom())


async def test_safe_run_passes_through_normal_return() -> None:
    """A normally-completing coroutine does not affect the task."""
    task = _NoOpTask()
    sentinel: list[int] = []

    async def ok() -> None:
        sentinel.append(1)

    await task._safe_run(ok())
    assert sentinel == [1]


def test_base_task_is_abstract() -> None:
    """The :class:`BackgroundTask` class cannot be instantiated directly."""
    with pytest.raises(TypeError):
        BackgroundTask()  # type: ignore[abstract]


class _FakeLoop:
    """Minimal stand-in for ``discord.ext.tasks.Loop`` for lifecycle tests."""

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


def test_start_starts_loop_when_not_running() -> None:
    """``start()`` calls ``_loop.start()`` exactly when not already running."""
    task = _NoOpTask()
    task._loop = _FakeLoop(running_initially=False)
    task.start()
    assert task._loop.started is True


def test_start_is_idempotent_when_loop_already_running() -> None:
    """``start()`` is a no-op if the loop is already running."""
    task = _NoOpTask()
    task._loop = _FakeLoop(running_initially=True)
    task.start()
    assert task._loop.started is False  # never called


def test_stop_cancels_loop_when_running() -> None:
    """``stop()`` cancels exactly when the loop is running."""
    task = _NoOpTask()
    task._loop = _FakeLoop(running_initially=True)
    task.stop()
    assert task._loop.cancelled is True


def test_stop_is_safe_when_loop_not_running() -> None:
    """``stop()`` is a no-op when the loop has not been started."""
    task = _NoOpTask()
    task._loop = _FakeLoop(running_initially=False)
    task.stop()
    assert task._loop.cancelled is False
