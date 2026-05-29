"""Tests for :class:`AdminCog` — ``/help`` and ``/game_intro``.

``/help`` is ephemeral; ``/game_intro`` is public and decorated with
``@app_commands.checks.has_permissions(manage_guild=True)``. Tests assert
the permission check is attached to the ``/game_intro`` command.
"""

from __future__ import annotations

import discord
from discord import app_commands

from friendex.adapters.discord_bot.cogs.admin_cog import AdminCog
from friendex.adapters.discord_bot.embeds import COLOR_INFO


def _send_call_kwargs(interaction) -> dict:  # type: ignore[no-untyped-def]
    """Return the kwargs of the last user-visible reply.

    Wave 1 (#82 H13) routed cog replies through ``followup.send`` after a
    ``response.defer(...)``.
    """
    assert interaction.followup.send.await_count >= 1
    return interaction.followup.send.await_args.kwargs


# ---------------------------------------------------------------------------
# /help


async def test_help_replies_ephemerally_with_help_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
) -> None:
    cog = AdminCog()
    interaction = fake_interaction()
    await AdminCog.help.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    assert kwargs.get("ephemeral") is True  # mutation-hardening
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_INFO.value
    title = data.get("title") or ""
    assert "command" in title.lower()


# ---------------------------------------------------------------------------
# /game_intro — manage_guild permission + public reply


async def test_game_intro_replies_publicly_with_intro_embed(
    fake_interaction,  # type: ignore[no-untyped-def]
) -> None:
    cog = AdminCog()
    interaction = fake_interaction()
    await AdminCog.game_intro.callback(cog, interaction)
    kwargs = _send_call_kwargs(interaction)
    # Mutation-hardening: ``/game_intro`` is public — ephemeral must be
    # False or unset (discord.py defaults to False).
    assert kwargs.get("ephemeral", False) is False
    embed = kwargs["embed"]
    assert isinstance(embed, discord.Embed)
    data = embed.to_dict()
    assert data["color"] == COLOR_INFO.value
    title = data.get("title") or ""
    assert "welcome" in title.lower()


def test_game_intro_has_manage_guild_permission_check() -> None:
    """The ``/game_intro`` command must carry a ``has_permissions`` check.

    ``app_commands.checks.has_permissions`` installs its predicate into the
    command's ``checks`` list. Removing the decorator drops the check, so
    this test fails if the permission guard is dropped — mutation-hardening
    per the work-unit spec.
    """
    command = AdminCog.game_intro
    assert isinstance(command, app_commands.Command)
    assert len(command.checks) >= 1, (
        "/game_intro must carry at least one permission check"
    )
    # The check predicate from ``has_permissions(manage_guild=True)`` is a
    # closure named ``predicate``; exercise it with a stub interaction to
    # confirm the check actually demands manage_guild.

    class _Perms:
        def __init__(self, *, manage_guild: bool) -> None:
            self.manage_guild = manage_guild

    class _Interaction:
        def __init__(self, *, manage_guild: bool) -> None:
            self.permissions = _Perms(manage_guild=manage_guild)

    # With manage_guild=True the check returns True.
    assert command.checks[0](_Interaction(manage_guild=True)) is True
    # With manage_guild=False the check raises MissingPermissions —
    # confirming the guard is wired and load-bearing.
    import pytest

    with pytest.raises(app_commands.MissingPermissions):
        command.checks[0](_Interaction(manage_guild=False))


# ---------------------------------------------------------------------------
# Slash-command registration sanity


def test_admin_cog_registers_help_and_game_intro_app_commands() -> None:
    assert isinstance(AdminCog.help, app_commands.Command)
    assert isinstance(AdminCog.game_intro, app_commands.Command)


# ---------------------------------------------------------------------------
# Wave 1 contracts


async def test_help_defers_ephemerally(
    fake_interaction,  # type: ignore[no-untyped-def]
) -> None:
    """``/help`` is ephemeral — defer with ``ephemeral=True``."""
    cog = AdminCog()
    interaction = fake_interaction()

    await AdminCog.help.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=True)


async def test_game_intro_defers_publicly(
    fake_interaction,  # type: ignore[no-untyped-def]
) -> None:
    """``/game_intro`` is public — defer with ``ephemeral=False``."""
    cog = AdminCog()
    interaction = fake_interaction()

    await AdminCog.game_intro.callback(cog, interaction)

    interaction.response.defer.assert_awaited_once_with(ephemeral=False)


def test_admin_commands_are_guild_only() -> None:
    """Wave 1 (#82 H14): both admin commands refuse DM dispatch."""
    for cmd in (AdminCog.help, AdminCog.game_intro):
        assert getattr(cmd, "guild_only", None) is True, (
            f"{cmd.name}: must be decorated @app_commands.guild_only()"
        )
