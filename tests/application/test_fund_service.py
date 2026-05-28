"""Behavioural tests for :class:`FundService` (Phase 8e).

The service mediates the ``/fund create``, ``/fund withdraw``,
``/fund send_events``, ``/fund info`` Discord slash sub-commands, plus the
monthly APY accrual called from the Phase 9 ``MonthlyRolloverTask``.

Acceptance criteria pinned here (work-unit contract E1-E7):

* **E1** — ``create_or_rename`` with no existing fund creates one with a
  default name when ``name=None`` (or with the provided name).
* **E2** — ``create_or_rename`` against an existing fund renames it.
* **E3** — ``withdraw`` on the 1st of the month does NOT apply the
  early-withdrawal penalty (the canonical "no-penalty" calendar day).
* **E4** — ``withdraw`` on any other day applies
  ``settings.early_withdraw_penalty`` (default ``0.05``) by upserting /
  extending a :class:`FundPenalty` row with the configured penalty duration.
* **E5** — ``send_to_events`` transfers from the user's fund to the
  ``events_wallet`` pseudo-fund and SKIPS the early-withdrawal penalty even
  mid-month (the events transfer is exempt by design).
* **E6** — ``accrue_apy(now)`` credits each personal fund with the monthly
  APY amount computed by :func:`fund_math.compute_apy_accrual`
  (annual rate ``settings.hedge_fund_base_apy`` over the ``"monthly"``
  period). The ``events_wallet`` pseudo-fund is skipped.
* **E7** — ``invest(...)`` debits the investor's cash, credits the target
  fund's balance, and records the investor stake (Phase 17b B1 — replaces
  the original §Open-Q5 ``NotImplementedError`` scaffold).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from friendex.application.fund_service import FundService
from friendex.domain.errors import FundInsufficientBalance, InvalidAmount
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    FundPenalty,
    HedgeFund,
    UserAccount,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import (
        FakeFundRepo,
        FakePenaltyRepo,
        FakeUserRepo,
    )


GUILD = "100000000000000001"
USER = "user-1"
OTHER_USER = "user-2"
EVENTS_WALLET_ID = "events_wallet"

_NOW_DAY_1 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
_NOW_MID_MONTH = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _account(
    user_id: str,
    *,
    cash: Decimal = Decimal("0.00"),
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the fund tests."""
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
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
    )


def _fund(
    user_id: str,
    *,
    name: str | None = None,
    cash: Decimal = Decimal("1000.00"),
) -> HedgeFund:
    """Build a personal :class:`HedgeFund` for ``user_id``."""
    return HedgeFund(
        fund_id=user_id,
        name=name if name is not None else f"Fund {user_id}",
        manager_id=user_id,
        cash_balance=cash,
        investors={},
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    fund_repo: FakeFundRepo,
    penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    settings: Settings,
) -> FundService:
    """Construct a :class:`FundService` wired to the shared fixtures."""
    return FundService(
        guild_id=GUILD,
        user_repo=user_repo,
        fund_repo=fund_repo,
        penalty_repo=penalty_repo,
        lock_manager=lock_manager,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# E1 — create with no existing fund
# ---------------------------------------------------------------------------


async def test_e1_create_or_rename_creates_default_named_fund_when_absent(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Without a name, ``create_or_rename`` creates a fund with the default name."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    fund = await service.create_or_rename(USER)

    assert fund.fund_id == USER
    assert fund.manager_id == USER
    assert fund.name == f"Fund {USER}"
    assert fund.cash_balance == Decimal("0.00")

    stored = await fake_fund_repo.get(GUILD, USER)
    assert stored is not None
    assert stored.name == f"Fund {USER}"


async def test_e1_create_or_rename_accepts_provided_name(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """With ``name`` provided, the new fund carries it verbatim."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    fund = await service.create_or_rename(USER, name="Alpha Capital")

    assert fund.name == "Alpha Capital"
    stored = await fake_fund_repo.get(GUILD, USER)
    assert stored is not None
    assert stored.name == "Alpha Capital"


# ---------------------------------------------------------------------------
# E2 — rename existing fund
# ---------------------------------------------------------------------------


async def test_e2_create_or_rename_renames_existing_fund(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """An existing fund's name is updated; balance and investors are preserved."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_fund_repo.upsert(
        GUILD, _fund(USER, name="Old Name", cash=Decimal("2500.00"))
    )
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    fund = await service.create_or_rename(USER, name="New Name")

    assert fund.name == "New Name"
    assert fund.cash_balance == Decimal("2500.00")
    stored = await fake_fund_repo.get(GUILD, USER)
    assert stored is not None
    assert stored.name == "New Name"
    assert stored.cash_balance == Decimal("2500.00")


# ---------------------------------------------------------------------------
# E3 — withdraw on day 1 — NO penalty
# ---------------------------------------------------------------------------


async def test_e3_withdraw_on_day_1_does_not_apply_penalty(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Calendar day 1 is the canonical month-end rollover; no penalty applies."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("100.00")))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("500.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.withdraw(USER, amount=Decimal("200.00"), now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    account_after = await fake_user_repo.get(GUILD, USER)
    penalty_after = await fake_penalty_repo.get(GUILD, USER)

    assert fund_after is not None
    assert account_after is not None
    assert fund_after.cash_balance == Decimal("300.00")
    assert account_after.cash_balance == Decimal("300.00")
    assert penalty_after is None  # NO penalty on day 1


# ---------------------------------------------------------------------------
# E4 — withdraw mid-month applies penalty
# ---------------------------------------------------------------------------


async def test_e4_withdraw_mid_month_applies_penalty(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """A non-day-1 withdrawal records a fresh penalty at the configured rate."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("100.00")))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("500.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.withdraw(USER, amount=Decimal("200.00"), now=_NOW_MID_MONTH)

    penalty_after = await fake_penalty_repo.get(GUILD, USER)
    assert penalty_after is not None
    assert penalty_after.user_id == USER
    assert penalty_after.penalty_apr == Decimal(
        str(default_settings.early_withdraw_penalty)
    )
    expected_until = _NOW_MID_MONTH + timedelta(
        days=default_settings.penalty_duration_days
    )
    assert penalty_after.penalty_until == expected_until


async def test_e4_withdraw_mid_month_stacks_existing_penalty(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Repeat withdrawals stack the APR penalty per spec line 614."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("100.00")))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("500.00")))
    # Pre-seed an active penalty.
    await fake_penalty_repo.upsert(
        GUILD,
        FundPenalty(
            user_id=USER,
            penalty_apr=Decimal("0.05"),
            penalty_until=_NOW_MID_MONTH + timedelta(days=2),
        ),
    )
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.withdraw(USER, amount=Decimal("100.00"), now=_NOW_MID_MONTH)

    penalty_after = await fake_penalty_repo.get(GUILD, USER)
    assert penalty_after is not None
    # 0.05 stacked with the configured 0.05 -> 0.10.
    assert penalty_after.penalty_apr == Decimal("0.05") + Decimal(
        str(default_settings.early_withdraw_penalty)
    )


async def test_withdraw_zero_or_negative_raises_invalid_amount(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Defensive check: amount must be positive."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("0.00")))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("500.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(InvalidAmount):
        await service.withdraw(USER, amount=Decimal("0.00"), now=_NOW_MID_MONTH)
    with pytest.raises(InvalidAmount):
        await service.withdraw(USER, amount=Decimal("-1.00"), now=_NOW_MID_MONTH)


async def test_withdraw_more_than_balance_raises(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Insufficient fund balance is caught and surfaced as a domain error."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("0.00")))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(FundInsufficientBalance):
        await service.withdraw(USER, amount=Decimal("200.00"), now=_NOW_MID_MONTH)


# ---------------------------------------------------------------------------
# E5 — send_to_events skips penalty
# ---------------------------------------------------------------------------


async def test_e5_send_to_events_transfers_and_skips_penalty(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """``send_to_events`` moves cash to ``events_wallet`` without any penalty."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("500.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.send_to_events(USER, amount=Decimal("150.00"))

    fund_after = await fake_fund_repo.get(GUILD, USER)
    events_after = await fake_fund_repo.get(GUILD, EVENTS_WALLET_ID)
    penalty_after = await fake_penalty_repo.get(GUILD, USER)

    assert fund_after is not None
    assert events_after is not None
    assert fund_after.cash_balance == Decimal("350.00")
    assert events_after.cash_balance == Decimal("150.00")
    assert penalty_after is None  # No penalty for events transfers


async def test_send_to_events_zero_or_negative_raises(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Defensive check: send-to-events amount must be positive."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(InvalidAmount):
        await service.send_to_events(USER, amount=Decimal("0.00"))


async def test_send_to_events_insufficient_balance_raises(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Insufficient fund balance for send-to-events surfaces a domain error."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(FundInsufficientBalance):
        await service.send_to_events(USER, amount=Decimal("200.00"))


# ---------------------------------------------------------------------------
# E6 — accrue_apy credits the monthly amount on each personal fund
# ---------------------------------------------------------------------------


async def test_e6_accrue_apy_credits_monthly_amount_to_each_personal_fund(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Each personal fund is credited ``balance * apy / 12`` (Decimal)."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_user_repo.upsert(GUILD, _account(OTHER_USER))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("1200.00")))
    await fake_fund_repo.upsert(GUILD, _fund(OTHER_USER, cash=Decimal("600.00")))
    # Events wallet must be skipped (no APY on the treasury pseudo-fund).
    await fake_fund_repo.ensure_events_wallet(GUILD)
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_user = await fake_fund_repo.get(GUILD, USER)
    fund_other = await fake_fund_repo.get(GUILD, OTHER_USER)
    events = await fake_fund_repo.get(GUILD, EVENTS_WALLET_ID)
    assert fund_user is not None
    assert fund_other is not None
    assert events is not None

    # apy=0.15, monthly => 0.15 / 12 = 0.0125
    # 1200 * 0.0125 = 15.00, 600 * 0.0125 = 7.50
    assert fund_user.cash_balance == Decimal("1215.00")
    assert fund_other.cash_balance == Decimal("607.50")
    # Events wallet untouched.
    assert events.cash_balance == Decimal("0.00")


async def test_accrue_apy_respects_active_penalty(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """An active :class:`FundPenalty` reduces the effective APY for accrual."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("1200.00")))
    # Penalty cuts APY from 0.15 to 0.10 -> monthly 0.10/12 ≈ 0.008333
    await fake_penalty_repo.upsert(
        GUILD,
        FundPenalty(
            user_id=USER,
            penalty_apr=Decimal("0.05"),
            penalty_until=_NOW_DAY_1 + timedelta(days=7),
        ),
    )
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    assert fund_after is not None
    # 1200 * (0.10 / 12) = 10.00
    assert fund_after.cash_balance == Decimal("1210.00")


# ---------------------------------------------------------------------------
# fund_info read path
# ---------------------------------------------------------------------------


async def test_fund_info_returns_none_when_absent(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """``fund_info`` returns ``None`` for a user with no personal fund."""
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    assert await service.fund_info(USER) is None


async def test_fund_info_returns_the_stored_fund(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """``fund_info`` round-trips the stored fund without mutation."""
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("777.77")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    info = await service.fund_info(USER)
    assert info is not None
    assert info.fund_id == USER
    assert info.cash_balance == Decimal("777.77")


# ---------------------------------------------------------------------------
# Phase 17b — /fund invest goes live
# ---------------------------------------------------------------------------


async def test_invest_zero_or_negative_amount_raises_invalid_amount(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B1: ``amount <= 0`` is rejected as ``InvalidAmount``."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(GUILD, _fund(OTHER_USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(InvalidAmount):
        await service.invest(USER, OTHER_USER, Decimal("0.00"))
    with pytest.raises(InvalidAmount):
        await service.invest(USER, OTHER_USER, Decimal("-1.00"))


async def test_invest_missing_fund_raises_invalid_amount(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B1: investing in a non-existent fund raises ``InvalidAmount``."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("500.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(InvalidAmount):
        await service.invest(USER, OTHER_USER, Decimal("100.00"))


async def test_invest_self_invest_is_blocked(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b §Q2: a manager may not invest in their own fund."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(InvalidAmount):
        await service.invest(USER, USER, Decimal("100.00"))


async def test_invest_missing_investor_account_raises_insufficient_funds(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B1: an absent investor account surfaces ``InsufficientFunds``."""
    await fake_fund_repo.upsert(GUILD, _fund(OTHER_USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    from friendex.domain.errors import InsufficientFunds

    with pytest.raises(InsufficientFunds) as excinfo:
        await service.invest(USER, OTHER_USER, Decimal("100.00"))
    assert excinfo.value.need == Decimal("100.00")
    assert excinfo.value.have == Decimal("0.00")


async def test_invest_insufficient_cash_raises_insufficient_funds(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B1: cash below the request raises ``InsufficientFunds``."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("50.00")))
    await fake_fund_repo.upsert(GUILD, _fund(OTHER_USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    from friendex.domain.errors import InsufficientFunds

    with pytest.raises(InsufficientFunds) as excinfo:
        await service.invest(USER, OTHER_USER, Decimal("100.00"))
    assert excinfo.value.need == Decimal("100.00")
    assert excinfo.value.have == Decimal("50.00")


async def test_invest_happy_path_mutates_cash_and_investors(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B1 happy path: debit investor cash, credit fund, record stake."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(GUILD, _fund(OTHER_USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.invest(USER, OTHER_USER, Decimal("150.00"))

    account_after = await fake_user_repo.get(GUILD, USER)
    fund_after = await fake_fund_repo.get(GUILD, OTHER_USER)
    assert account_after is not None
    assert fund_after is not None
    assert account_after.cash_balance == Decimal("350.00")
    assert fund_after.cash_balance == Decimal("250.00")
    assert fund_after.investors == {USER: Decimal("150.00")}


async def test_invest_second_call_accumulates_stake(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B1: a repeat invest increments the existing stake in place."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(GUILD, _fund(OTHER_USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.invest(USER, OTHER_USER, Decimal("100.00"))
    await service.invest(USER, OTHER_USER, Decimal("50.00"))

    fund_after = await fake_fund_repo.get(GUILD, OTHER_USER)
    assert fund_after is not None
    assert fund_after.investors == {USER: Decimal("150.00")}
    assert fund_after.cash_balance == Decimal("250.00")


# ---------------------------------------------------------------------------
# Phase 17b — withdraw caps at fund.cash_balance - sum(investors)
# ---------------------------------------------------------------------------


async def test_withdraw_caps_at_manager_balance_when_investors_present(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B2: manager cannot withdraw past their own balance share."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("0.00")))
    seeded = HedgeFund(
        fund_id=USER,
        name=f"Fund {USER}",
        manager_id=USER,
        cash_balance=Decimal("1000.00"),
        investors={OTHER_USER: Decimal("400.00")},
    )
    await fake_fund_repo.upsert(GUILD, seeded)
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(FundInsufficientBalance) as excinfo:
        await service.withdraw(USER, Decimal("700.00"), now=_NOW_MID_MONTH)
    assert excinfo.value.need == Decimal("700.00")
    assert excinfo.value.have == Decimal("600.00")


async def test_withdraw_at_manager_cap_succeeds_and_preserves_investor_stake(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B2: withdrawing at the manager cap leaves investor stake intact."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("0.00")))
    seeded = HedgeFund(
        fund_id=USER,
        name=f"Fund {USER}",
        manager_id=USER,
        cash_balance=Decimal("1000.00"),
        investors={OTHER_USER: Decimal("400.00")},
    )
    await fake_fund_repo.upsert(GUILD, seeded)
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.withdraw(USER, Decimal("600.00"), now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    account_after = await fake_user_repo.get(GUILD, USER)
    assert fund_after is not None
    assert account_after is not None
    assert fund_after.cash_balance == Decimal("400.00")
    assert fund_after.investors == {OTHER_USER: Decimal("400.00")}
    assert account_after.cash_balance == Decimal("600.00")


# ---------------------------------------------------------------------------
# Phase 17b — accrue_apy splits across manager balance and investor stakes
# ---------------------------------------------------------------------------


async def test_accrue_apy_splits_single_investor_at_annual_period(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B3 (a): split = manager $150 + investor $30 at 15% annual."""
    custom = default_settings.model_copy(
        update={"hedge_fund_base_apy_period": "annual"}
    )
    seeded = HedgeFund(
        fund_id=USER,
        name=f"Fund {USER}",
        manager_id=USER,
        cash_balance=Decimal("1200.00"),
        investors={OTHER_USER: Decimal("200.00")},
    )
    await fake_fund_repo.upsert(GUILD, seeded)
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=custom,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    assert fund_after is not None
    # manager_balance = 1200 - 200 = 1000 -> accrual = 1000 * 0.15 = 150
    # investor accrual = 200 * 0.15 = 30
    # new cash = 1000 + 150 + 230 = 1380
    assert fund_after.cash_balance == Decimal("1380.00")
    assert fund_after.investors == {OTHER_USER: Decimal("230.00")}


async def test_accrue_apy_splits_two_investors_at_annual_period(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Phase 17b B3 (b): two-investor split at 15% annual."""
    custom = default_settings.model_copy(
        update={"hedge_fund_base_apy_period": "annual"}
    )
    investor_a = "investor-A"
    investor_b = "investor-B"
    seeded = HedgeFund(
        fund_id=USER,
        name=f"Fund {USER}",
        manager_id=USER,
        cash_balance=Decimal("3000.00"),
        investors={
            investor_a: Decimal("1000.00"),
            investor_b: Decimal("500.00"),
        },
    )
    await fake_fund_repo.upsert(GUILD, seeded)
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=custom,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    assert fund_after is not None
    # manager_balance = 3000 - 1500 = 1500 -> accrual = 225
    # investor A accrual = 1000 * 0.15 = 150 -> new = 1150
    # investor B accrual = 500 * 0.15 = 75 -> new = 575
    # new cash = 1500 + 225 + 1150 + 575 = 3450
    assert fund_after.cash_balance == Decimal("3450.00")
    assert fund_after.investors == {
        investor_a: Decimal("1150.00"),
        investor_b: Decimal("575.00"),
    }


# ---------------------------------------------------------------------------
# Phase 17a — Open-Q8 toggle: hedge_fund_base_apy_period
# ---------------------------------------------------------------------------


async def test_accrue_apy_uses_annual_period_when_setting_is_annual(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Open-Q8: ``hedge_fund_base_apy_period="annual"`` credits the full APY.

    $100 balance at 0.15 APY accrues $15.00 in a single ``accrue_apy`` call
    when the period setting is ``"annual"`` (vs $1.25 monthly).
    """
    custom = default_settings.model_copy(
        update={"hedge_fund_base_apy_period": "annual"}
    )
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=custom,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    assert fund_after is not None
    # 100 * 0.15 = 15.00 (annual single-shot accrual)
    assert fund_after.cash_balance == Decimal("115.00")


async def test_accrue_apy_uses_monthly_period_by_default(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Open-Q8: default ``"monthly"`` cadence preserves Phase-8e accrual.

    $100 balance at 0.15 APY accrues $1.25 (= 100 * 0.15 / 12) in one call.
    """
    await fake_fund_repo.upsert(GUILD, _fund(USER, cash=Decimal("100.00")))
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    assert fund_after is not None
    # 100 * 0.15 / 12 = 1.25
    assert fund_after.cash_balance == Decimal("101.25")
