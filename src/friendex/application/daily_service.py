"""Daily-reward use case for the Phase 8e service layer.

:class:`DailyService` owns the ``/daily`` slash command — mediating between
the Phase 11 ``DailyCog`` and the persisted :class:`DailyProgress` state on
each :class:`UserAccount`. The service mirrors the original ``$daily`` command
(``docs/spec/original-skeleton.md:941-986``):

* The first-ever claim credits ``settings.daily_reward`` and starts the
  streak at ``1``.
* A repeat claim within 24 hours of the previous one raises
  :class:`AlreadyClaimedToday` (the original returned an ephemeral error
  message; here we surface a domain error so the cog's error handler can
  format the user-facing copy uniformly).
* A claim ``24 h`` after the previous one continues the streak; a gap of
  ``48 h`` or more resets the streak to ``1`` (spec lines 962-965).
* When the streak reaches ``7`` the claim credits
  ``daily_reward + streak_bonus`` and **resets the streak counter to ``0``**
  (spec line 980 — note the original resets to ``0`` rather than ``1``;
  the next consecutive claim therefore lands on streak ``1`` again).

**Guild scoping (ADR-0001 / Phase 8a digest).** ``guild_id`` is a constructor
argument captured once as ``self._guild_id``; the lock key follows the
composite ``"<guild_id>:<user_id>"`` shape (:meth:`_lock_key`).

**Concurrency (Phase 7 / Phase 8b RMW discipline).** ``claim_daily`` takes
``async with self._locks.locked(self._lock_key(user_id))`` in ONE critical
section, reads the fresh account inside the lock, and round-trips a
:func:`dataclasses.replace`d account so a concurrent trade or activity tick
landing between the public-method entry and the lock acquire is never
clobbered.

**Decimal + UTC invariants** preserved end-to-end (Phase 3.1):
``Decimal(str(settings.daily_reward))`` for the float-sourced reward, and
``now`` is always a tz-aware UTC :class:`datetime` (callers pass through
``datetime.now(tz=UTC)`` from the task layer).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from friendex.application.daily_result import DailyClaimResult
from friendex.domain.errors import AlreadyClaimedToday
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    UserAccount,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.interfaces import IUserRepo
    from friendex.application.lock_manager import LockManager


# Currency quantisation unit — two decimal places, banker's rounding.
_CENT = Decimal("0.01")
# Claim cadence window — 24 h since last claim is the cooldown line
# (spec line 960 ``time_since >= timedelta(days=1)``).
_CLAIM_WINDOW = timedelta(days=1)
# A gap of >= 2 days resets the streak (spec lines 962-965).
_STREAK_GAP = timedelta(days=2)
# Streak length that triggers the bonus reward and resets the counter
# (spec line 977 ``if user["daily"]["streak"] == 7``).
_STREAK_BONUS_LENGTH = 7
# Streak counter value immediately after the bonus fires (spec line 980).
_POST_BONUS_STREAK = 0


def _quantise(value: Decimal) -> Decimal:
    """Round ``value`` to two decimal places with banker's rounding."""
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


def _next_streak(
    previous_streak: int, last_claim: datetime | None, now: datetime
) -> int:
    """Return the streak counter after a candidate claim at ``now``.

    Pure helper — raises :class:`AlreadyClaimedToday` when ``now`` falls
    inside the 24 h cooldown window from ``last_claim``. Mirrors the spec
    lines 953-965 branching:

    * ``last_claim is None`` -> ``1`` (first claim).
    * ``time_since < 24 h``  -> raise.
    * ``24 h <= time_since < 48 h`` -> ``previous_streak + 1`` (continue).
    * ``time_since >= 48 h``  -> ``1`` (gap reset).
    """
    if last_claim is None:
        return 1
    time_since = now - last_claim
    if time_since < _CLAIM_WINDOW:
        remaining = _CLAIM_WINDOW - time_since
        raise AlreadyClaimedToday(seconds_remaining=int(remaining.total_seconds()))
    if time_since < _STREAK_GAP:
        return previous_streak + 1
    return 1


class DailyService:
    """``/daily`` reward-claim use case."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        lock_manager: LockManager,
        settings: Settings,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._locks = lock_manager
        self._settings = settings

    # -- internal helpers ---------------------------------------------------

    def _lock_key(self, user_id: str) -> str:
        """Return the composite ``"<guild>:<user>"`` lock key (ADR-0001)."""
        return f"{self._guild_id}:{user_id}"

    async def _get_or_create_account(self, user_id: str) -> UserAccount:
        """Return the stored account (creating an initial-cash one if absent).

        Mirrors :meth:`TradingService._get_or_create_user` (Phase 8c digest):
        a never-seen user is seeded with ``settings.initial_cash`` and
        empty positions / fresh zeroed buckets so first-time ``/daily``
        works without requiring a prior trade.
        """
        existing = await self._user_repo.get(self._guild_id, user_id)
        if existing is not None:
            return existing
        now = datetime.now(tz=UTC)
        initial_cash = _quantise(Decimal(str(self._settings.initial_cash)))
        seeded = UserAccount(
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
        await self._user_repo.upsert(self._guild_id, seeded)
        return seeded

    # -- public use case ----------------------------------------------------

    async def claim_daily(self, user_id: str, now: datetime) -> DailyClaimResult:
        """Credit the daily reward (and any streak bonus) atomically.

        ``now`` is a tz-aware UTC :class:`datetime` — typically the cog
        passes ``datetime.now(tz=UTC)``; tests pin a deterministic instant.

        Raises :class:`AlreadyClaimedToday` when the previous claim was less
        than 24 hours ago. Returns a frozen :class:`DailyClaimResult` the
        Phase 10 embed builder consumes as-is.

        Concurrency: per-user lock; the entire RMW (read → compute → upsert)
        happens inside one ``locked()`` critical section so a concurrent
        ``/buy`` or ``/sell`` cannot land between the read and the upsert
        and clobber the cash credit (Phase 8b ``_rmw_price`` discipline).
        """
        async with self._locks.locked(self._lock_key(user_id)):
            account = await self._get_or_create_account(user_id)

            candidate_streak = _next_streak(
                account.daily.streak, account.daily.last_claim, now
            )
            is_bonus = candidate_streak == _STREAK_BONUS_LENGTH

            daily_reward = _quantise(Decimal(str(self._settings.daily_reward)))
            streak_bonus = _quantise(Decimal(str(self._settings.streak_bonus)))
            reward = daily_reward + streak_bonus if is_bonus else daily_reward

            # Spec line 980: after the bonus fires the counter resets to 0.
            recorded_streak = _POST_BONUS_STREAK if is_bonus else candidate_streak

            new_account = replace(
                account,
                cash_balance=_quantise(account.cash_balance + reward),
                daily=DailyProgress(last_claim=now, streak=recorded_streak),
            )
            await self._user_repo.upsert(self._guild_id, new_account)

            return DailyClaimResult(
                user_id=user_id,
                streak=recorded_streak,
                reward=reward,
                is_streak_bonus=is_bonus,
                new_cash_balance=new_account.cash_balance,
                claim_date=now,
            )
