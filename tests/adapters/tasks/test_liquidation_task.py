"""Behavioural tests for :class:`LiquidationTask` (Phase 9 AC3).

Wraps :meth:`LiquidationService.check_and_liquidate_shorts` on a 5-minute
cadence and emits one notification per :class:`LiquidationEvent` via an
injected callback. The task itself MUST NOT import ``discord`` — the callback
is generic.

Acceptance criteria:

* **L1** — the service is invoked per guild and the notifier callback receives
  every :class:`LiquidationEvent` in order.
* **L2** — a notifier raising on one event does not break the rest of the
  sweep (notifier failures are isolated like service failures).
* **L3** — a service raising for one guild does not abort the next guild's
  sweep.
* **L4** — cadence is 5 minutes (spec-pinned).
* **L5** — the task module does NOT import ``discord``.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from friendex.adapters.tasks.liquidation_task import LiquidationTask
from friendex.application.liquidation_events import LiquidationEvent

if TYPE_CHECKING:
    from friendex.application.liquidation_service import LiquidationService


NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


def _event(
    *,
    holder: str = "holder",
    target: str = "target",
    shares: int = 1,
    pnl: Decimal = Decimal("0.00"),
) -> LiquidationEvent:
    return LiquidationEvent(
        holder_id=holder,
        target_id=target,
        shares=shares,
        entry_price=Decimal("100.00"),
        exit_price=Decimal("150.00"),
        collateral_returned=Decimal("0.00"),
        pnl=pnl,
        timestamp=NOW,
    )


def _factory(services: dict[str, LiquidationService]) -> object:
    def factory(guild_id: str) -> LiquidationService:
        return services[guild_id]

    return factory


async def test_liquidation_task_emits_every_event_in_order() -> None:
    """L1: every event the service returns is handed to the notifier in order."""
    e1 = _event(holder="h1", target="t1")
    e2 = _event(holder="h2", target="t2")
    e3 = _event(holder="h3", target="t3")

    svc_a = MagicMock()
    svc_a.check_and_liquidate_shorts = AsyncMock(return_value=[e1, e2])
    svc_b = MagicMock()
    svc_b.check_and_liquidate_shorts = AsyncMock(return_value=[e3])

    seen: list[LiquidationEvent] = []

    async def notifier(event: LiquidationEvent) -> None:
        seen.append(event)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = LiquidationTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
        notifier=notifier,
    )
    await task._run()

    assert seen == [e1, e2, e3]


async def test_liquidation_task_swallows_service_exception() -> None:
    """L3: a service raising for one guild does not abort the next sweep."""
    svc_a = MagicMock()
    svc_a.check_and_liquidate_shorts = AsyncMock(side_effect=RuntimeError("nope"))
    svc_b = MagicMock()
    svc_b.check_and_liquidate_shorts = AsyncMock(return_value=[_event()])

    seen: list[LiquidationEvent] = []

    async def notifier(event: LiquidationEvent) -> None:
        seen.append(event)

    async def iter_guilds() -> list[str]:
        return ["g1", "g2"]

    task = LiquidationTask(
        service_factory=_factory({"g1": svc_a, "g2": svc_b}),
        iter_guild_ids=iter_guilds,
        notifier=notifier,
    )
    # Must NOT raise.
    await task._run()

    # g1 raised → no events delivered. g2 still processed → one event.
    assert len(seen) == 1


async def test_liquidation_task_swallows_notifier_exception() -> None:
    """L2: a notifier failure on one event does not block subsequent events."""
    e1 = _event(holder="h1", target="t1")
    e2 = _event(holder="h2", target="t2")

    svc = MagicMock()
    svc.check_and_liquidate_shorts = AsyncMock(return_value=[e1, e2])

    seen: list[LiquidationEvent] = []

    async def notifier(event: LiquidationEvent) -> None:
        seen.append(event)
        if event is e1:
            raise RuntimeError("notifier oops")

    async def iter_guilds() -> list[str]:
        return ["g1"]

    task = LiquidationTask(
        service_factory=_factory({"g1": svc}),
        iter_guild_ids=iter_guilds,
        notifier=notifier,
    )
    await task._run()

    assert seen == [e1, e2]


def test_liquidation_task_cadence_is_five_minutes() -> None:
    """L4: the declared cadence is 5 minutes (spec-pinned)."""
    assert LiquidationTask.interval_minutes == 5
    assert LiquidationTask.interval_hours == 0


def test_liquidation_task_module_does_not_import_discord() -> None:
    """L5: the task module never imports ``discord`` (notifier is generic).

    Asserted by reading the module's source — both ``import discord`` and
    ``from discord...`` are forbidden anywhere in the code (the docstring is
    free to *mention* "discord notifier", which is why we strip it before
    scanning).
    """
    import inspect

    module = sys.modules["friendex.adapters.tasks.liquidation_task"]
    source = inspect.getsource(module)
    # Drop the module docstring so we only scan executable code.
    code_after_docstring = source.split('"""', maxsplit=2)[-1]
    assert "import discord" not in code_after_docstring, (
        "liquidation_task.py must not contain `import discord`"
    )
    assert "from discord" not in code_after_docstring, (
        "liquidation_task.py must not contain `from discord...`"
    )
