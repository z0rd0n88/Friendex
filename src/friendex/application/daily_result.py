"""Frozen result DTO for :meth:`DailyService.claim_daily`.

Carried as a return value from the daily-reward use case and consumed by the
Phase 10 ``/daily`` embed builder. Distinct from the persisted
:class:`~friendex.domain.models.DailyProgress` aggregate — that one tracks the
running streak state on the account, while this DTO captures the *outcome* of
one specific claim (what was credited, what the resulting streak is, whether
the 7-day bonus fired).

Frozen so the embed builder cannot mutate a result mid-render, and to match the
read-model immutability convention established by
:mod:`friendex.application.snapshot_models` and
:mod:`friendex.application.trade_results`.

Decimal at the boundary (Phase 3.1 invariant) — ``reward`` and
``new_cash_balance`` are :class:`~decimal.Decimal` so currency display formats
exactly. ``claim_date`` is a tz-aware UTC :class:`datetime` (the same instant
the caller passed to :meth:`DailyService.claim_daily`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal


@dataclass(frozen=True)
class DailyClaimResult:
    """Outcome of a single ``/daily`` claim.

    ``streak`` is the streak counter *after* the claim is recorded. Per the
    original spec semantics (``original-skeleton.md:976-980``), the counter
    resets to ``0`` immediately after the 7-day bonus fires, so a successful
    streak-bonus claim sets ``streak == 0`` and ``is_streak_bonus == True``.

    ``reward`` is the total amount credited to the user's cash balance for
    this claim (``daily_reward`` on a normal day, ``daily_reward +
    streak_bonus`` on the 7-day-bonus day).

    ``new_cash_balance`` is the user's cash balance *after* the credit lands,
    so the embed builder can render the post-claim balance without a second
    repository read.

    ``claim_date`` is the UTC instant the claim was recorded; the embed
    builder may render it as a relative "claimed at" string.
    """

    user_id: str
    streak: int
    reward: Decimal
    is_streak_bonus: bool
    new_cash_balance: Decimal
    claim_date: datetime
