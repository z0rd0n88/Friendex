"""Tests for :class:`MemberListener` — ``on_member_update`` + ``on_member_ban``.

The listener detects two disciplinary triggers and delegates to
:meth:`DisciplineService.apply_discipline_penalty`:

* ``on_member_update`` — fires ONLY on a fresh timeout transition
  (``before.timed_out_until is None and after.timed_out_until is not None``).
  Extensions (``set → later-set``) and un-timeouts (``set → None``) do not
  re-trigger.
* ``on_member_ban`` — fires for every ban.

Tests instantiate the listener and ``await`` each event handler directly
(Phase 11 callback-direct idiom).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from friendex.adapters.discord_bot.listeners.member_listener import MemberListener
from friendex.domain.errors import DomainError

if TYPE_CHECKING:
    from collections.abc import Callable
    from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# Helpers


def _guild(*, guild_id: int) -> MagicMock:
    """Build a stub :class:`discord.Guild`."""
    guild = MagicMock(name="Guild")
    guild.id = guild_id
    return guild


def _make_member(
    fake_member: Callable[..., MagicMock],
    *,
    user_id: int,
    guild_id: int,
    timed_out_until: datetime | None,
) -> MagicMock:
    return fake_member(
        user_id=user_id, guild_id=guild_id, timed_out_until=timed_out_until
    )


# ---------------------------------------------------------------------------
# on_member_update — timeout None → set fires


async def test_on_member_update_fires_timeout_on_none_to_set(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """A fresh timeout (``None`` → datetime) triggers a ``"timeout"`` penalty."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
    )

    await listener.on_member_update(before, after)

    discipline_service.apply_discipline_penalty.assert_awaited_once_with(
        "42", "timeout"
    )


async def test_on_member_update_routes_through_per_guild_factory(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
) -> None:
    """The factory is called with ``str(after.guild.id)``."""
    seen_guild_ids: list[str] = []

    def factory(guild_id: str) -> object:
        seen_guild_ids.append(guild_id)
        return discipline_service

    listener = MemberListener(discipline_service_factory=factory)
    before = _make_member(fake_member, user_id=42, guild_id=12345, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=12345,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
    )

    await listener.on_member_update(before, after)

    assert seen_guild_ids == ["12345"]


# ---------------------------------------------------------------------------
# on_member_update — guarded transitions (mutation-hardening for A6)


async def test_on_member_update_does_not_fire_on_extension(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """Re-timeout while already timed-out (``set → later-set``) does NOT fire."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    now = datetime.now(tz=UTC)
    before = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=now + timedelta(minutes=5),
    )
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=now + timedelta(minutes=30),
    )

    await listener.on_member_update(before, after)

    discipline_service.apply_discipline_penalty.assert_not_called()


async def test_on_member_update_does_not_fire_on_un_timeout(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """Clearing a timeout (``set → None``) does NOT re-fire the penalty."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    now = datetime.now(tz=UTC)
    before = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=now + timedelta(minutes=5),
    )
    after = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)

    await listener.on_member_update(before, after)

    discipline_service.apply_discipline_penalty.assert_not_called()


async def test_on_member_update_does_not_fire_on_none_to_none(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """Unrelated member edits (no timeout transition) are no-ops."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)

    await listener.on_member_update(before, after)

    discipline_service.apply_discipline_penalty.assert_not_called()


# ---------------------------------------------------------------------------
# on_member_ban — fires "ban"


async def test_on_member_ban_fires_ban_penalty(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """``on_member_ban`` always fires a ``"ban"`` penalty."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    member = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    guild = _guild(guild_id=999)

    await listener.on_member_ban(guild, member)

    discipline_service.apply_discipline_penalty.assert_awaited_once_with("42", "ban")


async def test_on_member_ban_routes_through_per_guild_factory(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
) -> None:
    """The factory is called with ``str(guild.id)`` (the ban guild)."""
    seen_guild_ids: list[str] = []

    def factory(guild_id: str) -> object:
        seen_guild_ids.append(guild_id)
        return discipline_service

    listener = MemberListener(discipline_service_factory=factory)
    member = _make_member(fake_member, user_id=42, guild_id=12345, timed_out_until=None)
    guild = _guild(guild_id=12345)

    await listener.on_member_ban(guild, member)

    assert seen_guild_ids == ["12345"]


# ---------------------------------------------------------------------------
# Mutation-hardening A6: kind argument flip "timeout" ↔ "ban"
#
# These two pinned assertions fail if the kind argument is flipped — they
# are deliberately phrased as explicit-string equality so a swap of the
# two literal arguments would break exactly one.


async def test_on_member_update_passes_kind_timeout_literal(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """The kind passed to the service is the literal ``"timeout"``."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
    )

    await listener.on_member_update(before, after)

    _, args, _ = discipline_service.apply_discipline_penalty.mock_calls[0]
    assert args[1] == "timeout"


async def test_on_member_ban_passes_kind_ban_literal(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """The kind passed to the service is the literal ``"ban"``."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    member = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    guild = _guild(guild_id=999)

    await listener.on_member_ban(guild, member)

    _, args, _ = discipline_service.apply_discipline_penalty.mock_calls[0]
    assert args[1] == "ban"


# ---------------------------------------------------------------------------
# DomainError propagation (A7)


async def test_on_member_update_propagates_domain_error(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """A :class:`DomainError` from the service surfaces uncaught."""
    discipline_service.apply_discipline_penalty.side_effect = DomainError(
        "discipline failed"
    )
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
    )

    with pytest.raises(DomainError):
        await listener.on_member_update(before, after)


async def test_on_member_ban_propagates_domain_error(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """A :class:`DomainError` from the service surfaces uncaught."""
    discipline_service.apply_discipline_penalty.side_effect = DomainError(
        "discipline failed"
    )
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    member = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    guild = _guild(guild_id=999)

    with pytest.raises(DomainError):
        await listener.on_member_ban(guild, member)


# ---------------------------------------------------------------------------
# Cog registration sanity


def test_member_listener_is_a_cog() -> None:
    """The listener subclasses ``commands.Cog`` so Phase 13 can ``add_cog`` it."""
    from discord.ext import commands

    assert issubclass(MemberListener, commands.Cog)


def test_member_listener_registers_update_and_ban_listeners(
    discipline_service_factory: Callable[[str], object],
) -> None:
    """Both event handlers are registered as cog listeners."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    names = [name for name, _ in listener.get_listeners()]
    assert "on_member_update" in names
    assert "on_member_ban" in names
