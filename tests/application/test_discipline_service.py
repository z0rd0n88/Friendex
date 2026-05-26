"""Behavioural tests for :class:`DisciplineService` (Phase 8f).

The discipline service applies a flat-percentage price penalty to a user's
stock when they incur a Discord-side discipline event (timeout or ban). The
penalty drops the stock by ``settings.discipline_penalty`` (default 17%) and
floors the result at ``settings.min_price``.

Acceptance criteria pinned here:

* **F5** — ``timeout`` drops the user's stock by 17%.
* **F6** — ``ban`` drops the user's stock by 17%.
* **F7** — ``min_price`` floor enforced: a stock already near the floor
  falls only to the floor, not below.
* **F8** — opt-OUT user's stock is STILL affected. ``opt_in`` only gates
  being **traded into**, not having disciplinary penalties applied.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.discipline_service import DisciplineService
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    Stock,
    UserAccount,
)

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import (
        FakePriceRepo,
        FakeUserRepo,
    )


GUILD = "100000000000000001"
USER = "user-disciplined"


def _account(
    user_id: str,
    *,
    opt_in: bool = True,
    last_activity: datetime | None = None,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the discipline tests."""
    now = last_activity if last_activity is not None else datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
        opt_in=opt_in,
    )


def _stock(user_id: str, *, current: Decimal) -> Stock:
    """Build a minimal valid :class:`Stock` with empty history."""
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    lock_manager: LockManager,
    settings: Settings,
) -> DisciplineService:
    """Construct the service under test with explicit dependencies."""
    return DisciplineService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        lock_manager=lock_manager,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# F5 — timeout drops the user's stock by 17%
# ---------------------------------------------------------------------------


async def test_timeout_drops_stock_by_discipline_penalty(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F5: timeout drops a $100 stock by 17% → $83.00."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_price_repo.upsert(GUILD, _stock(USER, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    event = await service.apply_discipline_penalty(USER, reason="timeout")

    after = await fake_price_repo.get(GUILD, USER)
    assert after is not None
    # 17% drop on $100.00 → $83.00 (penalty * current applied flat, then floor).
    assert after.current == Decimal("83.00")
    assert event.user_id == USER
    assert event.reason == "timeout"
    assert event.old_price == Decimal("100.00")
    assert event.new_price == Decimal("83.00")

    # History was appended atomically.
    history = await fake_price_repo.get_history(GUILD, USER)
    assert len(history) == 1
    assert history[0].price == Decimal("83.00")


# ---------------------------------------------------------------------------
# F6 — ban drops the user's stock by 17%
# ---------------------------------------------------------------------------


async def test_ban_drops_stock_by_discipline_penalty(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F6: ban drops a $200 stock by 17% → $166.00 (same math as timeout)."""
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_price_repo.upsert(GUILD, _stock(USER, current=Decimal("200.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    event = await service.apply_discipline_penalty(USER, reason="ban")

    after = await fake_price_repo.get(GUILD, USER)
    assert after is not None
    # 200 * (1 - 0.17) = 166.00 exact (no quantisation drift).
    assert after.current == Decimal("166.00")
    assert event.reason == "ban"
    assert event.old_price == Decimal("200.00")
    assert event.new_price == Decimal("166.00")


# ---------------------------------------------------------------------------
# F7 — min_price floor enforced
# ---------------------------------------------------------------------------


async def test_min_price_floor_enforced_when_near_floor(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F7: a stock near the $70 floor drops only to the floor, not below.

    Pre-condition: ``settings.min_price == 70.00`` (default). Start the stock
    at $75: a 17% drop would yield $62.25, which the floor must clamp to
    exactly $70.00.
    """
    assert default_settings.min_price == 70.0
    await fake_user_repo.upsert(GUILD, _account(USER))
    await fake_price_repo.upsert(GUILD, _stock(USER, current=Decimal("75.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    event = await service.apply_discipline_penalty(USER, reason="timeout")

    after = await fake_price_repo.get(GUILD, USER)
    assert after is not None
    # 75 * (1 - 0.17) = 62.25 → clamped to 70.00 (the floor).
    assert after.current == Decimal("70.00")
    assert event.new_price == Decimal("70.00")
    assert event.old_price == Decimal("75.00")


# ---------------------------------------------------------------------------
# F8 — opt-out user's stock is still affected
# ---------------------------------------------------------------------------


async def test_optout_user_stock_still_disciplined(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F8: opt-out does NOT exempt the user from discipline penalties.

    ``opt_in`` only controls whether the user can be **traded into** (Phase
    8c rejects buy/sell/short/cover with :class:`OptedOut` when the target
    has ``opt_in=False``). Disciplinary action by definition is applied to
    the user's own stock regardless of consent — they still get the drop.
    """
    await fake_user_repo.upsert(GUILD, _account(USER, opt_in=False))
    await fake_price_repo.upsert(GUILD, _stock(USER, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    event = await service.apply_discipline_penalty(USER, reason="ban")

    after = await fake_price_repo.get(GUILD, USER)
    assert after is not None
    assert after.current == Decimal("83.00")
    assert event.new_price == Decimal("83.00")
