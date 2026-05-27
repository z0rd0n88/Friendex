"""Tests for :class:`MessageListener` — ``on_message``.

The listener routes Discord message events to two services:

* :meth:`ActivityService.record_message` — for every non-bot guild message,
  credits text/media/reply engagement;
* :meth:`VoicePingService.register_ping_message` — only when the author is
  in a voice channel AND the message mentions a configured VC ping role
  (the `is_voice_ping_message` rule from ``docs/spec/original-skeleton.md:494-503``).

Tests instantiate the listener and ``await listener.on_message(message)``
directly — matching the Phase 11/12a cog/listener test idiom.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from friendex.adapters.config import Settings
from friendex.adapters.discord_bot.listeners.message_listener import MessageListener
from friendex.domain.errors import OptedOut

if TYPE_CHECKING:
    from collections.abc import Callable
    from unittest.mock import AsyncMock

# ---------------------------------------------------------------------------
# Helpers


def _settings(*, vc_ping_role_ids: list[int] | None = None) -> Settings:
    """Build a :class:`Settings` with the listener-relevant fields wired."""
    return Settings.model_validate(
        {
            "discord_token": "test-token",
            "vc_ping_role_ids": vc_ping_role_ids or [],
        }
    )


def _with_voice(message: MagicMock, *, channel_id: int | None) -> MagicMock:
    """Attach a voice state to ``message.author``.

    ``member.voice`` is the discord.py shape: ``None`` if not in a VC,
    otherwise an object with ``.channel`` (also ``None`` for stale states).
    """
    if channel_id is None:
        message.author.voice = None
    else:
        voice = MagicMock(name="VoiceState")
        channel = MagicMock(name="VoiceChannel")
        channel.id = channel_id
        voice.channel = channel
        message.author.voice = voice
    return message


def _with_role_mentions(message: MagicMock, *, role_ids: list[int]) -> MagicMock:
    """Attach ``role_mentions`` to a message (each mock carries ``.id``)."""
    role_mocks: list[MagicMock] = []
    for role_id in role_ids:
        role = MagicMock(name="Role")
        role.id = role_id
        role_mocks.append(role)
    message.role_mentions = role_mocks
    return message


def _with_channel(message: MagicMock, *, channel_id: int) -> MagicMock:
    """Attach a ``channel.id`` (the listener forwards it to ``record_message``)."""
    message.channel.id = channel_id
    return message


# ---------------------------------------------------------------------------
# B1 — happy path: text message records activity, no ping side-effect


async def test_on_message_records_text_message_engagement(
    fake_message: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """A plain non-bot guild message bumps ``record_message`` (text branch)."""
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=_settings(),
    )
    message = fake_message(author_id=42, guild_id=999, content="hi")
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=None)
    _with_role_mentions(message, role_ids=[])

    await listener.on_message(message)

    activity_service.record_message.assert_awaited_once_with(
        author_id="42",
        has_attachment=False,
        is_reply=False,
        channel_id=4242,
    )
    voice_ping_service.register_ping_message.assert_not_called()


async def test_on_message_media_and_reply_flags_pass_through(
    fake_message: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """A reply-with-attachment passes both flags as ``True``."""
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=_settings(),
    )
    message = fake_message(
        author_id=42,
        guild_id=999,
        content="reply",
        reference_id=1234,
    )
    attachment = MagicMock(name="Attachment")
    message.attachments = [attachment]
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=None)
    _with_role_mentions(message, role_ids=[])

    await listener.on_message(message)

    activity_service.record_message.assert_awaited_once_with(
        author_id="42",
        has_attachment=True,
        is_reply=True,
        channel_id=4242,
    )


# ---------------------------------------------------------------------------
# B1 — VC ping branch


async def test_on_message_registers_voice_ping_when_author_in_voice_and_role_mention(
    fake_message: Callable[..., MagicMock],
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """Author in VC + role-mention of a configured VC ping role → ping registered."""
    settings = _settings(vc_ping_role_ids=[111])
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=settings,
    )
    message = fake_message(author_id=42, guild_id=999, content="@VC come join")
    message.id = 88888
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=5555)
    _with_role_mentions(message, role_ids=[111])

    await listener.on_message(message)

    voice_ping_service.register_ping_message.assert_awaited_once()
    kwargs = voice_ping_service.register_ping_message.await_args.kwargs
    assert kwargs["message_id"] == 88888
    assert kwargs["host_id"] == "42"
    assert kwargs["channel_id"] == 5555
    # ``timestamp`` is a UTC-aware datetime; the listener uses ``now(tz=UTC)``.
    from datetime import UTC

    assert kwargs["timestamp"].tzinfo is UTC


async def test_on_message_does_not_register_ping_when_author_not_in_voice(
    fake_message: Callable[..., MagicMock],
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """Role-mention without the author being in voice is not a ping."""
    settings = _settings(vc_ping_role_ids=[111])
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=settings,
    )
    message = fake_message(author_id=42, guild_id=999, content="@VC")
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=None)
    _with_role_mentions(message, role_ids=[111])

    await listener.on_message(message)

    voice_ping_service.register_ping_message.assert_not_called()


async def test_on_message_does_not_register_ping_when_role_not_in_config(
    fake_message: Callable[..., MagicMock],
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """Mentioned role isn't in ``settings.vc_ping_role_ids`` → no ping."""
    settings = _settings(vc_ping_role_ids=[111])
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=settings,
    )
    message = fake_message(author_id=42, guild_id=999, content="@Other")
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=5555)
    _with_role_mentions(message, role_ids=[222])

    await listener.on_message(message)

    voice_ping_service.register_ping_message.assert_not_called()


async def test_on_message_does_not_register_ping_with_empty_config(
    fake_message: Callable[..., MagicMock],
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """Default ``vc_ping_role_ids = []`` means no message is ever a ping."""
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=_settings(),
    )
    message = fake_message(author_id=42, guild_id=999, content="@anything")
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=5555)
    _with_role_mentions(message, role_ids=[111, 222])

    await listener.on_message(message)

    voice_ping_service.register_ping_message.assert_not_called()


# ---------------------------------------------------------------------------
# B1 — DM skip


async def test_on_message_skips_dm_messages(
    fake_message: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """``message.guild is None`` (DM) → both services skipped."""
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=_settings(),
    )
    message = MagicMock(name="DMMessage")
    message.author.id = 42
    message.author.bot = False
    message.guild = None
    message.content = "hello"
    message.attachments = []
    message.reference = None
    message.role_mentions = []
    message.channel.id = 4242

    await listener.on_message(message)

    activity_service.record_message.assert_not_called()
    voice_ping_service.register_ping_message.assert_not_called()


# ---------------------------------------------------------------------------
# B1 + B6 — Bot-skip mutation-hardening (signoff decision 3)


async def test_on_message_skips_bot_author(
    fake_message: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service: AsyncMock,
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """Messages from any bot (own bot or third-party) are silently dropped.

    Load-bearing: if the ``author.bot`` skip is removed, this test fails
    because both services would be called.
    """
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=_settings(vc_ping_role_ids=[111]),
    )
    message = fake_message(author_id=99, guild_id=999, content="bot post", is_bot=True)
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=5555)
    _with_role_mentions(message, role_ids=[111])

    await listener.on_message(message)

    activity_service.record_message.assert_not_called()
    voice_ping_service.register_ping_message.assert_not_called()


# ---------------------------------------------------------------------------
# B7 — DomainError propagation


async def test_on_message_propagates_domain_error_from_activity_service(
    fake_message: Callable[..., MagicMock],
    activity_service: AsyncMock,
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """:meth:`record_message` raising :class:`DomainError` surfaces uncaught."""
    activity_service.record_message.side_effect = OptedOut(target_id="42")
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=_settings(),
    )
    message = fake_message(author_id=42, guild_id=999, content="hi")
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=None)
    _with_role_mentions(message, role_ids=[])

    with pytest.raises(OptedOut):
        await listener.on_message(message)


# ---------------------------------------------------------------------------
# Per-guild factory routing


async def test_on_message_routes_through_per_guild_factory(
    fake_message: Callable[..., MagicMock],
    activity_service: AsyncMock,
    voice_ping_service: AsyncMock,
) -> None:
    """The factory is called with ``str(message.guild.id)`` for each service."""
    seen_activity: list[str] = []
    seen_ping: list[str] = []

    def activity_factory(guild_id: str) -> object:
        seen_activity.append(guild_id)
        return activity_service

    def ping_factory(guild_id: str) -> object:
        seen_ping.append(guild_id)
        return voice_ping_service

    listener = MessageListener(
        activity_service_factory=activity_factory,
        voice_ping_service_factory=ping_factory,
        settings=_settings(vc_ping_role_ids=[111]),
    )
    message = fake_message(author_id=42, guild_id=12345, content="ping")
    message.id = 9
    _with_channel(message, channel_id=4242)
    _with_voice(message, channel_id=5555)
    _with_role_mentions(message, role_ids=[111])

    await listener.on_message(message)

    assert seen_activity == ["12345"]
    assert seen_ping == ["12345"]


# ---------------------------------------------------------------------------
# Cog registration sanity


def test_message_listener_is_a_cog() -> None:
    """The listener subclasses ``commands.Cog`` so Phase 13 can ``add_cog`` it."""
    from discord.ext import commands

    assert issubclass(MessageListener, commands.Cog)


def test_message_listener_registers_on_message_listener(
    activity_service_factory: Callable[[str], object],
    voice_ping_service_factory: Callable[[str], object],
) -> None:
    """``on_message`` is decorated with :meth:`commands.Cog.listener`."""
    listener = MessageListener(
        activity_service_factory=activity_service_factory,
        voice_ping_service_factory=voice_ping_service_factory,
        settings=_settings(),
    )
    names = [name for name, _ in listener.get_listeners()]
    assert "on_message" in names
