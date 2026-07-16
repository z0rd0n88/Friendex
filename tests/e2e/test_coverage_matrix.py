"""Coverage-matrix guard: every command, event, task, and reachable error
must appear in at least one scenario.

Parses the scenario YAMLs structurally (same loader as the runner) so the
matrix can never drift from what actually executes. If a new command / task /
error is added to the bot, this test fails until a scenario exercises it —
that is the point.
"""

from __future__ import annotations

from tests.e2e.conftest import scenario_paths
from tests.e2e.harness.schema import (
    COMMAND_NAMES,
    EVENT_NAMES,
    TASK_NAMES,
    Scenario,
    load_scenario,
)

# Every DomainError the application can actually raise, plus the two
# handler-only branches and the permission-check path. The four defined-but-
# unreachable errors are deliberate non-goals: NoPosition("long") (sell
# raises InsufficientShares first), AlreadyOptedIn / AlreadyOptedOut (the
# opt-in flow is idempotent), and DiscordError (never raised).
REACHABLE_ERRORS: frozenset[str] = frozenset(
    {
        "InsufficientFunds",
        "MarketClosed",
        "PositionFrozen",
        "OnCooldown",
        "OptedOut",
        "NoPosition",
        "InsufficientShares",
        "SelfTrade",
        "InvalidAmount",
        "FundInsufficientBalance",
        "AlreadyClaimedToday",
        "FundNotFound",
        "NotFundManager",
        "CheckFailure",
        "ValueError",
        "PersistenceError",
    }
)


def _load_all() -> list[Scenario]:
    paths = scenario_paths()
    assert paths, "no scenario files found"
    return [load_scenario(p) for p in paths]


def _covered(scenarios: list[Scenario], kind: str) -> set[str]:
    return {
        action.name
        for scenario in scenarios
        for action in scenario.timeline
        if action.kind == kind
    }


def test_every_command_is_exercised() -> None:
    missing = COMMAND_NAMES - _covered(_load_all(), "command")
    assert not missing, f"commands never exercised by any scenario: {sorted(missing)}"


def test_every_event_is_exercised() -> None:
    missing = EVENT_NAMES - _covered(_load_all(), "event")
    assert not missing, f"events never exercised by any scenario: {sorted(missing)}"


def test_every_task_is_exercised() -> None:
    missing = TASK_NAMES - _covered(_load_all(), "task")
    assert not missing, f"tasks never exercised by any scenario: {sorted(missing)}"


def test_every_reachable_error_is_expected_somewhere() -> None:
    expected_errors = {
        action.expect.error
        for scenario in _load_all()
        for action in scenario.timeline
        if action.expect.error is not None
    }
    missing = REACHABLE_ERRORS - expected_errors
    assert not missing, f"error paths never asserted by any scenario: {sorted(missing)}"


def test_scenarios_assert_failures_not_just_success() -> None:
    """Every scenario file must contain at least one expected-error action."""
    for scenario in _load_all():
        has_error = any(a.expect.error is not None for a in scenario.timeline)
        has_success = any(
            a.expect.error is None and not a.expect.is_empty for a in scenario.timeline
        )
        assert has_success, f"{scenario.name}: no asserted success path"
        # The happy-path and day-in-the-life scenarios are allowed to be
        # all-success; every *-errors / edge scenario must assert failures.
        if "error" in scenario.name or "edge" in scenario.name:
            assert has_error, f"{scenario.name}: no asserted error path"
