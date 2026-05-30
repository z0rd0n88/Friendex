"""Shared default-account seeding for first-time-seen users.

A never-before-seen user must be seeded with the configured initial cash,
empty positions, fresh zeroed today/week activity buckets, and a
``DailyProgress`` carrying ``last_claim=None, streak=0`` so any service that
needs to act on them (a daily claim, an activity-tick credit, a trade) can
proceed without a prior write.

Pre-#82 H16 every service that touched a possibly-new user carried its own
copy of this seed-shaped block (``TradingService._resolve_user``,
``DailyService._get_or_create_account``, ``ActivityService._get_or_create``,
``VoicePingService._get_or_create``). The shape was identical across all
four except for tiny incidental drift (``DailyService`` and
``TradingService`` quantised ``initial_cash`` to cents; the other two did
not). Consolidating into this single helper bakes the cents-quantised
``initial_cash`` shape in for everybody, eliminating the drift surface.

This helper does NOT persist the account — callers decide whether to
``upsert`` the seeded stub (e.g. ``TradingService.short`` persists the
target's stub eagerly so the opt-in check on a second call is sticky;
``ActivityService._mutate`` writes the seeded-and-mutated account in one
final upsert at the end of its critical section).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    UserAccount,
)
from friendex.domain.price_engine import quantise

if TYPE_CHECKING:
    from friendex.adapters.config import Settings


def seed_user_account(
    user_id: str,
    settings: Settings,
    now: datetime | None = None,
) -> UserAccount:
    """Return a fresh default :class:`UserAccount` for ``user_id``.

    Mirrors the original ``ensure_user`` shape from the monolith spec
    (``original-skeleton.md`` §USER ENSURE): initial cash from ``settings``
    (quantised to cents), empty long / short dicts, fresh zeroed today /
    week :class:`ActivityBucket` instances rooted at ``now``, a fresh
    :class:`DailyProgress` with no prior claim, and ``last_activity = now``.
    ``opt_in`` defaults to ``True`` (a brand-new user is tradable by
    default; opt-out is an explicit user action).

    ``now`` defaults to ``datetime.now(tz=UTC)`` when omitted so tests can
    pin a deterministic instant.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    initial_cash = quantise(Decimal(str(settings.initial_cash)))
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
