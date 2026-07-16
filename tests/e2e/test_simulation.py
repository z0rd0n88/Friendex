"""Run every YAML scenario end-to-end over a real container + in-memory DB.

Each scenario file under ``scenarios/`` is one parametrized test case: the
runner seeds the fake server, freezes time at ``start_at``, then executes
the timeline (slash commands, gateway events, background-task ticks) and
asserts each action's config-declared expectations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.e2e.conftest import scenario_paths
from tests.e2e.harness.runner import run_scenario
from tests.e2e.harness.schema import load_scenario

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest.mark.parametrize(
    "scenario_path",
    scenario_paths(),
    ids=lambda p: p.stem,
)
async def test_scenario(
    scenario_path: Path,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    scenario = load_scenario(scenario_path)
    await run_scenario(scenario, sessionmaker)
