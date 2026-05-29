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

from typing import TYPE_CHECKING

from friendex.adapters.tasks.base_task import BackgroundTask
from friendex.adapters.tasks.task_runner import TaskRunner

if TYPE_CHECKING:
    import pytest

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
# Tests for ``start()`` — loop startup is idempotent.


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
# Tests for ``stop()`` — loop cancellation is safe when not running.


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


# ---------------------------------------------------------------------------
# Restart-on-failure (Wave 1 #82 M3)
#
# If a ``discord.ext.tasks.loop`` ever escapes ``_safe_run`` with an unhandled
# exception (e.g. a bug in the swallow layer itself, or a callback raising
# outside ``_tick``), the runner restarts it with exponential backoff so the
# loop survives a crash instead of going silent. The handler is registered
# via the loop's ``error`` decorator at construction time.


def test_runner_registers_error_handler_at_construction() -> None:
    """The runner wires an ``error`` handler on its loop at construction time."""
    runner = TaskRunner(_MinuteTask())
    # The underlying loop must have a custom error coroutine set, not the
    # discord.py default. We accept any callable bound on ``_error``.
    assert callable(runner._loop._error)


async def test_runner_error_handler_restarts_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The error handler sleeps with exponential backoff and calls ``restart``.

    The runner's error coroutine takes (exc) and:
    1. Logs the failure with traceback.
    2. Sleeps for a backoff interval (exponential, capped).
    3. Calls ``self._loop.restart()``.
    """
    runner, fake = _runner_with_fake_loop(running=False)
    fake.restarted = False  # type: ignore[attr-defined]

    def restart() -> None:
        fake.restarted = True  # type: ignore[attr-defined]

    fake.restart = restart  # type: ignore[attr-defined]

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await runner._on_loop_error(RuntimeError("kaboom"))

    assert fake.restarted is True  # type: ignore[attr-defined]
    assert len(sleeps) == 1
    assert sleeps[0] > 0


async def test_runner_backoff_grows_then_caps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated failures grow the backoff up to a sensible cap."""
    runner = TaskRunner(_MinuteTask())

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    # Stub out the loop's restart so we can drive multiple cycles.
    restarts = 0

    def stub_restart() -> None:
        nonlocal restarts
        restarts += 1

    runner._loop.restart = stub_restart  # type: ignore[assignment]

    for _ in range(8):
        await runner._on_loop_error(RuntimeError("kaboom"))

    assert restarts == 8
    # Successive sleeps must be non-decreasing (exponential) up to a cap.
    from itertools import pairwise

    assert all(b >= a for a, b in pairwise(sleeps))
    # Cap is enforced — no sleep can exceed 5 minutes.
    assert max(sleeps) <= 300.0


async def test_runner_resets_consecutive_failures_on_clean_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful ``_tick`` clears the failure counter back to zero.

    Wave 1 PR #89 fix-up (M-1): without the reset, an isolated crash months
    after a pile-up still triggers the 5-minute cap. The fix: after every
    successful ``_tick`` body, set ``_consecutive_failures = 0`` so the next
    unrelated failure starts at 1x base backoff, not 2^N x base.

    Scenario: one failure (counter -> 1), then a clean tick (counter -> 0),
    then another failure — the second failure's sleep MUST be the 1-second
    base, not the 2-second second-attempt level.
    """
    runner = TaskRunner(_MinuteTask())

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    # Stub out the loop's restart so we can drive multiple cycles.
    def stub_restart() -> None:
        pass

    runner._loop.restart = stub_restart  # type: ignore[assignment]

    # Failure 1 — sleeps 1s (base * 2^0).
    await runner._on_loop_error(RuntimeError("first fail"))
    assert sleeps == [1.0]

    # A clean tick clears the counter.
    await runner._tick()

    # Failure 2 — must be back to 1s, NOT 2s.
    await runner._on_loop_error(RuntimeError("second fail"))
    assert sleeps == [1.0, 1.0], (
        "after a clean tick the failure counter must reset so the next "
        "unrelated failure starts at the base backoff, not 2x base"
    )


# ---------------------------------------------------------------------------
# Startup stagger (Wave 1 #82 M4)
#
# When the bot boots, every task starts at almost the same instant — so they
# all hit SQLite simultaneously on their first tick. The runner adds a small
# random offset (0-2s) via ``before_loop`` so the cohort spreads out.


def test_runner_registers_before_loop_hook_at_construction() -> None:
    """The runner wires a ``before_loop`` hook on its loop at construction time."""
    runner = TaskRunner(_MinuteTask())
    # The discord.py default is None until ``before_loop`` is called.
    assert callable(runner._loop._before_loop)


async def test_runner_before_loop_staggers_with_random_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``before_loop`` sleeps for a bounded random offset before the first tick."""
    runner = TaskRunner(_MinuteTask())

    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    # Pin the random source so the assertion is deterministic.
    monkeypatch.setattr("random.uniform", lambda lo, hi: 0.5)

    await runner._before_first_tick()

    assert sleeps == [0.5]


async def test_runner_before_loop_offset_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stagger delay is bounded (0, 2] seconds — small enough to be cheap."""
    runner = TaskRunner(_MinuteTask())

    observed_bounds: list[tuple[float, float]] = []

    def capture_bounds(lo: float, hi: float) -> float:
        observed_bounds.append((lo, hi))
        return lo

    monkeypatch.setattr("random.uniform", capture_bounds)

    async def fake_sleep(delay: float) -> None:
        pass

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    await runner._before_first_tick()

    assert observed_bounds == [(0.0, 2.0)]
