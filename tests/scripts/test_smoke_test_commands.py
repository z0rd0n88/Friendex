"""Tests for ``scripts/smoke_test_commands.py``.

These tests pin the contract of the Phase 16 cutover smoke-test driver
(see ``docs/04-migration-plan.md`` §Phase 16 and ``CLAUDE.md`` §Bot
Commands).  They guarantee that:

* Every slash command shipped in the bot is represented exactly once.
* Every listener event from the Phase 12 listener foundations is
  represented at least once.
* Every background task from the Phase 9 task foundations is represented
  at least once.
* ``main()`` prints the steps in their declared id order (no shuffling).
* ``STEPS`` is genuinely immutable — both the tuple itself and the
  per-step frozen dataclass.

The TDD red baseline for these tests is captured in the Phase 16
baton-pass (``baton-pass/phase-16/``).
"""

from __future__ import annotations

import dataclasses
from itertools import pairwise
from typing import TYPE_CHECKING

import pytest
from scripts.smoke_test_commands import STEPS, SmokeStep, main

if TYPE_CHECKING:
    from collections.abc import Iterator

# ---------------------------------------------------------------------------
# Hard-coded expectation sets — these are the spec, not derived from STEPS.
# A missing or extra step in the script will fail one of the asserts below.
# ---------------------------------------------------------------------------

# 13 top-level slash commands + 4 ``/fund`` subcommands = 17 entries.
EXPECTED_SLASH_COMMANDS: frozenset[str] = frozenset(
    {
        "/balance",
        "/daily",
        "/price",
        "/mystock",
        "/buy",
        "/sell",
        "/short",
        "/cover",
        "/portfolio",
        "/trending",
        "/mystats",
        "/optin",
        "/optout",
        "/fund create",
        "/fund invest",
        "/fund withdraw",
        "/fund info",
    }
)

# Phase 12a + 12b digest event surface.  Each entry is a literal substring
# the matching step's ``name`` field MUST contain.
EXPECTED_LISTENER_EVENTS: frozenset[str] = frozenset(
    {
        "on_message",
        "on_reaction_add",
        "on_voice_state_update",
        "on_member_update",
        "opt-out blocks tradeability",
    }
)

# Phase 9 digest task surface.  Each entry is a literal substring the
# matching step's ``name`` field MUST contain.
EXPECTED_BACKGROUND_TASKS: frozenset[str] = frozenset(
    {
        "activity tick",
        "short liquidation",
        "daily streak rollover",
        "hedge fund APY accrual",
        "early-withdrawal penalty decay",
        "VC extra-boost",
    }
)


def _slash_command_steps() -> Iterator[SmokeStep]:
    return (s for s in STEPS if s.category == "slash")


def _listener_steps() -> Iterator[SmokeStep]:
    return (s for s in STEPS if s.category == "listener")


def _background_steps() -> Iterator[SmokeStep]:
    return (s for s in STEPS if s.category == "background")


# ---------------------------------------------------------------------------
# AC3 (a) — slash command coverage
# ---------------------------------------------------------------------------


def test_expected_slash_commands_has_at_least_seventeen_entries() -> None:
    """Sanity guard on EXPECTED_SLASH_COMMANDS itself.

    The acceptance criterion requires ``len >= 17`` so this test pins the
    expectation set before the more interesting comparison below.
    """
    assert len(EXPECTED_SLASH_COMMANDS) >= 17


def test_every_slash_command_is_represented_exactly_once() -> None:
    """Each documented slash command appears in STEPS exactly once."""
    commands_in_steps: list[str] = [s.command for s in _slash_command_steps()]
    # Exactly-once: the multiset of commands matches the expectation set
    # both ways.  A missing entry fails the subset check below; a
    # duplicate fails the length check.
    assert set(commands_in_steps) == EXPECTED_SLASH_COMMANDS
    assert len(commands_in_steps) == len(EXPECTED_SLASH_COMMANDS)


def test_fund_invest_step_describes_live_invest_path() -> None:
    """C4: ``/fund invest`` is live in Phase 17b — the smoke step now
    describes the happy-path (debit invoker, credit fund, record stake)
    plus the pinned domain errors (self-invest blocked, missing fund,
    insufficient funds), and no longer mentions ``NotImplementedError``
    or "deferred". #82 H17 promoted the self-invest gate to
    :class:`NotFundManager` and the missing-fund gate to
    :class:`FundNotFound`; pre-fix both repurposed :class:`InvalidAmount`.
    """
    invest_steps = [s for s in STEPS if s.command == "/fund invest"]
    assert len(invest_steps) == 1
    expected_text = invest_steps[0].expected.lower()
    # Live-invest semantics: the investor's stake on the fund is recorded.
    assert "stake" in expected_text
    # Self-invest pin (#82 H17): surfaces as NotFundManager.
    assert "notfundmanager" in expected_text
    # Missing-fund pin (#82 H17): surfaces as FundNotFound.
    assert "fundnotfound" in expected_text
    # The retired "deferred / NotImplementedError" pin must NOT be back.
    assert "notimplementederror" not in expected_text
    assert "deferred" not in expected_text


# ---------------------------------------------------------------------------
# AC3 (b) — listener coverage
# ---------------------------------------------------------------------------


def test_every_listener_event_is_present() -> None:
    """Each Phase 12 listener event has at least one matching step."""
    listener_names_lower = [s.name.lower() for s in _listener_steps()]
    for needle in EXPECTED_LISTENER_EVENTS:
        assert any(needle.lower() in name for name in listener_names_lower), (
            f"listener event {needle!r} is not represented in STEPS; "
            f"listener step names = {listener_names_lower}"
        )


# ---------------------------------------------------------------------------
# AC3 (c) — background-task coverage
# ---------------------------------------------------------------------------


def test_every_background_task_is_present() -> None:
    """Each Phase 9 background task has at least one matching step."""
    bg_names_lower = [s.name.lower() for s in _background_steps()]
    for needle in EXPECTED_BACKGROUND_TASKS:
        assert any(needle.lower() in name for name in bg_names_lower), (
            f"background task {needle!r} is not represented in STEPS; "
            f"background step names = {bg_names_lower}"
        )


# ---------------------------------------------------------------------------
# AC3 (d) — main() prints steps in id order, no shuffling.
# ---------------------------------------------------------------------------


def test_main_prints_steps_in_strict_id_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``main()`` prints each step's id; the resulting integer sequence
    is strictly increasing across the entire output."""
    exit_code = main()
    assert exit_code == 0

    captured = capsys.readouterr().out

    # Parse out the leading "<id>." token of every "Step N." header.  We
    # rely on the script printing each step's header as "Step <id>." on
    # its own line — see the implementation for the literal format.
    printed_ids: list[int] = []
    for line in captured.splitlines():
        stripped = line.strip()
        if stripped.startswith("Step "):
            # Header format pinned by ``_format_step`` in the script under test.
            token = stripped.removeprefix("Step ").split(".", 1)[0]
            printed_ids.append(int(token))

    # Every step has been printed and ids are strictly increasing.
    assert printed_ids == sorted({s.id for s in STEPS})
    assert len(printed_ids) == len(STEPS)
    for prev, curr in pairwise(printed_ids):
        assert curr > prev, (
            f"step ids must be strictly increasing in printed output; "
            f"got ...{prev}, {curr}... — full sequence {printed_ids}"
        )


def test_main_output_is_byte_stable_across_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running ``main()`` twice produces byte-identical output (no
    timestamps, no shuffling, deterministic numbering)."""
    main()
    first = capsys.readouterr().out
    main()
    second = capsys.readouterr().out
    assert first == second


# ---------------------------------------------------------------------------
# AC3 (e) — STEPS is immutable; SmokeStep is frozen.
# ---------------------------------------------------------------------------


def test_steps_is_a_tuple_not_a_list() -> None:
    """The module-level STEPS export is a tuple — append-style mutation
    would surface as an AttributeError because tuples have no .append."""
    assert isinstance(STEPS, tuple)


def test_steps_tuple_rejects_append() -> None:
    """Calling ``.append`` on the tuple raises AttributeError."""
    with pytest.raises(AttributeError):
        # ``append`` does not exist on tuple — accessing it raises
        # AttributeError, which is the exact failure shape AC3(e) asks
        # the test to pin.
        STEPS.append(  # type: ignore[attr-defined]
            SmokeStep(
                id=9999,
                category="slash",
                name="bogus",
                command="/bogus",
                expected="should not be reachable",
            )
        )


def test_steps_tuple_rejects_item_assignment() -> None:
    """Index assignment on the tuple raises TypeError."""
    with pytest.raises(TypeError):
        STEPS[0] = SmokeStep(  # type: ignore[index]
            id=0,
            category="slash",
            name="bogus",
            command="/bogus",
            expected="should not be reachable",
        )


def test_smokestep_is_frozen_dataclass() -> None:
    """``SmokeStep`` is a frozen dataclass — assigning to any field on an
    existing instance raises ``dataclasses.FrozenInstanceError``."""
    assert dataclasses.is_dataclass(SmokeStep)
    step = STEPS[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        step.id = 9999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# AC1 / AC2 — minimal structural pins so the script can't silently lose
# categories or stop being importable.
# ---------------------------------------------------------------------------


def test_all_required_categories_are_present() -> None:
    """STEPS spans every required category (AC1)."""
    categories = {s.category for s in STEPS}
    assert categories == {
        "startup",
        "slash",
        "listener",
        "background",
        "shutdown",
    }


def test_steps_have_unique_ids() -> None:
    """No two steps share an id — the id is a load-bearing primary key
    for the printed checklist."""
    ids = [s.id for s in STEPS]
    assert len(ids) == len(set(ids))
