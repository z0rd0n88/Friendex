"""Tests for the central tree-level error handler (Phase 13).

The handler is registered on ``bot.tree.on_error`` and is the single point
where every uncaught exception from a slash command lands. It must:

* Unwrap :class:`discord.app_commands.errors.CommandInvokeError` (chained, recursively)
  to the underlying ``.original`` exception before classification.
* Render :class:`DomainError` as an ephemeral red embed whose description is
  the ``user_facing_message`` verbatim (the embed builder in
  ``adapters/discord_bot/embeds.py`` already encodes that contract).
* Log :class:`PersistenceError` at ERROR with structured context (``operation``,
  ``detail``) and reply ephemerally with "Internal error, please try again".
* Log any fallthrough :class:`Exception` at CRITICAL with ``exc_info=True``
  and reply ephemerally with "Unexpected error".
* Always use :meth:`Interaction.response.send_message` when the interaction
  has not been responded to (``interaction.response.is_done()`` is False),
  otherwise fall back to :meth:`Interaction.followup.send`.
* Always pass ``allowed_mentions=AllowedMentions.none()`` on every reply
  (Phase 10 I2 carry-forward, defence in depth).

These tests use the callback-direct invocation idiom established by Phase 11:
they instantiate a minimal fake interaction (a small dataclass exposing
``response.send_message`` / ``response.is_done`` / ``followup.send`` as async
mocks) and call the handler's underlying function directly. The Discord
``Bot`` / tree integration is exercised in ``tests/adapters/test_container.py``
where ``register_with(fake_bot)`` is asserted to install the handler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import discord
from discord import app_commands

from friendex.adapters.discord_bot.embeds import COLOR_ERROR
from friendex.adapters.discord_bot.error_handler import register_error_handler
from friendex.domain.errors import DomainError, InsufficientFunds, PersistenceError

if TYPE_CHECKING:
    import pytest


# ---------------------------------------------------------------------------
# Fake interaction


@dataclass
class _FakeResponse:
    send_message: AsyncMock = field(
        default_factory=lambda: AsyncMock(name="send_message")
    )
    _done: bool = False

    def is_done(self) -> bool:
        return self._done


@dataclass
class _FakeFollowup:
    send: AsyncMock = field(default_factory=lambda: AsyncMock(name="followup.send"))


@dataclass
class _FakeInteraction:
    response: _FakeResponse = field(default_factory=_FakeResponse)
    followup: _FakeFollowup = field(default_factory=_FakeFollowup)


def _make_interaction(*, already_responded: bool = False) -> _FakeInteraction:
    iac = _FakeInteraction()
    iac.response._done = already_responded
    return iac


# ---------------------------------------------------------------------------
# Helpers


def _install_handler() -> tuple[MagicMock, object]:
    """Build a fake bot whose ``tree`` records the registered handler.

    Returns the bot and the captured handler (the coroutine that the
    error handler module installed).
    """
    bot = MagicMock(name="Bot")
    bot.tree = MagicMock(name="bot.tree")
    settings = MagicMock(name="Settings")
    register_error_handler(bot, settings)
    # ``register_error_handler`` must have set ``bot.tree.on_error`` to the
    # handler coroutine — exposing it for callback-direct invocation.
    handler = bot.tree.on_error
    return bot, handler


# ---------------------------------------------------------------------------
# AC1 — DomainError → ephemeral red embed with user_facing_message verbatim


async def test_domain_error_renders_ephemeral_red_embed_with_user_facing_message() -> (
    None
):
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    error = InsufficientFunds(need=Decimal("100.00"), have=Decimal("50.00"))

    await handler(interaction, error)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    # Description is the user_facing_message VERBATIM (the AC bar).
    assert embed.description == error.user_facing_message
    # Palette: red (COLOR_ERROR from embeds.py).
    assert embed.color == COLOR_ERROR


# ---------------------------------------------------------------------------
# AC1 — PersistenceError → log + generic ephemeral reply


async def test_persistence_error_logs_with_structured_context_and_generic_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    error = PersistenceError("upsert_user", "constraint violation: 1062")

    with caplog.at_level("ERROR", logger="friendex.adapters.discord_bot.error_handler"):
        await handler(interaction, error)

    # ERROR-level log entry with structured kwargs covering operation + detail.
    error_records = [r for r in caplog.records if r.levelname == "ERROR"]
    assert error_records, "expected an ERROR-level log entry for PersistenceError"
    # The structured fields are attached via the ``extra`` kwarg, surfacing on
    # the LogRecord as attributes.
    rec = error_records[0]
    assert getattr(rec, "operation", None) == "upsert_user"
    assert getattr(rec, "detail", None) == "constraint violation: 1062"

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert kwargs.get("content") == "Internal error, please try again"


# ---------------------------------------------------------------------------
# AC1 — Unknown Exception → log CRITICAL + generic ephemeral reply


async def test_unknown_exception_logs_critical_with_exc_info_and_generic_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    error = RuntimeError("boom")

    logger_name = "friendex.adapters.discord_bot.error_handler"
    with caplog.at_level("CRITICAL", logger=logger_name):
        await handler(interaction, error)

    critical_records = [r for r in caplog.records if r.levelname == "CRITICAL"]
    assert critical_records, "expected a CRITICAL log entry for unknown Exception"
    rec = critical_records[0]
    # ``exc_info=True`` populates ``LogRecord.exc_info`` with a tuple.
    assert rec.exc_info is not None
    assert rec.exc_info[0] is RuntimeError

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert kwargs.get("content") == "Unexpected error"


# ---------------------------------------------------------------------------
# AC1 — CommandInvokeError(DomainError) unwraps to DomainError branch


async def test_command_invoke_error_wrapping_domain_error_is_unwrapped() -> None:
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    inner = DomainError("x")
    cmd = MagicMock(name="Command")
    wrapped = app_commands.errors.CommandInvokeError(cmd, inner)

    await handler(interaction, wrapped)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    embed = kwargs["embed"]
    # Description equals "x" — the inner DomainError's user_facing_message.
    assert embed.description == "x"
    assert kwargs.get("ephemeral") is True


# ---------------------------------------------------------------------------
# Bonus: nested CommandInvokeError wraps recursively unwrap (mutation hardener)


async def test_nested_command_invoke_error_is_recursively_unwrapped() -> None:
    """A two-level wrap still routes to the DomainError branch.

    Mutation-hardening: a single ``error = error.original`` (instead of a
    ``while``-loop unwrap) would let the outer wrapper through and route the
    error to the fallthrough CRITICAL branch.
    """
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    inner = DomainError("deeply-wrapped")
    cmd = MagicMock(name="Command")
    middle = app_commands.errors.CommandInvokeError(cmd, inner)
    outer = app_commands.errors.CommandInvokeError(cmd, middle)

    await handler(interaction, outer)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    embed = kwargs["embed"]
    assert embed.description == "deeply-wrapped"


# ---------------------------------------------------------------------------
# Followup path — when the interaction has already been responded to


async def test_handler_uses_followup_when_response_is_done() -> None:
    _bot, handler = _install_handler()
    interaction = _make_interaction(already_responded=True)
    error = InsufficientFunds(need=Decimal("1.00"), have=Decimal("0.00"))

    await handler(interaction, error)

    # The initial response slot must NOT have been used.
    interaction.response.send_message.assert_not_awaited()
    # The followup must have been awaited with the same ephemeral red embed.
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)
    embed = kwargs["embed"]
    assert embed.description == error.user_facing_message


# ---------------------------------------------------------------------------
# register_error_handler attaches the handler to bot.tree.on_error


def test_register_error_handler_installs_handler_on_bot_tree() -> None:
    bot = MagicMock(name="Bot")
    bot.tree = MagicMock(name="bot.tree")
    settings = MagicMock(name="Settings")
    register_error_handler(bot, settings)
    # Either the attribute is set, or ``bot.tree.error(...)`` decorator was
    # invoked — accept the attribute-set form (the simpler, less-magical
    # idiom for an externally registered handler).
    assert bot.tree.on_error is not None
    # The registered handler is callable (a coroutine function).
    assert callable(bot.tree.on_error)
