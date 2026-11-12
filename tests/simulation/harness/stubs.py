"""Stub discord.py objects for the simulation.

Same idiom as ``tests/integration/test_full_command_flow.py`` and the cog /
listener conftests: hand-rolled ``MagicMock``/``AsyncMock`` stubs carrying
exactly the attributes the production code reads. dpytest is deliberately
not used (slash-only bot — see the integration module docstring).

The one behavioural upgrade over the existing stubs: ``response.is_done()``
tracks whether ``defer``/``send_message`` ran, because the central error
handler picks initial-response vs followup off that flag and the simulation
routes real errors through that handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import discord

if TYPE_CHECKING:
    from tests.simulation.harness.schema import UserSpec


class _EmptyAsyncIterator:
    """Empty async iterator — stands in for ``guild.audit_logs(...)``.

    An empty audit log makes the member listener's moderator resolution
    fall back to its ``"unknown"`` sentinel, which is exactly the
    degraded-permission path worth exercising.
    """

    def __aiter__(self) -> _EmptyAsyncIterator:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


def make_guild(guild_id: int, name: str) -> MagicMock:
    guild = MagicMock(name=f"Guild:{name}", spec=discord.Guild)
    guild.id = guild_id
    guild.name = name
    guild.audit_logs = MagicMock(return_value=_EmptyAsyncIterator())
    guild.system_channel = None
    return guild


def make_member(spec: UserSpec, guild: MagicMock) -> MagicMock:
    """Stub ``discord.Member`` for one simulated user."""
    member = MagicMock(name=f"Member:{spec.name}", spec=discord.Member)
    member.id = spec.id
    member.bot = False
    member.guild = guild
    member.display_name = spec.name
    member.mention = f"<@{spec.id}>"
    member.guild_permissions.manage_guild = spec.manage_guild
    member.timed_out_until = None
    member.voice = None
    if spec.dms_blocked:
        member.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(), "DMs closed")
        )
    else:
        member.send = AsyncMock(name=f"Member:{spec.name}.send")
    return member


def make_interaction(member: MagicMock, guild: MagicMock) -> MagicMock:
    """Stub ``discord.Interaction`` with a live ``is_done`` flag.

    ``defer`` / ``send_message`` flip the flag exactly like the real
    ``InteractionResponse``, so the central error handler's
    initial-response-vs-followup branch behaves as in production.
    """
    interaction = MagicMock(name="Interaction")
    interaction.user = member
    interaction.guild = guild
    done = {"value": False}

    async def _defer(*args: Any, **kwargs: Any) -> None:
        done["value"] = True

    async def _send_message(*args: Any, **kwargs: Any) -> None:
        done["value"] = True

    interaction.response.defer = AsyncMock(side_effect=_defer)
    interaction.response.send_message = AsyncMock(side_effect=_send_message)
    interaction.response.is_done = MagicMock(side_effect=lambda: done["value"])
    interaction.followup.send = AsyncMock(name="followup.send")
    return interaction


def make_message(
    *,
    author: MagicMock,
    guild: MagicMock,
    channel_id: int,
    message_id: int,
    has_attachment: bool = False,
    is_reply: bool = False,
    role_mentions: tuple[MagicMock, ...] = (),
) -> MagicMock:
    message = MagicMock(name="Message", spec=discord.Message)
    message.id = message_id
    message.author = author
    message.guild = guild
    message.channel.id = channel_id
    message.attachments = [MagicMock(name="Attachment")] if has_attachment else []
    message.reference = MagicMock(name="Reference") if is_reply else None
    message.role_mentions = list(role_mentions)
    return message


def make_reaction(*, message: MagicMock) -> MagicMock:
    reaction = MagicMock(name="Reaction", spec=discord.Reaction)
    reaction.message = message
    return reaction


def make_voice_state(channel_id: int | None) -> MagicMock:
    state = MagicMock(name="VoiceState", spec=discord.VoiceState)
    if channel_id is None:
        state.channel = None
    else:
        state.channel = MagicMock(name=f"VoiceChannel:{channel_id}")
        state.channel.id = channel_id
    return state


def make_role(role_id: int, members: tuple[MagicMock, ...] = ()) -> MagicMock:
    role = MagicMock(name=f"Role:{role_id}", spec=discord.Role)
    role.id = role_id
    role.members = list(members)
    return role
