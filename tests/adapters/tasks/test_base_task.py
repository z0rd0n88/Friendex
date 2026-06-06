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


async def test_bind_guild_id_provider_installs_provider() -> None:
    """``bind_guild_id_provider`` is the public seam used by the container.

    Wave 1 PR #89 fix-up (M-2): replaces direct ``task._iter_guild_ids = fn``
    mutation. Pins the contract that the setter assigns the callable to
    ``_iter_guild_ids`` so future readers can find the wiring through a
    typed method instead of an attribute write.
    """
    task = _NoOpTask()

    async def provider() -> list[str]:
        return ["g1", "g2"]

    task.bind_guild_id_provider(provider)

    # Round-trip: the bound provider returns what we registered.
    guilds = list(await task._iter_guild_ids())
    assert guilds == ["g1", "g2"]


async def test_for_each_guild_calls_factory_for_every_guild() -> None:
    """``for_each_guild`` invokes the coro-factory for each guild in order.

    Wave 1 #82 (Item 7): the helper encapsulates the per-guild fan-out +
    ``_safe_run`` pattern so task ``_run`` bodies stay free of the boilerplate
    loop. This test confirms that a factory returning a normally-completing
    coroutine is called exactly once per guild in iteration order.
    """
    task = _NoOpTask()
    visited: list[str] = []

    async def provider() -> list[str]:
        return ["g1", "g2", "g3"]

    task.bind_guild_id_provider(provider)

    async def process(guild_id: str) -> None:
        visited.append(guild_id)

    await task.for_each_guild(process)

    assert visited == ["g1", "g2", "g3"]


async def test_for_each_guild_isolates_failing_guild() -> None:
    """``for_each_guild`` wraps each call in ``_safe_run`` so one failure cannot
    abort the remaining guilds.

    Wave 1 #82 (Item 7): the unsafe pattern — iterating guilds without
    ``_safe_run`` — is the historical default and must require deliberate
    effort. The helper makes isolation the default.
    """
    task = _NoOpTask()
    visited: list[str] = []

    async def provider() -> list[str]:
        return ["good1", "bad", "good2"]

    task.bind_guild_id_provider(provider)

    async def process(guild_id: str) -> None:
        if guild_id == "bad":
            raise RuntimeError("guild failure")
        visited.append(guild_id)

    # Must NOT raise even though "bad" raises.
    await task.for_each_guild(process)

    # Both good guilds must have been processed despite the middle failure.
    assert visited == ["good1", "good2"]


async def test_for_each_guild_returns_none_on_all_success() -> None:
    """``for_each_guild`` returns ``None`` when all guilds succeed."""
    task = _NoOpTask()

    async def provider() -> list[str]:
        return ["g1"]

    task.bind_guild_id_provider(provider)

    async def noop(_: str) -> None:
        return None

    await task.for_each_guild(noop)


async def test_safe_run_logs_exception_with_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_safe_run`` passes ``exc_info=True`` so structlog captures the traceback.

    Per the Wave 1 #84 M fix, the log call MUST carry the full traceback so
    operations can debug a per-tick failure — passing only ``str(exc)`` strips
    the call stack and turns silent-failure debugging into guesswork.
    """
    captured_kwargs: dict[str, object] = {}

    def fake_error(event: str, **kwargs: object) -> None:
        captured_kwargs.clear()
        captured_kwargs["event"] = event
        captured_kwargs.update(kwargs)

    import friendex.adapters.tasks.base_task as base_task_module

    monkeypatch.setattr(base_task_module._log, "error", fake_error)

    task = _NoOpTask()

    async def boom() -> None:
        raise RuntimeError("with-traceback")

    await task._safe_run(boom())

    assert captured_kwargs.get("event") == "background_task_iteration_failed"
    # The actual exception instance is bound on the log record (any of these
    # variants is acceptable as long as a real traceback is wired up).
    assert "exc_info" in captured_kwargs, (
        "structlog call must carry `exc_info` so the traceback is recorded"
    )
    exc_info = captured_kwargs["exc_info"]
    # Accept `True` (structlog reads sys.exc_info()) or the bound exception.
    assert exc_info is True or isinstance(exc_info, RuntimeError | tuple)
