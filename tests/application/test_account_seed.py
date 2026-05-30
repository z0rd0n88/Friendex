"""Tests for ``friendex.application.account_seed.seed_user_account`` (#82 H16).

Pre-fix four services carried near-identical copies of the same default-
account seed block; consolidating into one helper eliminates the drift
risk (``DailyService`` quantised initial cash to cents, two others did
not). These tests pin the canonical shape so any future divergence lands
here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from friendex.application.account_seed import seed_user_account
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    UserAccount,
)


class _StubSettings:
    """Minimal :class:`Settings` stand-in for the seed helper.

    The helper only reads ``settings.initial_cash``; using a stub keeps the
    test independent of the full ``Settings`` model and its environment
    discovery so it pins the seed shape, not the config loader.
    """

    def __init__(self, initial_cash: float = 10_000.0) -> None:
        self.initial_cash = initial_cash


_NOW = datetime(2026, 5, 30, 12, 0, 0, tzinfo=UTC)


def test_seed_returns_user_account_with_initial_cash() -> None:
    settings = _StubSettings(initial_cash=10_000.0)
    account = seed_user_account("u1", settings, now=_NOW)

    assert isinstance(account, UserAccount)
    assert account.user_id == "u1"
    assert account.cash_balance == Decimal("10000.00")
    assert account.net_worth == Decimal("10000.00")
    assert account.month_start_net_worth == Decimal("10000.00")


def test_seed_quantises_initial_cash_to_cents() -> None:
    """Pre-consolidation two of the four service copies skipped this
    quantisation step. The consolidated helper applies it uniformly so
    every freshly-seeded account starts at cent precision regardless of
    which entry point first sees the user.
    """
    settings = _StubSettings(initial_cash=10_000.123)
    account = seed_user_account("u1", settings, now=_NOW)
    # Cent-quantised under banker's rounding (10000.123 → 10000.12).
    assert account.cash_balance == Decimal("10000.12")


def test_seed_buckets_anchored_at_now() -> None:
    settings = _StubSettings()
    account = seed_user_account("u1", settings, now=_NOW)
    assert isinstance(account.today, ActivityBucket)
    assert isinstance(account.week, ActivityBucket)
    assert account.today.bucket_start == _NOW
    assert account.week.bucket_start == _NOW
    assert account.last_activity == _NOW


def test_seed_daily_progress_clean() -> None:
    settings = _StubSettings()
    account = seed_user_account("u1", settings, now=_NOW)
    assert isinstance(account.daily, DailyProgress)
    assert account.daily.last_claim is None
    assert account.daily.streak == 0


def test_seed_positions_empty() -> None:
    settings = _StubSettings()
    account = seed_user_account("u1", settings, now=_NOW)
    assert account.long_positions == {}
    assert account.short_positions == {}


def test_seed_opt_in_default_is_true() -> None:
    """Brand-new users are tradable by default; opt-out is an explicit
    user action (the previous service copies all relied on the dataclass
    default — the consolidated helper preserves that)."""
    settings = _StubSettings()
    account = seed_user_account("u1", settings, now=_NOW)
    assert account.opt_in is True


def test_seed_now_defaults_to_current_utc() -> None:
    """``now`` is optional; without it the helper samples
    ``datetime.now(tz=UTC)``. Pin that the omitted-now path produces a
    tz-aware UTC instant — the Phase 3.1 invariant.
    """
    settings = _StubSettings()
    account = seed_user_account("u1", settings)
    assert account.today.bucket_start.tzinfo is UTC
    assert account.last_activity.tzinfo is UTC


def test_seed_is_pure_does_not_persist_or_share_state() -> None:
    """Two seed calls produce two distinct objects with independent
    mutable child containers (no aliasing between accounts)."""
    settings = _StubSettings()
    a = seed_user_account("u1", settings, now=_NOW)
    b = seed_user_account("u2", settings, now=_NOW)
    assert a is not b
    assert a.long_positions is not b.long_positions
    assert a.short_positions is not b.short_positions
    assert a.today is not b.today
    assert a.week is not b.week
