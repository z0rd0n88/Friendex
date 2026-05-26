"""Application service that applies disciplinary price penalties.

:class:`DisciplineService` is the Discord moderation hook: when a member is
timed-out or banned (raised by the Phase 12 ``on_member_update`` listener),
their own stock takes a flat-percentage hit. The drop is
``settings.discipline_penalty`` (default 17% per the original spec) and the
result is floored at ``settings.min_price`` (default $70).

**Why it exists.** The original bot inflicted this penalty inline in the
event handler; here it lives behind a single service entry point so the
listener stays thin and the rule is unit-testable in isolation.

**Opt-in DOES NOT exempt.** ``UserAccount.opt_in`` only gates whether others
can trade *into* the stock (Phase 8c rejects buy/sell/short/cover against an
opted-out target). Disciplinary action by definition is applied to the
user's own stock regardless of consent — they still take the drop.

**Concurrency (Phase 7 + 8b RMW discipline).** The penalty is a write,
applied under a single per-user :meth:`LockManager.locked` critical section
covering: read stock → compute new price → upsert + history append +
``all_time_high`` ratchet. Mirrors :class:`PriceTickService._rmw_price`. The
floor is enforced via a flat ``max(proposed, min_price)`` (same shape as
:func:`~friendex.domain.price_engine.apply_inactivity_decay`) rather than
the attenuated :func:`~friendex.domain.price_engine.apply_floor_stall`,
because the disciplinary drop is defined as a flat percentage of the
current price — attenuating it near the floor would silently soften the
penalty for users whose stock has already cratered.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING, Literal

from friendex.domain.models import PricePoint

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.interfaces import IPriceRepo, IUserRepo
    from friendex.application.lock_manager import LockManager


DisciplineReason = Literal["timeout", "ban"]

# Currency quantisation unit — two decimal places, banker's rounding.
_CENT = Decimal("0.01")


def _quantise(value: Decimal) -> Decimal:
    """Round ``value`` to two decimal places with banker's rounding."""
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


@dataclass(frozen=True)
class DisciplineEvent:
    """Audit/notification payload from a discipline-penalty application.

    Returned by :meth:`DisciplineService.apply_discipline_penalty`. Carries
    enough context for the Phase 9 / Phase 12 notifier to render the
    disciplinary action in Discord without re-reading state. ``reason`` is
    one of ``"timeout"`` / ``"ban"`` so the notification copy can branch on
    severity. ``old_price`` and ``new_price`` are pre/post-penalty snapshots;
    when the stock was already at the floor they are equal (a no-op penalty
    on a fully-cratered stock).
    """

    user_id: str
    reason: DisciplineReason
    old_price: Decimal
    new_price: Decimal
    timestamp: datetime


class DisciplineService:
    """Applies disciplinary price penalties to a user's own stock."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        lock_manager: LockManager,
        settings: Settings,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._locks = lock_manager
        self._settings = settings

    def _lock_key(self, user_id: str) -> str:
        """Return the composite ``"<guild>:<user>"`` lock key (ADR-0001)."""
        return f"{self._guild_id}:{user_id}"

    async def apply_discipline_penalty(
        self,
        user_id: str,
        reason: DisciplineReason,
    ) -> DisciplineEvent:
        """Drop ``user_id``'s stock by ``settings.discipline_penalty``.

        Mirrors the original bot's ``on_member_update`` discipline branch:
        a flat percentage multiplier (``1 - discipline_penalty``) applied to
        the current price, then floored at ``settings.min_price``. The whole
        read-modify-write happens inside a single per-user
        :meth:`LockManager.locked` critical section so concurrent activity
        ticks or trades cannot clobber the drop.

        Returns a :class:`DisciplineEvent` carrying the pre/post-penalty
        price snapshot and the reason — consumed by the Phase 9 / Phase 12
        Discord notifier. ``opt_in`` is NOT checked: discipline applies
        regardless of whether the user has opted out of being traded into.
        """
        penalty = Decimal(str(self._settings.discipline_penalty))
        min_price = Decimal(str(self._settings.min_price))
        multiplier = Decimal("1") - penalty
        now = datetime.now(tz=UTC)

        async with self._locks.locked(self._lock_key(user_id)):
            stock = await self._price_repo.get(self._guild_id, user_id)
            if stock is None:
                # No stock row → nothing to penalise. Emit a no-op event so
                # the caller still has an audit trail of the attempt; the
                # old/new prices both reflect the floor (the implicit
                # starting price for a never-seen stock).
                return DisciplineEvent(
                    user_id=user_id,
                    reason=reason,
                    old_price=min_price,
                    new_price=min_price,
                    timestamp=now,
                )

            old_price = stock.current
            proposed = _quantise(old_price * multiplier)
            new_price = max(proposed, _quantise(min_price))

            if new_price == old_price:
                # No-op short-circuit (stock already at floor) — skip
                # upsert + history append to keep the price log quiet.
                return DisciplineEvent(
                    user_id=user_id,
                    reason=reason,
                    old_price=old_price,
                    new_price=new_price,
                    timestamp=now,
                )

            new_ath = max(stock.all_time_high, new_price)
            replaced = replace(stock, current=new_price, all_time_high=new_ath)
            await self._price_repo.upsert(self._guild_id, replaced)
            await self._price_repo.append_history(
                self._guild_id,
                user_id,
                PricePoint(price=new_price, timestamp=now),
            )

        return DisciplineEvent(
            user_id=user_id,
            reason=reason,
            old_price=old_price,
            new_price=new_price,
            timestamp=now,
        )
