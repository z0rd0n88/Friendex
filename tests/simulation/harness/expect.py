"""Expectation engine: assert config-declared outcomes after each action.

Matchers accept either a scalar (exact equality; money compared as
:class:`~decimal.Decimal`) or a mapping of comparison operators::

    cash: "10400.00"           # exact Decimal equality
    price: {gt: "100"}         # comparison
    balance: {approx: "1150", tol: "0.05"}   # tol is a *ratio* of expected

Every assertion failure raises :class:`SimulationAssertionFailure` carrying
the scenario + action label so a failed run pinpoints its timeline entry.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from tests.simulation.harness.actions import ActionOutcome
    from tests.simulation.harness.schema import Action, Expectation
    from tests.simulation.harness.world import SimWorld


class SimulationAssertionFailure(AssertionError):
    """An expectation declared in the scenario config was not met."""


_OPS = frozenset({"eq", "ne", "gt", "ge", "lt", "le", "approx", "tol"})


def _coerce(expected: object, actual: object) -> object:
    """Coerce the YAML-side expected value to the actual value's type."""
    if isinstance(actual, Decimal):
        return Decimal(str(expected))
    if isinstance(actual, bool):
        return bool(expected)
    if isinstance(actual, float):
        return float(str(expected))
    if isinstance(actual, int) and not isinstance(expected, bool):
        return int(str(expected))
    return expected


def check_matcher(actual: object, matcher: object, *, where: str) -> None:
    """Assert ``actual`` satisfies ``matcher`` (scalar or operator mapping)."""
    if not isinstance(matcher, dict):
        expected = _coerce(matcher, actual)
        if actual != expected:
            raise SimulationAssertionFailure(
                f"{where}: expected {expected!r}, got {actual!r}"
            )
        return

    unknown = set(matcher) - _OPS
    if unknown:
        raise SimulationAssertionFailure(
            f"{where}: unknown matcher ops {sorted(unknown)}"
        )

    checks: list[tuple[str, bool]] = []
    for op in ("eq", "ne", "gt", "ge", "lt", "le"):
        if op in matcher:
            expected = _coerce(matcher[op], actual)
            result = {
                "eq": actual == expected,
                "ne": actual != expected,
                "gt": actual > expected,  # type: ignore[operator]
                "ge": actual >= expected,  # type: ignore[operator]
                "lt": actual < expected,  # type: ignore[operator]
                "le": actual <= expected,  # type: ignore[operator]
            }[op]
            checks.append((f"{op} {expected!r}", result))
    if "approx" in matcher:
        expected_dec = Decimal(str(matcher["approx"]))
        tol_ratio = Decimal(str(matcher.get("tol", "0.01")))
        actual_dec = Decimal(str(actual))
        within = abs(actual_dec - expected_dec) <= abs(expected_dec) * tol_ratio
        checks.append((f"approx {expected_dec} ±{tol_ratio:%}", within))

    failed = [desc for desc, ok in checks if not ok]
    if failed:
        raise SimulationAssertionFailure(
            f"{where}: got {actual!r}, failed checks: {', '.join(failed)}"
        )


# ---------------------------------------------------------------------------
# Reply capture


def _embed_text(embed: discord.Embed) -> str:
    data = embed.to_dict()
    parts: list[str] = [str(data.get("title", "")), str(data.get("description", ""))]
    for f in data.get("fields", []):
        parts.append(str(f.get("name", "")))
        parts.append(str(f.get("value", "")))
    return "\n".join(parts)


def collect_replies(interaction: MagicMock) -> list[tuple[str, bool]]:
    """Extract ``(text, ephemeral)`` for every reply the action produced."""
    replies: list[tuple[str, bool]] = []
    for mock in (interaction.response.send_message, interaction.followup.send):
        for call in mock.await_args_list:
            kwargs = call.kwargs
            texts: list[str] = []
            if kwargs.get("content"):
                texts.append(str(kwargs["content"]))
            if call.args and isinstance(call.args[0], str):
                texts.append(call.args[0])
            embeds: list[discord.Embed] = []
            if isinstance(kwargs.get("embed"), discord.Embed):
                embeds.append(kwargs["embed"])
            embeds.extend(
                e for e in kwargs.get("embeds", []) if isinstance(e, discord.Embed)
            )
            texts.extend(_embed_text(e) for e in embeds)
            replies.append(("\n".join(texts), bool(kwargs.get("ephemeral", False))))
    return replies


# ---------------------------------------------------------------------------
# Top-level assertion entry point


def _check_error(
    expectation: Expectation,
    outcome: ActionOutcome,
    *,
    where: str,
) -> None:
    error = outcome.error
    expected = expectation.error
    if expected is None:
        if error is not None:
            raise SimulationAssertionFailure(
                f"{where}: expected success but got {type(error).__name__}: {error}"
            ) from error
        return
    if error is None:
        raise SimulationAssertionFailure(
            f"{where}: expected error {expected!r} but the action succeeded"
        )
    if expected == "CheckFailure":
        if not isinstance(error, app_commands.CheckFailure):
            raise SimulationAssertionFailure(
                f"{where}: expected a CheckFailure, got {type(error).__name__}"
            )
    elif type(error).__name__ != expected:
        raise SimulationAssertionFailure(
            f"{where}: expected error {expected!r}, got {type(error).__name__}: {error}"
        )
    # The handler must have *rendered* a reply — the whole point of routing
    # errors through the production error handler.
    if outcome.interaction is not None and not collect_replies(outcome.interaction):
        raise SimulationAssertionFailure(
            f"{where}: error {expected!r} raised but no reply was rendered "
            "by the central error handler"
        )


def _check_reply(
    expectation: Expectation,
    outcome: ActionOutcome,
    *,
    where: str,
) -> None:
    if expectation.reply_ephemeral is None and not expectation.reply_contains:
        return
    if outcome.interaction is None:
        raise SimulationAssertionFailure(
            f"{where}: reply expectations require a command (or synthetic) action"
        )
    replies = collect_replies(outcome.interaction)
    if not replies:
        raise SimulationAssertionFailure(f"{where}: no reply was sent")
    if expectation.reply_ephemeral is not None:
        last_ephemeral = replies[-1][1]
        if last_ephemeral != expectation.reply_ephemeral:
            raise SimulationAssertionFailure(
                f"{where}: expected ephemeral={expectation.reply_ephemeral}, "
                f"got ephemeral={last_ephemeral}"
            )
    all_text = "\n".join(text for text, _ in replies)
    for needle in expectation.reply_contains:
        if needle not in all_text:
            raise SimulationAssertionFailure(
                f"{where}: reply does not contain {needle!r}; replies were:\n{all_text}"
            )


_BUCKET_FIELDS = frozenset(
    {
        "text_msgs",
        "media_msgs",
        "voice_minutes",
        "reaction_count",
        "reply_count",
        "role_ping_joins",
        "role_ping_join_minutes",
    }
)


async def _check_user_state(
    world: SimWorld,
    user_name: str,
    checks: dict[str, Any],
    *,
    where: str,
) -> None:
    account = await world.container._user_repo.get(
        world.guild_id, world.user_id(user_name)
    )
    if "exists" in checks:
        expected_exists = bool(checks["exists"])
        if (account is not None) != expected_exists:
            raise SimulationAssertionFailure(
                f"{where}: {user_name} exists={account is not None}, "
                f"expected {expected_exists}"
            )
        checks = {k: v for k, v in checks.items() if k != "exists"}
        if not expected_exists:
            if checks:
                raise SimulationAssertionFailure(
                    f"{where}: cannot check fields on non-existent {user_name}"
                )
            return
    if account is None:
        raise SimulationAssertionFailure(
            f"{where}: no account for {user_name} (declare seed or run an action first)"
        )

    for key, matcher in checks.items():
        loc = f"{where}: state.{user_name}.{key}"
        if key == "cash":
            check_matcher(account.cash_balance, matcher, where=loc)
        elif key == "net_worth":
            check_matcher(account.net_worth, matcher, where=loc)
        elif key == "streak":
            check_matcher(account.daily.streak, matcher, where=loc)
        elif key == "opt_in":
            check_matcher(account.opt_in, matcher, where=loc)
        elif key == "long":
            for target_name, shares_matcher in dict(matcher).items():
                target_id = world.user_id(str(target_name))
                position = account.long_positions.get(target_id)
                shares = 0 if position is None else position.shares
                check_matcher(shares, shares_matcher, where=f"{loc}.{target_name}")
        elif key == "short":
            for target_name, shares_matcher in dict(matcher).items():
                target_id = world.user_id(str(target_name))
                short = account.short_positions.get(target_id)
                shares = 0 if short is None else short.shares
                check_matcher(shares, shares_matcher, where=f"{loc}.{target_name}")
        elif key == "short_frozen":
            for target_name, frozen_matcher in dict(matcher).items():
                target_id = world.user_id(str(target_name))
                short = account.short_positions.get(target_id)
                if short is None:
                    raise SimulationAssertionFailure(
                        f"{loc}.{target_name}: no short position exists"
                    )
                check_matcher(
                    short.frozen, frozen_matcher, where=f"{loc}.{target_name}"
                )
        elif key in ("today", "week"):
            bucket = account.today if key == "today" else account.week
            for bucket_field, bucket_matcher in dict(matcher).items():
                if bucket_field not in _BUCKET_FIELDS:
                    raise SimulationAssertionFailure(
                        f"{loc}.{bucket_field}: unknown activity-bucket field"
                    )
                check_matcher(
                    getattr(bucket, bucket_field),
                    bucket_matcher,
                    where=f"{loc}.{bucket_field}",
                )
        else:
            raise SimulationAssertionFailure(f"{loc}: unknown state field")


async def _check_price(
    world: SimWorld,
    user_name: str,
    matcher: object,
    *,
    where: str,
) -> None:
    stock = await world.container._price_repo.get(
        world.guild_id, world.user_id(user_name)
    )
    if stock is None:
        raise SimulationAssertionFailure(
            f"{where}: price.{user_name}: no stock exists yet"
        )
    check_matcher(stock.current, matcher, where=f"{where}: price.{user_name}")


async def _check_fund(
    world: SimWorld,
    user_name: str,
    checks: dict[str, Any],
    *,
    where: str,
) -> None:
    # "events_wallet" addresses the per-guild treasury pseudo-fund directly;
    # every other key is a declared user name (fund_id == manager user_id).
    fund_id = user_name if user_name == "events_wallet" else world.user_id(user_name)
    fund = await world.container._fund_repo.get(world.guild_id, fund_id)
    loc = f"{where}: fund.{user_name}"
    if "exists" in checks:
        expected_exists = bool(checks["exists"])
        if (fund is not None) != expected_exists:
            raise SimulationAssertionFailure(
                f"{loc}: exists={fund is not None}, expected {expected_exists}"
            )
        if not expected_exists:
            return
    if fund is None:
        raise SimulationAssertionFailure(f"{loc}: fund does not exist")
    for key, matcher in checks.items():
        if key == "exists":
            continue
        if key == "balance":
            check_matcher(fund.cash_balance, matcher, where=f"{loc}.balance")
        elif key == "name":
            check_matcher(fund.name, matcher, where=f"{loc}.name")
        elif key == "investors":
            for investor_name, amount_matcher in dict(matcher).items():
                investor_id = world.user_id(str(investor_name))
                amount = fund.investors.get(investor_id, Decimal("0"))
                check_matcher(
                    amount, amount_matcher, where=f"{loc}.investors.{investor_name}"
                )
        else:
            raise SimulationAssertionFailure(f"{loc}.{key}: unknown fund field")


async def check_expectation(
    world: SimWorld,
    action: Action,
    outcome: ActionOutcome,
) -> None:
    """Assert every expectation declared on ``action`` against the world."""
    expectation = action.expect
    where = f"[{world.scenario.name}] {action.label}"

    _check_error(expectation, outcome, where=where)
    _check_reply(expectation, outcome, where=where)

    if expectation.liquidations is not None:
        new_events = len(world.liquidation_events) - outcome.liquidations_before
        if new_events != expectation.liquidations:
            raise SimulationAssertionFailure(
                f"{where}: expected {expectation.liquidations} liquidation "
                f"event(s), got {new_events}"
            )

    for user_name, checks in expectation.state.items():
        await _check_user_state(world, user_name, dict(checks), where=where)
    for user_name, matcher in expectation.price.items():
        await _check_price(world, user_name, matcher, where=where)
    for user_name, checks in expectation.fund.items():
        await _check_fund(world, user_name, dict(checks), where=where)
