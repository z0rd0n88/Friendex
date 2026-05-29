"""Atomicity & invariant tests for :class:`TradingService` (Wave 1 remediation).

Pins the post-fix contract for the items addressed in
``docs/reviews/remediation-plan.md`` `fix/money-atomicity`:

* **#82 C1** — ``_set_cooldown`` runs inside the same critical section as the
  pre-lock cooldown check (no concurrent shorts can both pass).
* **#82 C2** — the public ``short`` / ``_cover_internal`` body runs inside a
  single :class:`IUnitOfWork` transaction; mid-sequence failure rolls every
  write back rather than destroying money.
* **#82 H2** — full-cover path releases the exact ``locked_cash`` /
  ``locked_fund`` recorded on the position, and the partial-cover
  proportional path preserves the invariant
  ``locked_cash + locked_fund == shares * entry_price`` across a sequence
  of partial covers (property-style test).
* **#82 M12** — cooldown ``expires_at`` is anchored to the time sampled
  *inside* the critical section, not before lock acquisition.
* **#84 H (ghost fund)** — ``_get_fund_cash`` propagates the persistence
  exception rather than silently swallowing it and creating a phantom
  $0 fund.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from freezegun import freeze_time

from friendex.application.trading_service import TradingService
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    HedgeFund,
    LongPosition,
    ShortPosition,
    Stock,
    UserAccount,
)
from tests.application.fakes.fake_unit_of_work import FakeUnitOfWork

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.lock_manager import LockManager
    from tests.application.fakes.fake_repos import (
        FakeFundRepo,
        FakePriceRepo,
        FakeTradeCooldownRepo,
        FakeUserRepo,
    )


GUILD = "100000000000000001"
SHORTER = "shorter-1"
COVERER = "coverer-1"
TARGET = "target-1"

WEEKDAY_OPEN = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)


def _account(
    user_id: str,
    *,
    cash: Decimal = Decimal("10000.00"),
    long_positions: dict[str, LongPosition] | None = None,
    short_positions: dict[str, ShortPosition] | None = None,
    opt_in: bool = True,
) -> UserAccount:
    now = datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=cash,
        month_start_net_worth=cash,
        long_positions=long_positions or {},
        short_positions=short_positions or {},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
        opt_in=opt_in,
    )


def _stock(user_id: str, *, current: Decimal = Decimal("100.00")) -> Stock:
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _fund(user_id: str, *, cash: Decimal) -> HedgeFund:
    return HedgeFund(
        fund_id=user_id,
        name=user_id,
        manager_id=user_id,
        cash_balance=cash,
        investors={},
    )


def _make_service(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    fund_repo: FakeFundRepo,
    cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    settings: Settings,
    unit_of_work: object | None = None,
) -> TradingService:
    return TradingService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=lock_manager,
        settings=settings,
        unit_of_work=unit_of_work,
    )


# ---------------------------------------------------------------------------
# #82 C1 — concurrent short calls cannot both bypass the cooldown
# ---------------------------------------------------------------------------


async def test_concurrent_shorts_serialize_so_second_call_sees_cooldown(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C1: two coroutines racing on ``short`` must NOT both succeed.

    Pre-fix both calls passed the pre-lock cooldown check before either
    wrote the cooldown row — so the second short went through. After
    moving ``_set_cooldown`` inside the locked critical section, the
    second call observes the cooldown row written by the first and
    raises :class:`OnCooldown`.
    """
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("10000.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("10000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    from friendex.domain.errors import OnCooldown

    with freeze_time(WEEKDAY_OPEN):
        results = await asyncio.gather(
            service.short(SHORTER, TARGET, 1),
            service.short(SHORTER, TARGET, 1),
            return_exceptions=True,
        )

    successes = [r for r in results if not isinstance(r, Exception)]
    cooldowns = [r for r in results if isinstance(r, OnCooldown)]
    assert len(successes) == 1
    assert len(cooldowns) == 1


# ---------------------------------------------------------------------------
# #82 C2 — mid-sequence failure rolls money back via the unit of work
# ---------------------------------------------------------------------------


class _ExplodingPriceRepo:
    """Decorator price repo that delegates to the inner fake then explodes.

    Used to simulate a mid-sequence persistence failure inside
    ``short`` after the user cash has been debited and the fund
    collateral locked. With the UoW seam in place, the
    :class:`FakeUnitOfWork` rolls every fake repo back to its
    pre-transaction snapshot so the shorter's cash and the fund's
    cash both return to their starting values.
    """

    def __init__(self, inner: FakePriceRepo, fail_after: int = 1) -> None:
        self._inner = inner
        self._writes = 0
        self._fail_after = fail_after

    async def get(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.get(*args, **kwargs)

    async def list_all(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.list_all(*args, **kwargs)

    async def upsert(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._writes += 1
        if self._writes > self._fail_after:
            raise RuntimeError("simulated persistence failure")
        return await self._inner.upsert(*args, **kwargs)

    async def append_history(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._writes += 1
        if self._writes > self._fail_after:
            raise RuntimeError("simulated persistence failure")
        return await self._inner.append_history(*args, **kwargs)

    async def get_history(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.get_history(*args, **kwargs)

    async def prune_history_older_than(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.prune_history_older_than(*args, **kwargs)

    async def delete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        return await self._inner.delete(*args, **kwargs)


async def test_short_rolls_back_user_and_fund_when_price_write_fails(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C2: a mid-sequence persistence failure during ``short`` must roll back
    every prior write — cash and fund balances are restored, no short
    position is left dangling, and no cooldown row is written.
    """
    starting_cash = Decimal("400.00")
    starting_fund = Decimal("2000.00")
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=starting_cash))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=starting_fund))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    exploding_price_repo = _ExplodingPriceRepo(fake_price_repo, fail_after=1)
    uow = FakeUnitOfWork(
        fake_user_repo, fake_fund_repo, fake_price_repo, fake_cooldown_repo
    )
    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=exploding_price_repo,  # type: ignore[arg-type]
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
        unit_of_work=uow,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(RuntimeError):
        await service.short(SHORTER, TARGET, 10)

    after_shorter = await fake_user_repo.get(GUILD, SHORTER)
    after_fund = await fake_fund_repo.get(GUILD, SHORTER)
    after_cooldown = await fake_cooldown_repo.get(GUILD, SHORTER, now=WEEKDAY_OPEN)
    assert after_shorter is not None
    assert after_shorter.cash_balance == starting_cash
    assert TARGET not in after_shorter.short_positions
    assert after_fund is not None
    assert after_fund.cash_balance == starting_fund
    assert after_cooldown is None
    assert uow.rollbacks == 1


# ---------------------------------------------------------------------------
# #82 H2 — collateral invariant on cover
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "shares_total,entry_price,partial_covers",
    [
        (10, Decimal("100.00"), [3, 4, 3]),
        (7, Decimal("125.00"), [2, 2, 3]),
        (9, Decimal("33.33"), [1, 4, 4]),
        (12, Decimal("99.99"), [5, 5, 2]),
    ],
)
async def test_partial_cover_sequence_preserves_collateral_invariant(
    shares_total: int,
    entry_price: Decimal,
    partial_covers: list[int],
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H2: across any sequence of partial covers, the collateral invariant
    holds — until the position is fully closed, the running sum
    ``released_cash + released_fund + locked_cash_remaining +
    locked_fund_remaining`` equals ``shares_total * entry_price``.

    Pre-fix the partial-cover path quantises each released slice and
    each remaining slice independently, so the sum could drift by up to
    ``len(covers) * CENT``. After fix the **final** cover path releases
    exactly what is left, so the totals reconcile.
    """
    initial_locked_cash = (
        entry_price * Decimal(shares_total) * Decimal("0.4")
    ).quantize(Decimal("0.01"))
    initial_locked_fund = (
        entry_price * Decimal(shares_total) - initial_locked_cash
    ).quantize(Decimal("0.01"))
    notional = (entry_price * Decimal(shares_total)).quantize(Decimal("0.01"))
    assert initial_locked_cash + initial_locked_fund == notional

    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=shares_total,
        entry_price=entry_price,
        locked_cash=initial_locked_cash,
        locked_fund=initial_locked_fund,
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("100000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_fund_repo.upsert(GUILD, _fund(COVERER, cash=Decimal("100000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=entry_price))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    released_cash_total = Decimal("0.00")
    released_fund_total = Decimal("0.00")
    current_time = WEEKDAY_OPEN
    for shares_to_cover in partial_covers:
        # Step the clock past the trade cooldown so the next cover proceeds.
        current_time += timedelta(seconds=default_settings.trade_cooldown_seconds + 1)
        with freeze_time(current_time):
            result = await service.cover(COVERER, TARGET, shares_to_cover)
        released_cash_total += result.released_cash
        released_fund_total += result.released_fund

    after = await fake_user_repo.get(GUILD, COVERER)
    assert after is not None
    remaining = after.short_positions.get(TARGET)
    if remaining is None:
        remaining_locked_cash = Decimal("0.00")
        remaining_locked_fund = Decimal("0.00")
    else:
        remaining_locked_cash = remaining.locked_cash
        remaining_locked_fund = remaining.locked_fund

    total_returned = (
        released_cash_total
        + released_fund_total
        + remaining_locked_cash
        + remaining_locked_fund
    )
    assert total_returned == notional


async def test_full_cover_releases_exact_locked_values(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H2: a full cover releases the position's locked_cash and locked_fund
    bit-for-bit, not a re-derived proportional slice that could drift.

    Choose a (shares, entry_price) pair where ``shares * entry_price`` is
    not exactly divisible into two two-decimal slices, then bias the
    initial split so the recomputed proportional values would round
    differently from the originals.
    """
    shares = 3
    entry_price = Decimal("33.33")
    initial_short = ShortPosition(
        target_user_id=TARGET,
        shares=shares,
        entry_price=entry_price,
        locked_cash=Decimal("40.00"),
        locked_fund=Decimal("59.99"),
        created_at=WEEKDAY_OPEN - timedelta(minutes=5),
        frozen=False,
    )
    notional = initial_short.locked_cash + initial_short.locked_fund
    await fake_user_repo.upsert(
        GUILD,
        _account(
            COVERER,
            cash=Decimal("10000.00"),
            short_positions={TARGET: initial_short},
        ),
    )
    await fake_fund_repo.upsert(GUILD, _fund(COVERER, cash=Decimal("0.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=entry_price))

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        result = await service.cover(COVERER, TARGET, shares)

    assert result.position_after is None
    assert result.released_cash == initial_short.locked_cash
    assert result.released_fund == initial_short.locked_fund
    assert result.released_cash + result.released_fund == notional


# ---------------------------------------------------------------------------
# #82 M12 — cooldown ``expires_at`` anchored to the in-lock ``now``
# ---------------------------------------------------------------------------


async def test_cooldown_expires_at_is_anchored_to_in_lock_now(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """M12: cooldown expiry is anchored to the time sampled INSIDE the lock.

    Wrap ``LockManager.locked`` so the wall clock advances by 60s while
    the short is waiting for the lock. The cooldown row must record an
    ``expires_at`` based on the post-acquire ``now`` (i.e. ``now`` +
    ``trade_cooldown_seconds``), not the pre-lock sample.
    """
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("10000.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("10000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    sampled_times: list[datetime] = []

    real_locked = lock_manager.locked

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def slow_locked(*ids):  # type: ignore[no-untyped-def]
        # Push the freezegun clock forward by 60s while waiting for the lock.
        from freezegun import api as freeze_api

        if freeze_api.freeze_factories:  # pragma: no branch - assert framework state
            freezer = freeze_api.freeze_factories[-1]
            freezer.tick(timedelta(seconds=60))
        async with real_locked(*ids):
            sampled_times.append(datetime.now(tz=UTC))
            yield

    lock_manager.locked = slow_locked  # type: ignore[method-assign]

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN):
        await service.short(SHORTER, TARGET, 1)

    cooldown_row = await fake_cooldown_repo.get(GUILD, SHORTER, now=sampled_times[0])
    assert cooldown_row is not None
    expected = sampled_times[0] + timedelta(
        seconds=default_settings.trade_cooldown_seconds
    )
    assert cooldown_row.expires_at == expected


# ---------------------------------------------------------------------------
# #84 H — _get_fund_cash propagates persistence failure (no ghost fund)
# ---------------------------------------------------------------------------


class _FailingFundRepo:
    """Fund repo that raises on every ``get`` to simulate persistence failure.

    Mirrors :class:`FakeFundRepo` shape so it can be passed in place of
    the fixture: the trading service's ``_get_fund_cash`` must propagate
    the exception rather than silently return ``Decimal("0")`` and let
    the rest of ``short`` / ``cover`` carry on building a position
    against a phantom fund.
    """

    async def get(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("fund repo unavailable")

    async def upsert(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("fund repo unavailable")

    async def delete(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("fund repo unavailable")

    async def list_all(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("fund repo unavailable")

    async def ensure_events_wallet(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("fund repo unavailable")


async def test_short_propagates_fund_repo_failure_instead_of_ghost_fund(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """#84 H: a fund-repo read failure must propagate, not become a phantom $0 fund.

    Pre-fix ``_get_fund_cash`` caught the exception and returned
    ``Decimal("0")``, causing ``short`` to silently assume no fund
    collateral. After fix the exception bubbles up and the trade
    aborts cleanly without writing any state.
    """
    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("10000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    failing_fund_repo = _FailingFundRepo()
    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=failing_fund_repo,  # type: ignore[arg-type]
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    with freeze_time(WEEKDAY_OPEN), pytest.raises(RuntimeError):
        await service.short(SHORTER, TARGET, 1)
