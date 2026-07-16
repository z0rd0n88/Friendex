"""Timeline runner: execute a scenario under a freezegun master clock.

Most services sample ``datetime.now(tz=UTC)`` directly (there is no clock
port), so freezegun is the only reliable way to steer simulation time: the
runner freezes at ``start_at`` and ``move_to``s each action's timestamp.
The schema guarantees timestamps are monotonic, so time only moves forward
— exactly like production.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from freezegun import freeze_time

from tests.simulation.harness.actions import execute
from tests.simulation.harness.expect import (
    SimulationAssertionFailure,
    check_expectation,
)
from tests.simulation.harness.world import SimWorld

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from tests.simulation.harness.schema import Scenario


def _on_action_failure(
    failures: list[SimulationAssertionFailure],
    failure: SimulationAssertionFailure,
) -> None:
    """Hybrid failure policy (user decision, 2026-07-10).

    Value-mismatch failures are collected so one run surveys the whole
    timeline; an *unexpected exception* (``__cause__`` set — the action
    crashed rather than diverged) fails fast because every downstream
    expectation is suspect after a crash.
    """
    if failure.__cause__ is not None:
        raise failure
    failures.append(failure)


async def run_scenario(
    scenario: Scenario,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Execute every timeline action and assert its declared expectations."""
    world = SimWorld(scenario, sessionmaker)
    failures: list[SimulationAssertionFailure] = []

    with freeze_time(scenario.start_at) as frozen:
        await world.seed()
        for action in scenario.timeline:
            frozen.move_to(action.at)
            outcome = await execute(world, action)
            for _ in range(action.repeat - 1):
                outcome = await execute(world, action)
            try:
                await check_expectation(world, action, outcome)
            except SimulationAssertionFailure as failure:
                _on_action_failure(failures, failure)

    if failures:
        summary = "\n".join(f"- {f}" for f in failures)
        raise SimulationAssertionFailure(
            f"[{scenario.name}] {len(failures)} action(s) failed:\n{summary}"
        )
