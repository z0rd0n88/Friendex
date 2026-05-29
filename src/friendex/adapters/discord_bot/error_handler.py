"""Central slash-command error handler (Phase 13).

This module owns the **single** error-routing seam for every slash command
in the bot. It is installed on ``bot.tree.on_error`` by
:func:`register_error_handler`; cogs and listeners deliberately do **not**
``try/except DomainError`` (see Phase 11/12 digests) so every uncaught
exception lands here.

Behaviour, in classification order:

* :class:`discord.app_commands.errors.CheckFailure` — discord.py dispatches
  permission-check failures (``has_permissions``, ``has_role``, custom
  ``app_commands.check`` decorators) without wrapping them in a
  ``CommandInvokeError``, so the branch sits BEFORE the unwrap loop. Reply
  ephemerally with a fixed user-facing permission-denied message; do NOT
  log at CRITICAL — a denied permission is a routine outcome, not an
  operator-visible incident.
* If the raised exception is wrapped in one or more
  :class:`discord.app_commands.errors.CommandInvokeError` layers, unwrap
  recursively to ``.original`` before classification. Discord wraps every
  exception raised from a slash callback in a single
  ``CommandInvokeError``; nested wraps are rare but possible (e.g. when a
  cog's own decorator re-raises). One ``while``-loop unwrap covers both.
* After unwrapping, a second :class:`CheckFailure` test catches the
  defence-in-depth case where a custom decorator (or a future discord.py
  release) raises ``CommandInvokeError(CheckFailure(...))``. The unwrapped
  inner exception is still a routine permission denial; without this
  follow-up branch it would fall through to the CRITICAL "Unexpected
  error" path. Reviewer's MEDIUM-1 hardener.
* :class:`DomainError` → ephemeral red embed whose ``description`` is the
  ``user_facing_message`` verbatim (palette pinned via
  :data:`friendex.adapters.discord_bot.embeds.COLOR_ERROR`). The user sees
  exactly the message the game-rule violation carried.
* :class:`PersistenceError` → log at ERROR with the carried
  ``operation`` + ``detail`` attached as structured fields (so JSON-mode
  log sinks can index them), then reply ephemerally with a fixed
  ``"Internal error, please try again"``. The user never sees database
  internals.
* Any other :class:`Exception` → log at CRITICAL with ``exc_info=True``
  (full traceback for the operator), then reply ephemerally with
  ``"Unexpected error"``.

Every reply passes ``allowed_mentions=AllowedMentions.none()`` — defence
in depth against any future reply that incorporates user-supplied text
(today's replies are canned strings + the ``DomainError.user_facing_message``
which is constructed from validated game state, so this is belt-and-braces).

The handler picks the right send mechanism:

* If ``interaction.response.is_done()`` is False (the slash callback has
  not yet replied), the handler uses ``interaction.response.send_message``.
* Otherwise the callback already replied (or deferred), and the handler
  uses ``interaction.followup.send`` — Discord rejects a second
  ``response.send_message`` call.

Prefix-command branches (``commands.MissingRequiredArgument``,
``commands.MemberNotFound``) are deliberately omitted: this bot is
slash-only, so those paths are unreachable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
import structlog
from discord import app_commands

from friendex.adapters.discord_bot.embeds import build_error_embed
from friendex.domain.errors import DomainError, PersistenceError

if TYPE_CHECKING:
    from discord.ext import commands

    from friendex.adapters.config import Settings

# ``structlog.get_logger`` is the project-standard adapter API (Phase 13
# digest §logging). The pre-fix module used ``logging.getLogger(__name__)``
# and passed structured fields via the stdlib ``extra={}`` kwarg — those
# fields are silently dropped by the JSON renderer in production. structlog
# accepts keyword arguments natively, so ``log.error("event", k=v)`` round-
# trips through the processor chain configured in ``adapters/config.py``.
logger = structlog.get_logger(__name__)


_GENERIC_PERSISTENCE_REPLY = "Internal error, please try again"
_GENERIC_UNEXPECTED_REPLY = "Unexpected error"
_GENERIC_CHECK_FAILURE_REPLY = "You don't have permission to use that command."


def _unwrap(error: BaseException) -> BaseException:
    """Recursively peel ``CommandInvokeError`` layers off ``error``.

    discord.py wraps every callback exception in
    :class:`app_commands.errors.CommandInvokeError`; the underlying error
    lives on ``.original``. Multiple wraps can occur when a cog's own
    decorator re-raises — a single-shot unwrap would miss the inner cause,
    so we loop until ``error`` is no longer a ``CommandInvokeError``.
    """
    while isinstance(error, app_commands.errors.CommandInvokeError):
        error = error.original
    return error


async def _reply_embed(
    interaction: discord.Interaction,
    embed: discord.Embed,
) -> None:
    """Send an ephemeral embed reply, choosing initial-response vs followup."""
    allowed = discord.AllowedMentions.none()
    if interaction.response.is_done():
        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
            allowed_mentions=allowed,
        )
    else:
        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
            allowed_mentions=allowed,
        )


async def _reply_content(
    interaction: discord.Interaction,
    content: str,
) -> None:
    """Send an ephemeral content-only reply, choosing initial-response vs followup."""
    allowed = discord.AllowedMentions.none()
    if interaction.response.is_done():
        await interaction.followup.send(
            content=content,
            ephemeral=True,
            allowed_mentions=allowed,
        )
    else:
        await interaction.response.send_message(
            content=content,
            ephemeral=True,
            allowed_mentions=allowed,
        )


def register_error_handler(
    bot: commands.Bot,
    settings: Settings,
) -> None:
    """Install the central error handler on ``bot.tree.on_error``.

    ``settings`` is accepted but unused today — it is reserved for Phase 14+
    log-channel routing (operator-facing alert sink, e.g. a configured
    Discord channel ID). The argument is kept rather than dropped because the
    call site in ``adapters/container.py`` is out-of-scope for the
    silent-failures branch (issue #82 L3): removing it would cross the
    wave-2 ownership boundary. The ``del`` makes the intentional discard
    explicit so static analysis treats the parameter as consumed.
    """
    del settings  # reserved for future tunables (e.g. log-channel routing)

    async def on_tree_error(
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        """Single error-routing entry point for every slash command."""
        # ``CheckFailure`` covers permission checks (``has_permissions``,
        # ``has_role``, custom checks). discord.py dispatches these without
        # wrapping in ``CommandInvokeError``, so the branch sits BEFORE the
        # unwrap loop. Reply ephemerally with a user-facing message; do NOT
        # log at CRITICAL — a denied permission is a routine outcome, not an
        # operator-visible incident.
        if isinstance(error, app_commands.CheckFailure):
            await _reply_content(interaction, _GENERIC_CHECK_FAILURE_REPLY)
            return

        unwrapped = _unwrap(error)

        # Defence in depth (Wave 1 review MEDIUM-1): a custom decorator (or
        # a future discord.py release) may raise
        # ``CommandInvokeError(CheckFailure(...))``. After the unwrap loop,
        # re-check for ``CheckFailure`` so a wrapped routine permission
        # denial still routes to the friendly ephemeral reply instead of
        # the CRITICAL "Unexpected error" fallthrough.
        if isinstance(unwrapped, app_commands.CheckFailure):
            await _reply_content(interaction, _GENERIC_CHECK_FAILURE_REPLY)
            return

        if isinstance(unwrapped, DomainError):
            await _reply_embed(interaction, build_error_embed(unwrapped))
            return

        if isinstance(unwrapped, PersistenceError):
            # structlog accepts the structured fields as keyword arguments —
            # the JSON renderer in the production processor chain emits them
            # as top-level keys alongside ``event``. The pre-fix call passed
            # them via stdlib ``extra={}`` to ``logging.getLogger``; that
            # adapter shape silently drops the kwargs at the JSON sink.
            logger.error(
                "persistence_error",
                operation=unwrapped.operation,
                detail=unwrapped.detail,
            )
            await _reply_content(interaction, _GENERIC_PERSISTENCE_REPLY)
            return

        # Fallthrough: unknown Exception — log full traceback at CRITICAL.
        # Pass the explicit exception tuple (rather than the bare
        # ``exc_info=True`` sentinel) so the traceback survives even when
        # the handler is invoked outside an ``except:`` block — e.g. when
        # discord.py dispatches the error coroutine after the original
        # frame has unwound and ``sys.exc_info()`` is empty. structlog's
        # ``ExceptionRenderer`` formats the tuple downstream.
        logger.critical(
            "unexpected_error",
            exc_info=(type(unwrapped), unwrapped, unwrapped.__traceback__),
        )
        await _reply_content(interaction, _GENERIC_UNEXPECTED_REPLY)

    # discord.py's CommandTree.on_error is a regular method on the class; the
    # sanctioned override pattern is direct attribute assignment (the docs
    # show this idiom). Mypy flags method-assign for the type narrowing —
    # silence it locally since the assignment is the intended customization
    # seam.
    bot.tree.on_error = on_tree_error  # type: ignore[method-assign]
