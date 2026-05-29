"""Behavioural tests for :class:`DailyService` (Phase 8e).

The service owns the ``/daily`` slash command, mediating between the cog
(Phase 11) and the persisted :class:`DailyProgress` state on each
:class:`UserAccount`.

Acceptance criteria pinned here (work-unit contract E8-E11):

* **E8** — First-ever claim credits the configured daily reward, sets
  ``streak == 1``, and reports ``is_streak_bonus == False``.
* **E9** — A second claim on the SAME day raises
  :class:`AlreadyClaimedToday`.
* **E10** — A claim the NEXT day continues the streak (``streak == 2``).
* **E11** — Seven consecutive daily claims fire the streak bonus on day 7:
  the reward is ``daily_reward + streak_bonus``, ``is_streak_bonus == True``,
  and the streak counter resets to ``0`` (matches spec line 980).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import structlog

from friendex.application.daily_result import DailyClaimResult
from friendex.application.daily_service import DailyService
from friendex.domain.errors import AlreadyClaimedToday
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    UserAccount,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import FakeUserRepo


GUILD = "100000000000000001"
USER = "user-1"

_DAY_0 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_DAY_1 = _DAY_0 + timedelta(days=1)
_SAME_DAY = _DAY_0 + timedelta(hours=2)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _account(
    user_id: str,
    *,
    cash: Decimal = Decimal("0.00"),
    last_claim: datetime | None = None,
    streak: int = 0,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the daily tests."""
    now = datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=cash,
        month_start_net_worth=cash,
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=last_claim, streak=streak),
        last_activity=now,
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    lock_manager: LockManager,
    settings: Settings,
) -> DailyService:
    """Construct a :class:`DailyService` wired to the shared fixtures."""
    return DailyService(
        guild_id=GUILD,
        user_repo=user_repo,
        lock_manager=lock_manager,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# E8 — first claim credits daily reward, streak=1, no bonus
# ---------------------------------------------------------------------------


async def test_e8_first_claim_credits_daily_reward(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """First-ever claim credits ``daily_reward`` and sets ``streak == 1``."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    result = await service.claim_daily(USER, now=_DAY_0)

    expected_reward = Decimal(str(default_settings.daily_reward))
    assert isinstance(result, DailyClaimResult)
    assert result.user_id == USER
    assert result.reward == expected_reward
    assert result.streak == 1
    assert result.is_streak_bonus is False
    assert result.new_cash_balance == Decimal("100.00") + expected_reward
    assert result.claim_date == _DAY_0

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.cash_balance == Decimal("100.00") + expected_reward
    assert account.daily.last_claim == _DAY_0
    assert account.daily.streak == 1


# ---------------------------------------------------------------------------
# E9 — second claim same day raises AlreadyClaimedToday
# ---------------------------------------------------------------------------


async def test_e9_second_claim_same_day_raises(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A repeat claim within the 24-hour window raises :class:`AlreadyClaimedToday`."""
    await fake_user_repo.upsert(GUILD, _account(USER, last_claim=_DAY_0, streak=1))
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(AlreadyClaimedToday):
        await service.claim_daily(USER, now=_SAME_DAY)

    # Cash balance is untouched.
    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.cash_balance == Decimal("0.00")
    assert account.daily.streak == 1


# ---------------------------------------------------------------------------
# E10 — claim next day continues streak
# ---------------------------------------------------------------------------


async def test_e10_next_day_claim_continues_streak(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A claim 24h+ later continues the streak (``streak == 2``)."""
    await fake_user_repo.upsert(GUILD, _account(USER, last_claim=_DAY_0, streak=1))
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    result = await service.claim_daily(USER, now=_DAY_1)

    assert result.streak == 2
    assert result.is_streak_bonus is False
    assert result.reward == Decimal(str(default_settings.daily_reward))


async def test_skipping_a_day_resets_streak_to_1(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A 48h+ gap resets the streak (matches spec line 962-965)."""
    await fake_user_repo.upsert(GUILD, _account(USER, last_claim=_DAY_0, streak=3))
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    # Gap of >= 2 days breaks the streak.
    result = await service.claim_daily(USER, now=_DAY_0 + timedelta(days=2, hours=1))

    assert result.streak == 1
    assert result.is_streak_bonus is False


# ---------------------------------------------------------------------------
# Issue #84 L (silent-failures branch): streak-reset debug log
#
# A common support question is "why did my 6-day streak reset?". The user
# crossed the 48-hour boundary and the streak silently reset to 1. Emit a
# structured DEBUG-level log so the operator can correlate the reset with
# the user's claim history without re-running the math by hand.


def _rebind_daily_service_logger() -> None:
    """Rebind ``daily_service._log`` to a fresh proxy at default levels.

    ``adapters.config.configure_logging`` (exercised by the
    ``test_configure_logging_*`` tests) installs a filtering wrapper-class
    via ``structlog.make_filtering_bound_logger(INFO)``. Once a module-level
    ``structlog.get_logger`` has bound against that filter, subsequent
    ``capture_logs`` calls inherit the filter and DEBUG events are dropped.
    The pragmatic fix in tests is to ``reset_defaults`` and *rebind* the
    module attribute so the proxy re-resolves against the now-default
    (unfiltered) wrapper class.
    """
    import friendex.application.daily_service as ds_module

    structlog.reset_defaults()
    ds_module._log = structlog.get_logger("friendex.application.daily_service")


async def test_streak_reset_at_48h_boundary_logs_debug_event(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Crossing the 48-hour boundary emits ``daily_streak_reset`` at debug."""
    _rebind_daily_service_logger()
    await fake_user_repo.upsert(GUILD, _account(USER, last_claim=_DAY_0, streak=6))
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with structlog.testing.capture_logs() as captured:
        await service.claim_daily(USER, now=_DAY_0 + timedelta(days=2, hours=1))

    reset_records = [
        r
        for r in captured
        if r.get("event") == "daily_streak_reset" and r.get("log_level") == "debug"
    ]
    assert reset_records, "expected a debug log entry for the streak reset"
    rec = reset_records[0]
    assert rec["user_id"] == USER
    assert rec["guild_id"] == GUILD
    assert rec["previous_streak"] == 6


async def test_streak_continuation_does_not_log_reset(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A continuation (within 24-48h) does NOT emit the reset debug event."""
    _rebind_daily_service_logger()
    await fake_user_repo.upsert(GUILD, _account(USER, last_claim=_DAY_0, streak=3))
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with structlog.testing.capture_logs() as captured:
        await service.claim_daily(USER, now=_DAY_1)

    reset_records = [r for r in captured if r.get("event") == "daily_streak_reset"]
    assert reset_records == []


# ---------------------------------------------------------------------------
# E11 — day-7 streak bonus + reset to 0
# ---------------------------------------------------------------------------


async def test_e11_day_seven_streak_bonus_and_reset(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Seven consecutive claims fire the streak bonus on day 7 and reset to 0."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("0.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    daily_reward = Decimal(str(default_settings.daily_reward))
    streak_bonus = Decimal(str(default_settings.streak_bonus))
    last_result: DailyClaimResult | None = None
    expected_cash = Decimal("0.00")

    for day_index in range(7):
        now = _DAY_0 + timedelta(days=day_index)
        last_result = await service.claim_daily(USER, now=now)
        if day_index < 6:
            expected_cash += daily_reward
        else:
            expected_cash += daily_reward + streak_bonus

    assert last_result is not None
    assert last_result.is_streak_bonus is True
    assert last_result.reward == daily_reward + streak_bonus
    # Streak resets to 0 immediately after the bonus fires (spec line 980).
    assert last_result.streak == 0
    assert last_result.new_cash_balance == expected_cash

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.cash_balance == expected_cash
    assert account.daily.streak == 0


# ---------------------------------------------------------------------------
# Missing account creates a default on claim
# ---------------------------------------------------------------------------


async def test_claim_creates_account_for_unknown_user(
    fake_user_repo: FakeUserRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """An unseen user gets a default account so first ``/daily`` works."""
    service = _make_service(
        user_repo=fake_user_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    result = await service.claim_daily(USER, now=_DAY_0)

    initial_cash = Decimal(str(default_settings.initial_cash))
    daily_reward = Decimal(str(default_settings.daily_reward))
    assert result.streak == 1
    assert result.reward == daily_reward
    assert result.new_cash_balance == initial_cash + daily_reward

    account = await fake_user_repo.get(GUILD, USER)
    assert account is not None
    assert account.cash_balance == initial_cash + daily_reward
