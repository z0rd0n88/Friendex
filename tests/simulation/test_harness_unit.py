"""Unit tests for the simulation harness: schema validation + matchers.

The scenario YAMLs are executable config — a malformed file must fail at
load time with a message naming the offending entry, and the matcher
semantics (type coercion, operator maps, approx tolerance) must be exact,
because every scenario assertion routes through them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from tests.simulation.harness.expect import SimulationAssertionFailure, check_matcher
from tests.simulation.harness.schema import ScenarioError, load_scenario

_MINIMAL = """
name: minimal
start_at: "2026-05-25 12:00:00"
guild: {{id: 1, name: G}}
users:
  alice: {{id: 1111}}
timeline:
{timeline}
"""


def _write(tmp_path: Path, timeline: str, **replace: str) -> Path:
    content = _MINIMAL.format(timeline=timeline)
    for old, new in replace.items():
        content = content.replace(old, new)
    path = tmp_path / "scenario.yml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Schema validation


def test_minimal_scenario_loads(tmp_path: Path) -> None:
    scenario = load_scenario(_write(tmp_path, "  - {command: daily, actor: alice}"))
    assert scenario.name == "minimal"
    assert scenario.start_at == datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    assert scenario.timeline[0].name == "daily"
    assert scenario.timeline[0].at == scenario.start_at


def test_relative_at_offsets_from_previous_action(tmp_path: Path) -> None:
    scenario = load_scenario(
        _write(
            tmp_path,
            '  - {command: daily, actor: alice, at: "+5m"}\n'
            '  - {command: balance, actor: alice, at: "+2h"}',
        )
    )
    first, second = scenario.timeline
    assert (first.at - scenario.start_at).total_seconds() == 300
    assert (second.at - first.at).total_seconds() == 7200


def test_non_monotonic_timeline_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '  - {command: daily, actor: alice, at: "2026-05-25 13:00:00"}\n'
        '  - {command: balance, actor: alice, at: "2026-05-25 12:30:00"}',
    )
    with pytest.raises(ScenarioError, match="monotonic"):
        load_scenario(path)


def test_unknown_command_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "  - {command: yolo, actor: alice}")
    with pytest.raises(ScenarioError, match="unknown command 'yolo'"):
        load_scenario(path)


def test_command_without_actor_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "  - {command: daily}")
    with pytest.raises(ScenarioError, match="require an 'actor'"):
        load_scenario(path)


def test_undeclared_actor_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "  - {command: daily, actor: mallory}")
    with pytest.raises(ScenarioError, match="'mallory' not declared"):
        load_scenario(path)


def test_undeclared_args_user_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - {command: buy, actor: alice, args: {user: ghost, shares: 1}}",
    )
    with pytest.raises(ScenarioError, match=r"args\.user='ghost'"):
        load_scenario(path)


def test_unknown_expect_key_is_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - {command: daily, actor: alice, expect: {reward: 500}}",
    )
    with pytest.raises(ScenarioError, match="unknown expect keys"):
        load_scenario(path)


def test_multiple_action_kinds_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - {command: daily, task: activity_tick, actor: alice}",
    )
    with pytest.raises(ScenarioError, match="exactly one of"):
        load_scenario(path)


def test_duplicate_user_ids_are_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "  - {command: daily, actor: alice}",
        **{"alice: {id: 1111}": "alice: {id: 1111}\n  bob: {id: 1111}"},
    )
    with pytest.raises(ScenarioError, match="duplicate user ids"):
        load_scenario(path)


# ---------------------------------------------------------------------------
# Matchers


def test_scalar_matcher_coerces_to_decimal() -> None:
    check_matcher(Decimal("10.50"), "10.50", where="t")
    check_matcher(Decimal("10.50"), 10.5, where="t")
    with pytest.raises(SimulationAssertionFailure, match="expected"):
        check_matcher(Decimal("10.50"), "10.51", where="t")


def test_scalar_matcher_bool_and_int() -> None:
    check_matcher(True, True, where="t")
    check_matcher(3, "3", where="t")
    with pytest.raises(SimulationAssertionFailure):
        check_matcher(False, True, where="t")


def test_operator_matchers() -> None:
    check_matcher(Decimal("101"), {"gt": "100", "le": "101"}, where="t")
    with pytest.raises(SimulationAssertionFailure, match="failed checks: gt"):
        check_matcher(Decimal("99"), {"gt": "100"}, where="t")


def test_approx_matcher_uses_ratio_tolerance() -> None:
    check_matcher(Decimal("1012.50"), {"approx": "1012", "tol": "0.001"}, where="t")
    with pytest.raises(SimulationAssertionFailure, match="approx"):
        check_matcher(Decimal("1100"), {"approx": "1000", "tol": "0.01"}, where="t")


def test_unknown_matcher_op_is_rejected() -> None:
    with pytest.raises(SimulationAssertionFailure, match="unknown matcher ops"):
        check_matcher(1, {"gte": 1}, where="t")
