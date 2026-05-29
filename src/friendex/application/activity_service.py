"""Application service that records Discord activity into user buckets.

:class:`ActivityService` is the use-case layer between the Discord listeners
(``on_message``, ``on_reaction_add``, ``on_voice_state_update`` — Phase 12) and
the persistence ports. It accumulates engagement signals — text/media messages,
replies, reactions, voice minutes — into a user's *today* and *week*
:class:`~friendex.domain.models.ActivityBucket`s, and applies the one-time price
boost a user earns for a long voice stay. The activity totals are later collapsed
to a price delta by the activity-tick task (Phase 9) via the pure
:mod:`friendex.domain.price_engine`.

**Guild scoping (ADR-0001).** Every market is keyed by ``(guild_id, user_id)``.
The service is constructed *per guild* — ``guild_id`` is a constructor argument
rather than a per-call parameter — so the listener-facing methods match the
original single-guild handler signatures while the per-guild scope is supplied at
composition time (Phase 14 wires one service per guild).

**Concurrency.** Every method that mutates a user's stored state does so inside
``async with lock_manager.locked(self._lock_key(user_id))`` (composite
``"<guild_id>:<user_id>"`` key, per ADR-0001) so concurrent listener callbacks
for the same ``(guild, user)`` serialise — and the same user in a *different*
guild does not. The :class:`~friendex.application.lock_manager.LockManager` is
an injected process-local singleton (one shared across every per-guild scope)
— never constructed per call.

**Immutability.** Domain models are dataclasses but are treated as immutable: a
mutation reads the stored aggregate, builds a replaced copy via
:func:`dataclasses.replace`, and round-trips it through ``upsert``. Stored
references are never mutated in place (keeping fake and SQLite behaviour
identical).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from friendex.domain.activity import reset_activity_bucket
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    PricePoint,
    UserAccount,
    VoiceSession,
)
from friendex.domain.price_engine import apply_floor_stall

if TYPE_CHECKING:
    from collections.abc import Callable

    from friendex.adapters.config import Settings
    from friendex.application.interfaces import IPriceRepo, IUserRepo
    from friendex.application.lock_manager import LockManager
    from friendex.application.voice_session_store import VoiceSessionStore

# Module-level structlog logger — keyword arguments are picked up by the
# configured processor chain in ``adapters/config.py``.
_log = structlog.get_logger(__name__)


class ActivityService:
    """Records Discord activity into a user's today + week buckets."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        lock_manager: LockManager,
        settings: Settings,
        voice_sessions: VoiceSessionStore,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._locks = lock_manager
        self._settings = settings
        self._voice_sessions = voice_sessions

    # -- internal helpers ---------------------------------------------------

    def _lock_key(self, user_id: str) -> str:
        """Return the ``LockManager`` key for ``user_id`` in this guild.

        ADR-0001 mandates per-guild market isolation: the same user in two
        guilds must NOT serialise against themselves on the single shared
        :class:`~friendex.application.lock_manager.LockManager` that
        Phase 14 injects into every per-guild service scope. Composing the
        guild id into the key guarantees that.
        """
        return f"{self._guild_id}:{user_id}"

    async def _get_or_create(self, user_id: str) -> UserAccount:
        """Return the stored account for ``user_id``, creating a default one.

        Mirrors the original ``ensure_user``: a never-seen user starts with the
        configured initial cash, a flat net worth, empty positions, and fresh
        zeroed buckets. The caller persists any subsequent mutation.
        """
        existing = await self._user_repo.get(self._guild_id, user_id)
        if existing is not None:
            return existing
        now = datetime.now(tz=UTC)
        initial_cash = Decimal(str(self._settings.initial_cash))
        return UserAccount(
            user_id=user_id,
            cash_balance=initial_cash,
            net_worth=initial_cash,
            month_start_net_worth=initial_cash,
            long_positions={},
            short_positions={},
            today=ActivityBucket(bucket_start=now),
            week=ActivityBucket(bucket_start=now),
            daily=DailyProgress(last_claim=None, streak=0),
            last_activity=now,
        )

    async def _mutate(
        self,
        user_id: str,
        change: Callable[[UserAccount], UserAccount],
    ) -> None:
        """Apply ``change`` to ``user_id``'s account under its lock and persist.

        ``change`` is a pure function from the current account to a replaced
        copy; it must not mutate its argument in place.
        """
        async with self._locks.locked(self._lock_key(user_id)):
            account = await self._get_or_create(user_id)
            await self._user_repo.upsert(self._guild_id, change(account))

    @staticmethod
    def _bump_both(
        account: UserAccount,
        **deltas: int | float,
    ) -> UserAccount:
        """Return ``account`` with the given counter deltas added to both buckets.

        Each keyword is an :class:`ActivityBucket` field; the same delta is added
        to the matching field in both ``today`` and ``week`` via fresh replaced
        buckets, so neither stored bucket is mutated in place.
        """
        today = replace(
            account.today,
            **{
                name: getattr(account.today, name) + delta
                for name, delta in deltas.items()
            },
        )
        week = replace(
            account.week,
            **{
                name: getattr(account.week, name) + delta
                for name, delta in deltas.items()
            },
        )
        return replace(
            account,
            today=today,
            week=week,
            last_activity=datetime.now(tz=UTC),
        )

    # -- message / reaction recording --------------------------------------

    async def record_message(
        self,
        author_id: str,
        has_attachment: bool,
        is_reply: bool,
        channel_id: int,
    ) -> None:
        """Record a message's engagement for ``author_id``.

        A message with an attachment counts as *media* (and, in a configured
        photo-bonus channel, also grants a fixed ``role_ping_join_minutes``
        bonus); a message without one counts as *text*. A reply additionally
        bumps ``reply_count``. Both today and week buckets are updated.
        """
        deltas: dict[str, int | float] = {}
        if has_attachment:
            deltas["media_msgs"] = 1
            if channel_id in self._settings.photo_bonus_channel_ids:
                deltas["role_ping_join_minutes"] = self._settings.photo_bonus_points
        else:
            deltas["text_msgs"] = 1
        if is_reply:
            deltas["reply_count"] = 1

        await self._mutate(author_id, lambda acc: self._bump_both(acc, **deltas))

    async def record_reaction(self, user_id: str) -> None:
        """Record that ``user_id`` added a reaction (today + week)."""
        await self._mutate(user_id, lambda acc: self._bump_both(acc, reaction_count=1))

    # -- voice session lifecycle -------------------------------------------

    async def handle_voice_join(
        self,
        user_id: str,
        channel_id: int,
        joined_from_ping: bool,
    ) -> None:
        """Open a live voice session for ``user_id`` and refresh last activity.

        The volatile session records the start instant and channel; the
        ping-response reward path (``VoicePingService``) links any originating
        ping message ids onto it for the long-stay bonus.
        """
        session = VoiceSession(
            user_id=user_id,
            channel_id=channel_id,
            start=datetime.now(tz=UTC),
            from_ping_message_ids=set(),
        )
        await self._voice_sessions.set(session)
        await self._mutate(
            user_id,
            lambda acc: replace(acc, last_activity=datetime.now(tz=UTC)),
        )

    async def handle_voice_leave(
        self,
        user_id: str,
        channel_id: int,
        stay_minutes: float,
        joined_from_ping: bool,
    ) -> None:
        """Credit a voice stay for ``user_id`` and clear the live session.

        ``stay_minutes`` of voice time are added to both buckets, ``channel_id``
        is recorded as a unique channel, and a stay at or beyond
        ``voice_stay_bonus_minutes`` earns a one-time price boost
        (``voice_stay_boost``) clamped through :func:`apply_floor_stall`.

        **Atomicity (issue #84 M — silent-failures branch).** ``_mutate`` and
        ``_apply_stay_boost`` each take the same composite
        ``(guild_id, user_id)`` lock key independently — they do NOT share a
        single composite-lock acquisition. The pre-fix concern was that a
        concurrent path could land between the two releases and observe an
        intermediate state. The realised risk is small because:

        * the two writes target different aggregates (``UserAccount`` bucket
          vs ``Stock`` price) — they do not race on the same row;
        * ``_apply_stay_boost`` re-reads the stock inside its own lock
          acquisition, so any concurrent price write is preserved (the boost
          composes onto the latest value);
        * the boost is gated by an external ``stay_minutes`` threshold the
          caller computed; it is *not* a derived predicate that could fire
          twice on the same stay.

        Documenting rather than consolidating: a single composite lock would
        widen the critical section across an unrelated I/O hop (the stock
        upsert) for every voice-leave even when no boost fires. The bucket
        write is the hot path; the price write is the cold path.
        """
        await self._voice_sessions.pop(user_id)

        def credit(account: UserAccount) -> UserAccount:
            today = self._with_voice(account.today, stay_minutes, channel_id)
            week = self._with_voice(account.week, stay_minutes, channel_id)
            return replace(
                account,
                today=today,
                week=week,
                last_activity=datetime.now(tz=UTC),
            )

        await self._mutate(user_id, credit)

        if stay_minutes >= self._settings.voice_stay_bonus_minutes:
            await self._apply_stay_boost(user_id)

    @staticmethod
    def _with_voice(
        bucket: ActivityBucket,
        stay_minutes: float,
        channel_id: int,
    ) -> ActivityBucket:
        """Return ``bucket`` with voice minutes + unique channel folded in."""
        channels = list(bucket.voice_unique_channels)
        ch_id = str(channel_id)
        if ch_id not in channels:
            channels.append(ch_id)
        return replace(
            bucket,
            voice_minutes=bucket.voice_minutes + stay_minutes,
            voice_unique_channels=channels,
        )

    async def _apply_stay_boost(self, user_id: str) -> None:
        """Apply the one-time long-stay price boost to ``user_id``'s stock.

        Logs ``stay_boost_no_stock`` (warning) when the user's stock row is
        missing — issue #84 M (silent-failures branch). Silently dropping the
        boost hid a persistence drift; the structured log lets the operator
        catch it without changing user-visible behaviour.

        Issue #82 M6 — the upsert is paired with an ``append_history`` call
        on a real price change so the 24h-window aggregations
        (:class:`PortfolioService` high/low derived from history) include
        this boost. The ``if new_price != stock.current`` guard mirrors
        :meth:`PriceTickService._rmw_price` and avoids padding history
        with duplicate points when the boost rounds to a no-op (e.g. the
        floor stall stalled the rise).
        """
        min_price = Decimal(str(self._settings.min_price))
        boost = Decimal(str(self._settings.voice_stay_boost))
        async with self._locks.locked(self._lock_key(user_id)):
            stock = await self._price_repo.get(self._guild_id, user_id)
            if stock is None:
                _log.warning(
                    "stay_boost_no_stock",
                    user_id=user_id,
                    guild_id=self._guild_id,
                )
                return
            proposed = stock.current * boost
            new_price = apply_floor_stall(stock.current, proposed, min_price)
            if new_price == stock.current:
                return
            await self._price_repo.upsert(
                self._guild_id, replace(stock, current=new_price)
            )
            await self._price_repo.append_history(
                self._guild_id,
                user_id,
                PricePoint(price=new_price, timestamp=datetime.now(tz=UTC)),
            )

    # -- consent + intro ----------------------------------------------------

    async def set_opt_in(self, user_id: str, value: bool) -> None:
        """Set whether ``user_id`` consents to being a tradeable stock."""
        await self._mutate(user_id, lambda acc: replace(acc, opt_in=value))

    async def mark_intro_shown(self, user_id: str) -> None:
        """Record that the one-time intro message was shown to ``user_id``."""
        await self._mutate(user_id, lambda acc: replace(acc, intro_shown=True))

    async def opt_in_and_consume_intro(self, user_id: str) -> bool:
        """Opt ``user_id`` in and atomically consume the one-time intro flag.

        Returns ``True`` when the account had ``intro_shown=False`` before the
        call — the cog interprets that as "fire the one-time intro DM" — and
        also flips ``intro_shown`` to ``True`` in the same persisted write.
        Returns ``False`` when the intro was already consumed; ``opt_in`` is
        still set to ``True`` (idempotent) but no further mutation occurs.

        An unknown user is auto-seeded by :meth:`_get_or_create` (mirroring
        :meth:`set_opt_in` and :meth:`mark_intro_shown`) so a never-seen
        member's first ``/optin`` still produces a single atomic write.

        The read-modify-write happens under
        ``self._locks.locked(self._lock_key(user_id))`` — the same composite
        ``(guild_id, user_id)`` key every other mutation uses (ADR-0001
        per-guild market isolation).
        """
        async with self._locks.locked(self._lock_key(user_id)):
            account = await self._get_or_create(user_id)
            should_show_intro = not account.intro_shown
            updated = replace(account, opt_in=True, intro_shown=True)
            await self._user_repo.upsert(self._guild_id, updated)
            return should_show_intro

    # -- bucket resets (called by daily / weekly reset tasks) --------------

    async def reset_today_buckets(self) -> None:
        """Zero every account's *today* bucket across the guild (week untouched)."""
        now = datetime.now(tz=UTC)
        for account in await self._user_repo.list_all(self._guild_id):
            async with self._locks.locked(self._lock_key(account.user_id)):
                current = await self._user_repo.get(self._guild_id, account.user_id)
                if current is None:
                    continue
                fresh = replace(
                    current, today=reset_activity_bucket(current.today, now)
                )
                await self._user_repo.upsert(self._guild_id, fresh)

    async def reset_week_buckets(self) -> None:
        """Zero every account's *week* bucket across the guild (today untouched)."""
        now = datetime.now(tz=UTC)
        for account in await self._user_repo.list_all(self._guild_id):
            async with self._locks.locked(self._lock_key(account.user_id)):
                current = await self._user_repo.get(self._guild_id, account.user_id)
                if current is None:
                    continue
                fresh = replace(current, week=reset_activity_bucket(current.week, now))
                await self._user_repo.upsert(self._guild_id, fresh)
