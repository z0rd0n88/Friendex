"""Atomicity & invariant tests for :class:`FundService` (Wave 1 remediation).

Pins the post-fix contract for the items addressed in
``docs/reviews/remediation-plan.md`` `fix/money-atomicity`:

* **#82 H1** — ``send_to_events`` guards against draining investor principal
  by comparing ``manager_balance = cash_balance - sum(investors.values())``
  to the requested amount, mirroring the same guard already in
  ``withdraw``.
* **#82 H3** — APY accrual sums unquantised per-investor :class:`Decimal`
  values and quantises the total once, so half-cent rounding errors do
  not pile up across many investors.
* **#84 H** — ``invest`` self-block compares ``actor.id ==
  loaded_fund.manager.id`` instead of comparing the actor id directly
  against the fund id; a fund with ``fund_id != manager_id`` (e.g. the
  ``events_wallet`` pseudo-fund) must NOT block an investor whose id
  happens to equal its non-manager fund id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from friendex.adapters.config import Settings
from friendex.application.fund_service import FundService
from friendex.domain.errors import FundInsufficientBalance, InvalidAmount
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    HedgeFund,
    UserAccount,
)

if TYPE_CHECKING:
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import (
        FakeFundRepo,
        FakePenaltyRepo,
        FakeUserRepo,
    )


GUILD = "100000000000000001"
USER = "user-1"
OTHER_USER = "user-2"

_NOW_DAY_1 = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)


def _account(user_id: str, *, cash: Decimal = Decimal("0.00")) -> UserAccount:
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


def _make_service(
    *,
    user_repo: FakeUserRepo,
    fund_repo: FakeFundRepo,
    penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    settings: Settings,
) -> FundService:
    return FundService(
        guild_id=GUILD,
        user_repo=user_repo,
        fund_repo=fund_repo,
        penalty_repo=penalty_repo,
        lock_manager=lock_manager,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# #82 H1 — send_to_events guards manager balance, not raw cash balance
# ---------------------------------------------------------------------------


async def test_send_to_events_rejects_amount_above_manager_balance(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H1: ``send_to_events`` must NOT let the manager drain investor principal.

    Fund has $1000 cash with $400 of investor stake — the manager's own
    share is $600. A request for $700 must be rejected with
    :class:`FundInsufficientBalance` even though the raw cash balance
    nominally covers it.
    """
    await fake_user_repo.upsert(GUILD, _account(USER))
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
        await service.send_to_events(USER, amount=Decimal("700.00"))
    assert excinfo.value.need == Decimal("700.00")
    assert excinfo.value.have == Decimal("600.00")


async def test_send_to_events_at_manager_cap_succeeds_and_preserves_stake(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H1: sending exactly the manager-balance amount leaves investor stake intact."""
    await fake_user_repo.upsert(GUILD, _account(USER))
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

    await service.send_to_events(USER, amount=Decimal("600.00"))

    fund_after = await fake_fund_repo.get(GUILD, USER)
    events_after = await fake_fund_repo.get(GUILD, "events_wallet")
    assert fund_after is not None
    assert events_after is not None
    assert fund_after.cash_balance == Decimal("400.00")
    assert fund_after.investors == {OTHER_USER: Decimal("400.00")}
    assert events_after.cash_balance == Decimal("600.00")


# ---------------------------------------------------------------------------
# #82 H3 — APY accrual quantises the sum, not each individual stake
# ---------------------------------------------------------------------------


async def test_accrue_apy_quantises_total_not_per_investor(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H3: per-investor stake accruals are accumulated unquantised; the SUM is
    quantised once.

    Construct three investors whose individual monthly accruals each end
    in ``0.005`` so banker's rounding quantises each stake's stored
    delta down to a flat cent (.00 / .01 alternating). With monthly APY
    ``0.15 / 12 = 0.0125`` the choice of stake ``$0.40`` produces an
    accrual of exactly ``$0.005`` per stake — pre-fix each rounded to
    ``$0.00`` (a flat loss of half a cent times three); post-fix the
    sum is quantised once as ``$0.02``.

    Default ``apy_residual_recipient="manager"`` so the quantised residual
    flows to the manager balance (raw cash) — making the fund's total
    cash_balance increase by the full sum even though no single investor
    stake moved.
    """
    seeded = HedgeFund(
        fund_id=USER,
        name=f"Fund {USER}",
        manager_id=USER,
        cash_balance=Decimal("1.20"),
        investors={
            "investor-a": Decimal("0.40"),
            "investor-b": Decimal("0.40"),
            "investor-c": Decimal("0.40"),
        },
    )
    await fake_fund_repo.upsert(GUILD, seeded)
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
    # Manager balance is 0 (1.20 - 3 * 0.40); only investors accrue.
    # Per-investor raw accrual: 0.40 * 0.0125 = 0.005.
    # Pre-fix: each rounds to 0.00, total accrual is 0.00.
    # Post-fix: 3 * 0.005 = 0.015 -> quantise to 0.02 by banker's rounding.
    # Cash balance gains the full quantised sum (no manager component here).
    assert fund_after.cash_balance == Decimal("1.22")
    # Investor stakes do NOT move at this sub-cent regime — they all stay
    # at the seeded $0.40 (cents precision).
    assert fund_after.investors == {
        "investor-a": Decimal("0.40"),
        "investor-b": Decimal("0.40"),
        "investor-c": Decimal("0.40"),
    }


# ---------------------------------------------------------------------------
# H3 residual recipient — manager / treasury / drop branches
# ---------------------------------------------------------------------------


def _override_recipient(
    base: Settings,
    recipient: str,
) -> Settings:
    """Build a fresh ``Settings`` with the residual-recipient overridden."""
    return Settings(
        discord_token="test-token",  # type: ignore[call-arg]
        apy_residual_recipient=recipient,  # type: ignore[arg-type]
        _env_file=None,  # type: ignore[call-arg]
    )


async def test_accrue_apy_residual_recipient_treasury_routes_to_events_wallet(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H3 residual: treasury setting credits the residual to ``events_wallet``.

    Same sub-cent-investor scenario as
    :func:`test_accrue_apy_quantises_total_not_per_investor`. With the
    residual recipient set to ``"treasury"`` the manager balance does
    NOT receive the residual; the per-guild ``events_wallet`` pseudo-fund
    gains it instead.
    """
    seeded = HedgeFund(
        fund_id=USER,
        name=f"Fund {USER}",
        manager_id=USER,
        cash_balance=Decimal("1.20"),
        investors={
            "investor-a": Decimal("0.40"),
            "investor-b": Decimal("0.40"),
            "investor-c": Decimal("0.40"),
        },
    )
    await fake_fund_repo.upsert(GUILD, seeded)
    treasury_settings = _override_recipient(default_settings, "treasury")
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=treasury_settings,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    events_after = await fake_fund_repo.get(GUILD, "events_wallet")
    assert fund_after is not None
    assert events_after is not None
    # Fund cash stays put (the residual went to the treasury).
    assert fund_after.cash_balance == Decimal("1.20")
    # Investor stakes unchanged (same as manager branch).
    assert fund_after.investors == {
        "investor-a": Decimal("0.40"),
        "investor-b": Decimal("0.40"),
        "investor-c": Decimal("0.40"),
    }
    # The residual ($0.02 via the sub-cent math) lands in the events wallet.
    assert events_after.cash_balance == Decimal("0.02")


async def test_accrue_apy_residual_recipient_drop_discards_residual(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H3 residual: drop setting discards the residual entirely.

    Same sub-cent-investor scenario; with the residual recipient set to
    ``"drop"`` neither the manager balance nor the events wallet
    receives the fractional accrual — the sub-cent sum is lost on the
    floor, matching the pre-H3 original-monolith behaviour.
    """
    seeded = HedgeFund(
        fund_id=USER,
        name=f"Fund {USER}",
        manager_id=USER,
        cash_balance=Decimal("1.20"),
        investors={
            "investor-a": Decimal("0.40"),
            "investor-b": Decimal("0.40"),
            "investor-c": Decimal("0.40"),
        },
    )
    await fake_fund_repo.upsert(GUILD, seeded)
    drop_settings = _override_recipient(default_settings, "drop")
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=drop_settings,
    )

    await service.accrue_apy(now=_NOW_DAY_1)

    fund_after = await fake_fund_repo.get(GUILD, USER)
    events_after = await fake_fund_repo.get(GUILD, "events_wallet")
    assert fund_after is not None
    # Fund cash stays exactly at the seeded value; the residual evaporated.
    assert fund_after.cash_balance == Decimal("1.20")
    # Investor stakes unchanged.
    assert fund_after.investors == {
        "investor-a": Decimal("0.40"),
        "investor-b": Decimal("0.40"),
        "investor-c": Decimal("0.40"),
    }
    # Events wallet is NEVER created in drop mode (no treasury credit ever lands).
    assert events_after is None


# ---------------------------------------------------------------------------
# #84 H — invest self-block: compare actor.id to manager.id, not fund.id
# ---------------------------------------------------------------------------


async def test_invest_self_block_uses_manager_id_not_fund_id(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """#84 H: the self-invest block must compare the actor's id to the fund's
    manager id, not the fund's id.

    Stand up a fund whose ``fund_id`` ("vault-1") differs from its
    ``manager_id`` ("manager-1"). An investor named "vault-1" (same id
    as the fund) must be permitted to invest because they are not the
    manager; an investor named "manager-1" must be blocked.
    """
    fund_id = "vault-1"
    manager_id = "manager-1"
    investor_id = fund_id  # deliberately equal to fund_id, NOT manager_id

    await fake_user_repo.upsert(GUILD, _account(investor_id, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(
        GUILD,
        HedgeFund(
            fund_id=fund_id,
            name="Vault",
            manager_id=manager_id,
            cash_balance=Decimal("100.00"),
            investors={},
        ),
    )
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    await service.invest(investor_id, fund_id, Decimal("100.00"))

    fund_after = await fake_fund_repo.get(GUILD, fund_id)
    assert fund_after is not None
    assert fund_after.investors == {investor_id: Decimal("100.00")}


async def test_invest_manager_still_blocked_from_own_fund(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """#84 H: a true manager-self-invest is still rejected."""
    fund_id = "vault-1"
    manager_id = "manager-1"

    await fake_user_repo.upsert(GUILD, _account(manager_id, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(
        GUILD,
        HedgeFund(
            fund_id=fund_id,
            name="Vault",
            manager_id=manager_id,
            cash_balance=Decimal("100.00"),
            investors={},
        ),
    )
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(InvalidAmount):
        await service.invest(manager_id, fund_id, Decimal("100.00"))


async def test_invest_personal_fund_self_block_still_works(
    fake_user_repo: FakeUserRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """For personal funds (``fund_id == manager_id``), the block still fires."""
    await fake_user_repo.upsert(GUILD, _account(USER, cash=Decimal("500.00")))
    await fake_fund_repo.upsert(
        GUILD,
        HedgeFund(
            fund_id=USER,
            name=f"Fund {USER}",
            manager_id=USER,
            cash_balance=Decimal("100.00"),
            investors={},
        ),
    )
    service = _make_service(
        user_repo=fake_user_repo,
        fund_repo=fake_fund_repo,
        penalty_repo=fake_penalty_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with pytest.raises(InvalidAmount):
        await service.invest(USER, USER, Decimal("100.00"))
