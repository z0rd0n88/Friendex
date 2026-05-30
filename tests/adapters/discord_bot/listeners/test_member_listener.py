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

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from friendex.adapters.discord_bot.listeners.member_listener import MemberListener
from friendex.domain.errors import DomainError

if TYPE_CHECKING:
    from collections.abc import Callable


# ---------------------------------------------------------------------------
# Helpers


def _guild(
    *,
    guild_id: int,
    audit_user_id: int | None = None,
    audit_target_id: int | None = None,
    audit_entries: list[tuple[int | None, int | None]] | None = None,
) -> MagicMock:
    """Build a stub :class:`discord.Guild`.

    ``audit_user_id`` mimics the moderator the audit-log lookup will surface
    when the listener calls ``guild.audit_logs(...)``. Pass ``None`` to
    simulate a guild where the bot lacks ``view_audit_log`` permission (or
    the action has no recent audit entry) — the listener falls back to a
    sentinel actor id.

    ``audit_target_id`` is the ``entry.target.id`` the listener filters on
    (PR #93 M2). Defaults to ``None``, which means the test does not care
    about the target binding; in that case the audit-log entry is built
    with no ``target`` and the listener will fall through to the sentinel.

    ``audit_entries`` overrides both single-entry kwargs with a list of
    ``(actor_id, target_id)`` tuples, in newest-first order. Use this for
    tests that need to exercise the listener's "scan up to N entries and
    pick the first target-bound one" path (e.g. busy-guild scenarios where
    the most-recent entry belongs to an unrelated mod action).
    """
    guild = MagicMock(name="Guild")
    guild.id = guild_id
    guild.audit_logs = _audit_logs_factory(
        audit_user_id=audit_user_id,
        audit_target_id=audit_target_id,
        audit_entries=audit_entries,
    )
    return guild


def _audit_logs_factory(
    *,
    audit_user_id: int | None = None,
    audit_target_id: int | None = None,
    audit_entries: list[tuple[int | None, int | None]] | None = None,
) -> MagicMock:
    """Return a callable that yields an async iterable of audit entries.

    ``discord.Guild.audit_logs`` returns an async-iterator-shaped object;
    the listener (post-PR #93 M2) iterates up to ``limit`` entries and
    filters by ``entry.target.id == int(target_id)`` to find the actor
    that mutated this specific target.

    Two shapes:
    * Single-entry mode (``audit_user_id`` + ``audit_target_id``) — one
      entry yielded with that ``user.id`` + ``target.id``; matches the
      common "the most-recent matching audit entry IS for our target"
      case used across most tests.
    * Multi-entry mode (``audit_entries`` list) — yields N entries in
      newest-first order so the listener's scan-and-filter logic can be
      exercised end-to-end.

    Either way, passing ``audit_user_id=None`` / ``audit_entries=None``
    yields zero entries — drives the no-audit-permission / no-entry
    fallback path.
    """

    if audit_entries is None:
        if audit_user_id is None:
            entries_payload: list[tuple[int | None, int | None]] = []
        else:
            entries_payload = [(audit_user_id, audit_target_id)]
    else:
        entries_payload = audit_entries

    class _AsyncIter:
        def __init__(self) -> None:
            self._entries: list[MagicMock] = []
            for user_id, target_id in entries_payload:
                entry = MagicMock(name="AuditLogEntry")
                if user_id is None:
                    entry.user = None
                else:
                    entry.user = MagicMock(name="AuditLogEntry.user")
                    entry.user.id = user_id
                if target_id is None:
                    entry.target = None
                else:
                    entry.target = MagicMock(name="AuditLogEntry.target")
                    entry.target.id = target_id
                self._entries.append(entry)
            self._index = 0

        def __aiter__(self) -> _AsyncIter:
            return self

        async def __anext__(self) -> MagicMock:
            if self._index >= len(self._entries):
                raise StopAsyncIteration
            entry = self._entries[self._index]
            self._index += 1
            return entry

    # ``Guild.audit_logs(...)`` is invoked synchronously; the returned
    # value supports ``__aiter__``. Use a plain callable so the listener
    # can pass action= / limit= kwargs without the mock complaining.
    def _factory(*_args: object, **_kwargs: object) -> _AsyncIter:
        return _AsyncIter()

    return MagicMock(name="Guild.audit_logs", side_effect=_factory)


def _make_member(
    fake_member: Callable[..., MagicMock],
    *,
    user_id: int,
    guild_id: int,
    timed_out_until: datetime | None,
    audit_user_id: int | None = None,
    audit_target_id: int | None = None,
    audit_entries: list[tuple[int | None, int | None]] | None = None,
) -> MagicMock:
    """Build a stub member and override its guild's audit-log iterable.

    ``audit_user_id`` / ``audit_target_id`` populate the single audit-log
    entry the listener will see (post-PR #93 M2: the listener filters on
    ``entry.target.id``, so to drive the happy path the entry's target id
    must equal the member id). When ``audit_target_id`` is omitted but
    ``audit_user_id`` is supplied, ``user_id`` is used as the default so
    the existing single-target tests still pass without per-test edits.

    ``audit_entries`` overrides the single-entry mode entirely — see
    :func:`_audit_logs_factory`.
    """
    member = fake_member(
        user_id=user_id, guild_id=guild_id, timed_out_until=timed_out_until
    )
    # By default, when a test sets ``audit_user_id`` but not
    # ``audit_target_id``, assume the audit entry is target-bound on this
    # member — preserves the simple "this member just got timed out by
    # actor X" shape used across the cooldown tests without forcing each
    # one to thread the target id explicitly.
    resolved_target_id = (
        audit_target_id
        if audit_target_id is not None
        else (user_id if audit_user_id is not None else None)
    )
    member.guild.audit_logs = _audit_logs_factory(
        audit_user_id=audit_user_id,
        audit_target_id=resolved_target_id,
        audit_entries=audit_entries,
    )
    return member


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


# ---------------------------------------------------------------------------
# Issue #84 M — audit log + per-(guild, target, actor) cooldown
# ---------------------------------------------------------------------------


def _logger_name() -> str:
    """Module logger name used by :class:`MemberListener` for audit lines."""
    return "friendex.adapters.discord_bot.listeners.member_listener"


async def test_on_member_update_emits_audit_log_entry(
    fake_member: Callable[..., MagicMock],
    discipline_service_factory: Callable[[str], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #84 M — every applied timeout penalty emits a structured audit line.

    Operators need a stable signal in the log stream when discipline fires
    so a sudden spike of mass-timeouts is observable without scraping the
    raw Discord event stream. The line has the canonical event name
    ``discipline_penalty_applied`` and carries ``guild_id`` / ``target_id``
    / ``actor_id`` / ``reason`` as structured ``extra`` fields.
    """
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        audit_user_id=7777,
    )

    with caplog.at_level(logging.INFO, logger=_logger_name()):
        await listener.on_member_update(before, after)

    matching = [
        r
        for r in caplog.records
        if r.name == _logger_name() and r.message == "discipline_penalty_applied"
    ]
    assert len(matching) == 1
    record = matching[0]
    assert record.levelno == logging.INFO
    assert getattr(record, "guild_id", None) == "999"
    assert getattr(record, "target_id", None) == "42"
    assert getattr(record, "reason", None) == "timeout"
    # The audit-log lookup found user 7777, so that's the actor id.
    assert getattr(record, "actor_id", None) == "7777"


async def test_on_member_ban_emits_audit_log_entry(
    fake_member: Callable[..., MagicMock],
    discipline_service_factory: Callable[[str], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #84 M — every applied ban penalty emits a structured audit line."""
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    member = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    # PR #93 M2: the listener filters audit entries by ``entry.target.id``;
    # bind the entry to user 42 so the target-bound match succeeds.
    guild = _guild(guild_id=999, audit_user_id=8888, audit_target_id=42)

    with caplog.at_level(logging.INFO, logger=_logger_name()):
        await listener.on_member_ban(guild, member)

    matching = [
        r
        for r in caplog.records
        if r.name == _logger_name() and r.message == "discipline_penalty_applied"
    ]
    assert len(matching) == 1
    record = matching[0]
    assert getattr(record, "guild_id", None) == "999"
    assert getattr(record, "target_id", None) == "42"
    assert getattr(record, "reason", None) == "ban"
    assert getattr(record, "actor_id", None) == "8888"


async def test_on_member_update_falls_back_to_unknown_actor_when_audit_missing(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #84 M — missing audit entry yields ``actor_id="unknown"``.

    When the bot lacks ``view_audit_log`` permission (or the action has no
    recent audit entry within the lookup window), the listener still
    applies the penalty but tags the audit line + cooldown key with the
    sentinel ``"unknown"`` so the cooldown semantics still bucket all
    "unknown-actor" rapid-fires together — defensive: it prevents a
    permission misconfiguration from silently disabling the cooldown.
    """
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        # No audit entry — simulate missing permission.
        audit_user_id=None,
    )

    with caplog.at_level(logging.INFO, logger=_logger_name()):
        await listener.on_member_update(before, after)

    matching = [
        r
        for r in caplog.records
        if r.name == _logger_name() and r.message == "discipline_penalty_applied"
    ]
    assert len(matching) == 1
    assert getattr(matching[0], "actor_id", None) == "unknown"
    # The service still ran — the audit gap must NOT silently swallow the
    # disciplinary action.
    discipline_service.apply_discipline_penalty.assert_awaited_once_with(
        "42", "timeout"
    )


async def test_cooldown_blocks_second_penalty_within_window(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Issue #84 M — same (guild, target, actor) twice in window: second is dropped.

    A moderator re-timing-out the same member within the 24h cooldown
    window does not re-fire the discipline service: the service is
    awaited exactly once, and the second call emits a
    ``discipline_penalty_skipped_cooldown`` audit line so operators can
    still see the attempt.
    """
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before_1 = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after_1 = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        audit_user_id=7777,
    )
    # The cooldown is keyed (guild, target, actor). Re-fire with the same
    # actor 7777 → blocked.
    before_2 = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after_2 = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=30),
        audit_user_id=7777,
    )

    with caplog.at_level(logging.INFO, logger=_logger_name()):
        await listener.on_member_update(before_1, after_1)
        await listener.on_member_update(before_2, after_2)

    # Service ran exactly once — the second call was gated by the cooldown.
    assert discipline_service.apply_discipline_penalty.await_count == 1
    skipped = [
        r
        for r in caplog.records
        if r.name == _logger_name()
        and r.message == "discipline_penalty_skipped_cooldown"
    ]
    assert len(skipped) == 1
    record = skipped[0]
    assert getattr(record, "actor_id", None) == "7777"
    assert getattr(record, "target_id", None) == "42"


async def test_cooldown_allows_different_actor_within_window(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """Issue #84 M — a *different* moderator within the window still triggers.

    The cooldown key is ``(guild, target, actor)``; two distinct moderators
    timing-out the same target back-to-back is a legitimate signal (e.g.
    one moderator escalating after a peer review) and must not be muted.
    """
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before_1 = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after_1 = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        audit_user_id=7777,
    )
    before_2 = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after_2 = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=15),
        # Different moderator.
        audit_user_id=9999,
    )

    await listener.on_member_update(before_1, after_1)
    await listener.on_member_update(before_2, after_2)

    # Both fired — distinct actor cooldown buckets.
    assert discipline_service.apply_discipline_penalty.await_count == 2


async def test_cooldown_expires_after_window(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
) -> None:
    """Issue #84 M — a re-fire after the cooldown elapses succeeds.

    A short ``cooldown_seconds`` injected at construction time makes the
    expiry behaviour testable without freezing the clock. The injected
    ``clock`` callable advances between the two events to step past the
    cooldown boundary.
    """
    now_holder = [datetime(2026, 5, 28, 12, 0, tzinfo=UTC)]

    def fake_clock() -> datetime:
        return now_holder[0]

    listener = MemberListener(
        discipline_service_factory=discipline_service_factory,
        cooldown_seconds=60,
        clock=fake_clock,
    )
    before_1 = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after_1 = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=now_holder[0] + timedelta(minutes=10),
        audit_user_id=7777,
    )
    await listener.on_member_update(before_1, after_1)

    # Step the clock past the 60 s cooldown.
    now_holder[0] = now_holder[0] + timedelta(seconds=61)

    before_2 = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after_2 = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=now_holder[0] + timedelta(minutes=10),
        audit_user_id=7777,
    )
    await listener.on_member_update(before_2, after_2)

    # Second fired — the cooldown window had elapsed.
    assert discipline_service.apply_discipline_penalty.await_count == 2


# ---------------------------------------------------------------------------
# PR #93 M1 — opportunistic cooldown GC
# ---------------------------------------------------------------------------


async def test_cooldown_dict_is_garbage_collected_on_next_penalty(
    fake_member: Callable[..., MagicMock],
    discipline_service_factory: Callable[[str], object],
) -> None:
    """PR #93 M1 — expired cooldown entries are dropped on the next penalty fire.

    Without an eviction sweep the ``_cooldown`` dict grows unboundedly across
    the bot's lifetime (one entry per moderator x target x guild that ever
    triggered discipline). The fix is an opportunistic GC inside the cooldown
    lock that drops entries whose expiry has passed ``now`` before checking
    the active row. This test pins the eviction behaviour: after the first
    penalty's TTL has elapsed and a SECOND penalty fires for a DIFFERENT
    actor, the dict must contain only the second entry — the first was
    reaped during the sweep.
    """
    now_holder = [datetime(2026, 5, 28, 12, 0, tzinfo=UTC)]

    def fake_clock() -> datetime:
        return now_holder[0]

    listener = MemberListener(
        discipline_service_factory=discipline_service_factory,
        cooldown_seconds=60,
        clock=fake_clock,
    )

    before_1 = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after_1 = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=now_holder[0] + timedelta(minutes=10),
        audit_user_id=7777,
    )
    await listener.on_member_update(before_1, after_1)
    assert len(listener._cooldown) == 1  # first entry pinned

    # Step the clock well past the 60 s cooldown so the first entry expires.
    now_holder[0] = now_holder[0] + timedelta(seconds=120)

    # Fire a SECOND penalty for a different target so a new (guild, target,
    # actor) row is inserted. The opportunistic sweep should drop the now-
    # expired first row before the second insert, leaving |dict| == 1.
    before_2 = _make_member(fake_member, user_id=99, guild_id=999, timed_out_until=None)
    after_2 = _make_member(
        fake_member,
        user_id=99,
        guild_id=999,
        timed_out_until=now_holder[0] + timedelta(minutes=10),
        audit_user_id=8888,
    )
    await listener.on_member_update(before_2, after_2)

    assert len(listener._cooldown) == 1
    # Only the second (still-active) key remains.
    assert ("999", "99", "8888") in listener._cooldown
    assert ("999", "42", "7777") not in listener._cooldown


# ---------------------------------------------------------------------------
# PR #93 M2 — target-bound audit-log filter
# ---------------------------------------------------------------------------


async def test_audit_log_filters_by_target_id_on_busy_guild(
    fake_member: Callable[..., MagicMock],
    discipline_service_factory: Callable[[str], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PR #93 M2 — the listener picks the audit entry matching THIS target.

    On a busy guild the most-recent ``AuditLogAction.member_update`` entry
    may belong to a completely different moderator's unrelated mutation on
    a different member (nickname change, role update, etc. — all share the
    ``member_update`` action). The previous ``limit=1`` lookup would key
    the cooldown to that wrong actor. The fix scans up to N entries and
    selects the FIRST one whose ``entry.target.id`` matches ``target_id``,
    so the actor binding is correct even when ours is not the newest entry.
    """
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        # The most-recent entry (newest first) belongs to a DIFFERENT target
        # — that mod's action must be skipped. The second entry is the
        # match we want.
        audit_entries=[
            (9999, 88),  # newer unrelated entry on target 88 (different member)
            (7777, 42),  # the actual moderator action on our target 42
        ],
    )

    with caplog.at_level(logging.INFO, logger=_logger_name()):
        await listener.on_member_update(before, after)

    matching = [
        r
        for r in caplog.records
        if r.name == _logger_name() and r.message == "discipline_penalty_applied"
    ]
    assert len(matching) == 1
    # The unrelated 9999/88 entry must be ignored; 7777 is the real actor.
    assert getattr(matching[0], "actor_id", None) == "7777"


async def test_audit_log_returns_unknown_on_unexpected_exception(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PR #93 N2 — defensive ``except Exception`` is exercised end-to-end.

    Lifts the prior ``pragma: no cover`` by injecting an audit_logs factory
    that raises a generic :class:`Exception` (mimicking an HTTP error or
    malformed payload). The listener must still apply the penalty and tag
    the audit log line with the ``"unknown"`` actor sentinel — a defensive
    catch that's never exercised is a refactor footgun, so this test
    pins the contract.
    """
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
    )

    def _raising_audit_logs(*_args: object, **_kwargs: object) -> object:
        # Mimic an unexpected upstream failure — HTTPException, malformed
        # payload, anything that isn't ``discord.Forbidden`` (which is
        # already handled by its dedicated catch).
        raise RuntimeError("simulated audit-log API failure")

    after.guild.audit_logs = MagicMock(
        name="Guild.audit_logs", side_effect=_raising_audit_logs
    )

    with caplog.at_level(logging.INFO, logger=_logger_name()):
        await listener.on_member_update(before, after)

    matching = [
        r
        for r in caplog.records
        if r.name == _logger_name() and r.message == "discipline_penalty_applied"
    ]
    assert len(matching) == 1
    assert getattr(matching[0], "actor_id", None) == "unknown"
    # The penalty was still applied — the catch must NOT silently swallow
    # the disciplinary action.
    discipline_service.apply_discipline_penalty.assert_awaited_once_with(
        "42", "timeout"
    )


async def test_audit_log_falls_back_to_unknown_when_no_target_bound_entry(
    fake_member: Callable[..., MagicMock],
    discipline_service: AsyncMock,
    discipline_service_factory: Callable[[str], object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """PR #93 M2 — no target-bound entry in the scan window → "unknown" actor.

    If none of the recent audit entries match the current target, the
    listener falls back to ``"unknown"`` rather than guessing the most-
    recent unrelated entry's actor. This is the "eventually consistent
    audit feed" case — the matching entry has not been flushed yet, so
    the safest answer is the sentinel rather than a false-positive
    binding.
    """
    listener = MemberListener(discipline_service_factory=discipline_service_factory)
    before = _make_member(fake_member, user_id=42, guild_id=999, timed_out_until=None)
    after = _make_member(
        fake_member,
        user_id=42,
        guild_id=999,
        timed_out_until=datetime.now(tz=UTC) + timedelta(minutes=10),
        # All entries are for OTHER targets — no match for target 42.
        audit_entries=[
            (9999, 88),
            (8888, 77),
            (7777, 66),
        ],
    )

    with caplog.at_level(logging.INFO, logger=_logger_name()):
        await listener.on_member_update(before, after)

    matching = [
        r
        for r in caplog.records
        if r.name == _logger_name() and r.message == "discipline_penalty_applied"
    ]
    assert len(matching) == 1
    assert getattr(matching[0], "actor_id", None) == "unknown"
    # Penalty still fires — the unknown actor must not silently swallow it.
    discipline_service.apply_discipline_penalty.assert_awaited_once_with(
        "42", "timeout"
    )
