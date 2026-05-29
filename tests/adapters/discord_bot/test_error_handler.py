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
from unittest.mock import AsyncMock, MagicMock

import discord
import structlog
from discord import app_commands

from friendex.adapters.discord_bot.embeds import COLOR_ERROR
from friendex.adapters.discord_bot.error_handler import register_error_handler
from friendex.domain.errors import DomainError, InsufficientFunds, PersistenceError

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


async def test_persistence_error_logs_with_structured_context_and_generic_reply() -> (
    None
):
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    error = PersistenceError("upsert_user", "constraint violation: 1062")

    with structlog.testing.capture_logs() as captured:
        await handler(interaction, error)

    # Structured log entry: structlog routes ``log.error("event", k=v)`` into
    # a dict carrying both ``event`` and the keyword arguments. With
    # ``stdlib.logging.getLogger`` + ``extra={...}`` (the pre-fix call shape)
    # those kwargs are silently dropped from the JSON renderer.
    error_records = [r for r in captured if r["log_level"] == "error"]
    assert error_records, "expected an ERROR-level log entry for PersistenceError"
    rec = error_records[0]
    assert rec["operation"] == "upsert_user"
    assert rec["detail"] == "constraint violation: 1062"

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert kwargs.get("content") == "Internal error, please try again"


# ---------------------------------------------------------------------------
# AC1 — Unknown Exception → log CRITICAL + generic ephemeral reply


async def test_unknown_exception_logs_critical_with_exc_info_and_generic_reply() -> (
    None
):
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    error = RuntimeError("boom")

    with structlog.testing.capture_logs() as captured:
        await handler(interaction, error)

    critical_records = [r for r in captured if r["log_level"] == "critical"]
    assert critical_records, "expected a CRITICAL log entry for unknown Exception"
    rec = critical_records[0]
    # structlog forwards ``exc_info=(type, value, tb)`` as ``exc_info`` in the
    # captured dict so downstream renderers (``ExceptionRenderer``) can pick it
    # up. The shape mirrors the stdlib ``LogRecord.exc_info`` tuple so the
    # traceback is preserved even when the handler is invoked after the
    # original frame has unwound.
    assert rec.get("exc_info") is not None
    exc_info = rec["exc_info"]
    if isinstance(exc_info, tuple):
        assert exc_info[0] is RuntimeError
    else:
        assert isinstance(exc_info, RuntimeError)

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


# ---------------------------------------------------------------------------
# CheckFailure branch (Wave 1, issue #84 C) — must reply ephemerally and NOT
# log at CRITICAL or fall through to the "Unexpected error" path.


async def test_check_failure_replies_ephemerally_and_does_not_log_critical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``app_commands.CheckFailure`` (e.g. ``has_permissions`` deny) gets a
    friendly ephemeral "you don't have permission" reply, and the handler
    must NOT escalate it to the unexpected-error CRITICAL log.

    Mutation-hardening: a regression that drops the CheckFailure branch
    would route this through the fallthrough Exception branch — the test
    asserts both the ephemeral content shape AND the absence of any
    CRITICAL record on the error_handler logger.
    """
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    # ``CheckFailure`` is the base of permission-denials and custom checks.
    error = app_commands.errors.CheckFailure("missing manage_guild")

    logger_name = "friendex.adapters.discord_bot.error_handler"
    with caplog.at_level("DEBUG", logger=logger_name):
        await handler(interaction, error)

    # The reply went out ephemerally (no embed — content-only is fine, the
    # CheckFailure surface is plain text).
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)
    content = kwargs.get("content", "")
    # The content carries a user-facing permission denial — not internal state.
    assert "permission" in content.lower()

    # Absence of CRITICAL on the handler logger is the mutation guard:
    # the regression sends this to the CRITICAL-with-exc_info fallthrough.
    critical = [
        r for r in caplog.records if r.name == logger_name and r.levelname == "CRITICAL"
    ]
    assert critical == [], (
        f"CheckFailure must not log at CRITICAL; got {[r.message for r in critical]!r}"
    )


async def test_check_failure_branch_runs_before_unwrap_loop() -> None:
    """A direct ``CheckFailure`` (not wrapped in ``CommandInvokeError``) is
    classified before the unwrap loop touches it.

    discord.py dispatches check failures without a ``CommandInvokeError``
    wrap — the handler must accept the bare type directly.
    """
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    error = app_commands.errors.CheckFailure("not allowed")

    await handler(interaction, error)

    interaction.response.send_message.assert_awaited_once()


async def test_check_failure_uses_followup_when_response_done() -> None:
    """Followup path mirrors the other branches: ephemeral, allowed_mentions.none()."""
    _bot, handler = _install_handler()
    interaction = _make_interaction(already_responded=True)
    error = app_commands.errors.CheckFailure("permission denied")

    await handler(interaction, error)

    interaction.response.send_message.assert_not_awaited()
    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)


async def test_wrapped_check_failure_replies_ephemerally_and_does_not_log_critical(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A ``CommandInvokeError`` wrapping a ``CheckFailure`` still routes friendly.

    Wave 1 review MEDIUM-1: today discord.py dispatches bare ``CheckFailure``
    (so the pre-unwrap branch catches it), but a custom decorator (or a
    future discord.py version) can raise ``CommandInvokeError(CheckFailure)``.
    The handler must classify the *unwrapped* exception too — otherwise the
    fallthrough sends the routine permission-denial through the
    "Unexpected error" CRITICAL path.

    Mutation-hardener: without the post-unwrap ``isinstance(unwrapped,
    CheckFailure)`` check, ``unwrapped`` is a ``CheckFailure``, but it does
    not match ``DomainError`` or ``PersistenceError``, so it falls through
    and logs at CRITICAL.
    """
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    inner = app_commands.errors.CheckFailure("missing manage_guild")
    cmd = MagicMock(name="Command")
    wrapped = app_commands.errors.CommandInvokeError(cmd, inner)

    logger_name = "friendex.adapters.discord_bot.error_handler"
    with caplog.at_level("DEBUG", logger=logger_name):
        await handler(interaction, wrapped)

    # The friendly ephemeral reply must have been sent.
    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    assert isinstance(kwargs.get("allowed_mentions"), discord.AllowedMentions)
    content = kwargs.get("content", "")
    assert "permission" in content.lower()

    # And the handler must NOT have escalated to the CRITICAL fallthrough.
    critical = [
        r for r in caplog.records if r.name == logger_name and r.levelname == "CRITICAL"
    ]
    messages = [r.message for r in critical]
    assert critical == [], (
        f"Wrapped CheckFailure must not log at CRITICAL; got {messages!r}"
    )


async def test_nested_wrapped_check_failure_unwraps_recursively_to_friendly_reply(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Two-level ``CommandInvokeError`` wraps a ``CheckFailure``.

    Mutation-hardener pair to the unwrap loop test above: confirms the
    post-unwrap ``CheckFailure`` check sees the result of the *recursive*
    unwrap, not just one peel.
    """
    _bot, handler = _install_handler()
    interaction = _make_interaction()
    inner = app_commands.errors.CheckFailure("permission denied")
    cmd = MagicMock(name="Command")
    middle = app_commands.errors.CommandInvokeError(cmd, inner)
    outer = app_commands.errors.CommandInvokeError(cmd, middle)

    logger_name = "friendex.adapters.discord_bot.error_handler"
    with caplog.at_level("DEBUG", logger=logger_name):
        await handler(interaction, outer)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs.get("ephemeral") is True
    content = kwargs.get("content", "")
    assert "permission" in content.lower()

    critical = [
        r for r in caplog.records if r.name == logger_name and r.levelname == "CRITICAL"
    ]
    messages = [r.message for r in critical]
    assert critical == [], (
        f"Nested wrapped CheckFailure must not log at CRITICAL; got {messages!r}"
    )
