"""Scenario schema: parse + validate YAML simulation configs.

A scenario file describes a fake Discord server, its members, and a
timestamped timeline of actions (slash commands, gateway events, background
task ticks). Each timeline entry may declare an ``expect`` block whose
assertions the runner checks declaratively after executing the action.

The loader is strict: unknown keys, malformed timestamps, out-of-order
timestamps, and references to undeclared users all raise
:class:`ScenarioError` with the offending file + action label so a broken
config fails fast instead of producing a confusing mid-run assertion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

# Command names the runner can dispatch (see ``actions.py``). Fund
# subcommands use ``fund_``-prefixed names so a timeline entry is one flat
# string.
COMMAND_NAMES: frozenset[str] = frozenset(
    {
        "balance",
        "optin",
        "optout",
        "daily",
        "buy",
        "sell",
        "short",
        "cover",
        "portfolio",
        "trending",
        "mystats",
        "price",
        "mystock",
        "fund_create",
        "fund_info",
        "fund_withdraw",
        "fund_send_events",
        "fund_invest",
        "help",
        "game_intro",
    }
)

EVENT_NAMES: frozenset[str] = frozenset(
    {
        "message",
        "reaction",
        "voice_join",
        "voice_leave",
        "voice_switch",
        "member_timeout",
        "member_ban",
        "guild_remove",
        # Synthetic events routed straight through the central slash-command
        # error handler — they exercise the CRITICAL "Unexpected error" and
        # PersistenceError branches, which no organic user action reaches.
        "raise_unexpected",
        "raise_persistence",
    }
)

TASK_NAMES: frozenset[str] = frozenset(
    {
        "activity_tick",
        "liquidation",
        "freeze_check",
        "inactivity_decay",
        "vc_boost",
        "daily_reset",
        "weekly_reset",
        "monthly_rollover",
    }
)

_RELATIVE_AT = re.compile(r"^\+(\d+)([smhdw])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


class ScenarioError(Exception):
    """Raised when a scenario file fails validation."""


@dataclass(frozen=True, kw_only=True)
class UserSpec:
    """One simulated member of the fake server."""

    name: str
    id: int
    opted_in: bool = True
    seed: bool = True
    cash: Decimal | None = None
    price: Decimal | None = None
    fund_balance: Decimal | None = None
    manage_guild: bool = False
    dms_blocked: bool = False


@dataclass(frozen=True, kw_only=True)
class Expectation:
    """Declarative post-action assertions (all optional)."""

    error: str | None = None
    reply_ephemeral: bool | None = None
    reply_contains: tuple[str, ...] = ()
    state: dict[str, dict[str, Any]] = field(default_factory=dict)
    price: dict[str, Any] = field(default_factory=dict)
    fund: dict[str, dict[str, Any]] = field(default_factory=dict)
    liquidations: int | None = None

    @property
    def is_empty(self) -> bool:
        return (
            self.error is None
            and self.reply_ephemeral is None
            and not self.reply_contains
            and not self.state
            and not self.price
            and not self.fund
            and self.liquidations is None
        )


@dataclass(frozen=True, kw_only=True)
class Action:
    """One timeline entry: exactly one of command/event/task."""

    index: int
    label: str
    at: datetime
    kind: str  # "command" | "event" | "task"
    name: str
    actor: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    expect: Expectation = field(default_factory=Expectation)
    repeat: int = 1


@dataclass(frozen=True, kw_only=True)
class Scenario:
    """A fully validated simulation scenario."""

    name: str
    description: str
    path: Path
    start_at: datetime
    guild_id: int
    guild_name: str
    users: dict[str, UserSpec]
    settings_overrides: dict[str, Any]
    timeline: tuple[Action, ...]


def _parse_at(raw: object, *, previous: datetime, label: str) -> datetime:
    """Parse an ``at`` value: absolute ISO or relative ``+<n><unit>``."""
    if isinstance(raw, datetime):
        parsed = raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
        return parsed
    if not isinstance(raw, str):
        raise ScenarioError(f"{label}: 'at' must be a string or timestamp, got {raw!r}")
    match = _RELATIVE_AT.match(raw)
    if match is not None:
        amount, unit = int(match.group(1)), match.group(2)
        return previous + timedelta(seconds=amount * _UNIT_SECONDS[unit])
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ScenarioError(f"{label}: unparseable 'at' value {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_decimal(raw: object, *, label: str, key: str) -> Decimal:
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise ScenarioError(f"{label}: {key} is not a valid amount: {raw!r}") from exc


def _parse_users(raw: object, *, path: Path) -> dict[str, UserSpec]:
    if not isinstance(raw, dict) or not raw:
        raise ScenarioError(f"{path.name}: 'users' must be a non-empty mapping")
    _allowed = {
        "id",
        "opted_in",
        "seed",
        "cash",
        "price",
        "fund_balance",
        "manage_guild",
        "dms_blocked",
    }
    users: dict[str, UserSpec] = {}
    for name, spec in raw.items():
        label = f"{path.name}: users.{name}"
        if not isinstance(spec, dict):
            raise ScenarioError(f"{label}: must be a mapping")
        unknown = set(spec) - _allowed
        if unknown:
            raise ScenarioError(f"{label}: unknown keys {sorted(unknown)}")
        if "id" not in spec:
            raise ScenarioError(f"{label}: 'id' is required")
        users[str(name)] = UserSpec(
            name=str(name),
            id=int(spec["id"]),
            opted_in=bool(spec.get("opted_in", True)),
            seed=bool(spec.get("seed", True)),
            cash=(
                _parse_decimal(spec["cash"], label=label, key="cash")
                if "cash" in spec
                else None
            ),
            price=(
                _parse_decimal(spec["price"], label=label, key="price")
                if "price" in spec
                else None
            ),
            fund_balance=(
                _parse_decimal(spec["fund_balance"], label=label, key="fund_balance")
                if "fund_balance" in spec
                else None
            ),
            manage_guild=bool(spec.get("manage_guild", False)),
            dms_blocked=bool(spec.get("dms_blocked", False)),
        )
    ids = [u.id for u in users.values()]
    if len(ids) != len(set(ids)):
        raise ScenarioError(f"{path.name}: duplicate user ids in 'users'")
    return users


def _parse_expect(raw: object, *, label: str) -> Expectation:
    if raw is None:
        return Expectation()
    if not isinstance(raw, dict):
        raise ScenarioError(f"{label}: 'expect' must be a mapping")
    _allowed = {"error", "reply", "state", "price", "fund", "liquidations"}
    unknown = set(raw) - _allowed
    if unknown:
        raise ScenarioError(f"{label}: unknown expect keys {sorted(unknown)}")

    reply = raw.get("reply") or {}
    if not isinstance(reply, dict):
        raise ScenarioError(f"{label}: expect.reply must be a mapping")
    reply_unknown = set(reply) - {"ephemeral", "contains"}
    if reply_unknown:
        raise ScenarioError(
            f"{label}: unknown expect.reply keys {sorted(reply_unknown)}"
        )
    contains_raw = reply.get("contains", [])
    if isinstance(contains_raw, str):
        contains_raw = [contains_raw]

    return Expectation(
        error=raw.get("error"),
        reply_ephemeral=reply.get("ephemeral"),
        reply_contains=tuple(str(c) for c in contains_raw),
        state=dict(raw.get("state") or {}),
        price=dict(raw.get("price") or {}),
        fund=dict(raw.get("fund") or {}),
        liquidations=raw.get("liquidations"),
    )


def _parse_action(
    raw: object,
    *,
    index: int,
    previous_at: datetime,
    users: dict[str, UserSpec],
    path: Path,
) -> Action:
    label = f"{path.name}: timeline[{index}]"
    if not isinstance(raw, dict):
        raise ScenarioError(f"{label}: must be a mapping")
    if "label" in raw:
        label = f"{path.name}: {raw['label']}"

    kinds = [k for k in ("command", "event", "task") if k in raw]
    if len(kinds) != 1:
        raise ScenarioError(f"{label}: exactly one of command/event/task is required")
    kind = kinds[0]
    name = str(raw[kind])

    valid = {"command": COMMAND_NAMES, "event": EVENT_NAMES, "task": TASK_NAMES}[kind]
    if name not in valid:
        raise ScenarioError(f"{label}: unknown {kind} {name!r}")

    _allowed = {
        "label",
        "at",
        "command",
        "event",
        "task",
        "actor",
        "args",
        "expect",
        "repeat",
    }
    unknown = set(raw) - _allowed
    if unknown:
        raise ScenarioError(f"{label}: unknown keys {sorted(unknown)}")

    actor = raw.get("actor")
    if kind == "command" and actor is None:
        raise ScenarioError(f"{label}: commands require an 'actor'")
    if actor is not None and actor not in users:
        raise ScenarioError(f"{label}: actor {actor!r} not declared in 'users'")

    args = dict(raw.get("args") or {})
    for key in ("user", "target", "author", "reactor", "message_author"):
        if key in args and args[key] not in users:
            raise ScenarioError(f"{label}: args.{key}={args[key]!r} not in 'users'")

    at = (
        _parse_at(raw["at"], previous=previous_at, label=label)
        if "at" in raw
        else previous_at
    )
    if at < previous_at:
        raise ScenarioError(
            f"{label}: 'at' ({at.isoformat()}) is before the previous action "
            f"({previous_at.isoformat()}) — timeline must be monotonic"
        )

    repeat = int(raw.get("repeat", 1))
    if repeat < 1:
        raise ScenarioError(f"{label}: 'repeat' must be >= 1")

    return Action(
        index=index,
        label=str(raw.get("label", f"{kind}:{name}[{index}]")),
        at=at,
        kind=kind,
        name=name,
        actor=str(actor) if actor is not None else None,
        args=args,
        expect=_parse_expect(raw.get("expect"), label=label),
        repeat=repeat,
    )


def load_scenario(path: Path) -> Scenario:
    """Load + validate one scenario YAML file."""
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ScenarioError(f"{path.name}: top level must be a mapping")

    _allowed = {
        "name",
        "description",
        "start_at",
        "guild",
        "users",
        "settings",
        "timeline",
    }
    unknown = set(raw) - _allowed
    if unknown:
        raise ScenarioError(f"{path.name}: unknown top-level keys {sorted(unknown)}")

    for required in ("name", "start_at", "guild", "users", "timeline"):
        if required not in raw:
            raise ScenarioError(f"{path.name}: missing required key {required!r}")

    start_at = _parse_at(
        raw["start_at"], previous=datetime(1970, 1, 1, tzinfo=UTC), label=path.name
    )

    guild = raw["guild"]
    if not isinstance(guild, dict) or "id" not in guild:
        raise ScenarioError(f"{path.name}: 'guild' must be a mapping with an 'id'")

    users = _parse_users(raw["users"], path=path)

    timeline_raw = raw["timeline"]
    if not isinstance(timeline_raw, list) or not timeline_raw:
        raise ScenarioError(f"{path.name}: 'timeline' must be a non-empty list")

    actions: list[Action] = []
    previous_at = start_at
    for index, entry in enumerate(timeline_raw):
        action = _parse_action(
            entry, index=index, previous_at=previous_at, users=users, path=path
        )
        actions.append(action)
        previous_at = action.at

    return Scenario(
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        path=path,
        start_at=start_at,
        guild_id=int(guild["id"]),
        guild_name=str(guild.get("name", "Sim Server")),
        users=users,
        settings_overrides=dict(raw.get("settings") or {}),
        timeline=tuple(actions),
    )
