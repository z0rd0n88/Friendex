"""Behavioural tests for :class:`BackgroundTask` (Phase 9 AC0).

The base owns ONE load-bearing contract: every exception raised by the wrapped
service inside :meth:`BackgroundTask._safe_run` is **swallowed** and logged via
structlog — never re-raised — so the ``discord.ext.tasks`` scheduler never sees
a crash that would cancel the loop on the user's behalf. This is the single
quality the task layer guarantees over what the application services raise.

The test instantiates a trivial concrete subclass (the abstract base is not
directly instantiable by contract) and drives ``_safe_run`` with a coroutine
that raises arbitrary exception types.

Lifecycle tests (start/stop) live in ``test_task_runner.py`` — the loop is
now owned by :class:`~friendex.adapters.tasks.task_runner.TaskRunner`.
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
