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
    from friendex.application.interfaces import TradeCooldown
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
    """C1 outcome pin: two coroutines racing on ``short`` MUST NOT both succeed.

    Outcome-level pin (mirrors what users observe). The mechanism-level
    pin — that the *in-lock recheck* is the load-bearing thing — lives
    in :func:`test_in_lock_cooldown_recheck_fires_against_in_flight_write`.
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


async def test_in_lock_cooldown_recheck_fires_against_in_flight_write(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """C1 mechanism pin: the in-lock recheck is what stops the second short.

    The outcome test above asserts "only one of two concurrent shorts
    succeeds" — but that holds even if the in-lock recheck is a no-op,
    because :class:`LockManager` serialises the two calls and the second
    queues behind the first's cooldown write anyway. This test pins the
    load-bearing mechanism: the AUTHORITATIVE cooldown check fires INSIDE
    the locked + UoW critical section, after A's cooldown has been
    written and before B's cooldown write would be attempted.

    Deterministic interleaving: an ``asyncio.Event`` blocks coroutine A's
    cooldown write (via a wrapped repo) until coroutine B has passed
    the pre-lock probe. The lock + cooldown row then serialise the two
    coroutines, and B's in-lock recheck observes A's cooldown row as
    soon as A is released. The recheck call sites are instrumented so
    the test can assert B's authoritative recheck saw the cooldown
    (raised :class:`OnCooldown`) rather than reaching the cooldown
    write — the latter outcome is what the pre-fix race produced.
    """
    from contextlib import asynccontextmanager, suppress

    from friendex.domain.errors import OnCooldown

    await fake_user_repo.upsert(GUILD, _account(SHORTER, cash=Decimal("10000.00")))
    await fake_fund_repo.upsert(GUILD, _fund(SHORTER, cash=Decimal("10000.00")))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("100.00")))

    b_passed_pre_lock_probe = asyncio.Event()
    recheck_observations: list[tuple[str, bool]] = []

    inner_get = fake_cooldown_repo.get

    async def instrumented_get(
        guild_id: str, user_id: str, *, now: datetime
    ) -> TradeCooldown | None:
        result = await inner_get(guild_id, user_id, now=now)
        # The third call to ``get`` is the SECOND coroutine's authoritative
        # in-lock recheck — see ``short`` ordering: each call does (1)
        # pre-lock probe, then in-lock (2) recheck. Coroutine A's writes
        # come between A's recheck and B's recheck.
        recheck_observations.append((user_id, result is not None))
        return result

    fake_cooldown_repo.get = instrumented_get  # type: ignore[method-assign]

    real_locked = lock_manager.locked

    @asynccontextmanager
    async def coordinating_locked(*ids):  # type: ignore[no-untyped-def]
        # Each ``short`` takes the lock once with two ids; the first
        # coroutine to acquire it will be A. We gate A's body so B can
        # pass its pre-lock cooldown probe (the only step that runs
        # before lock acquisition) and queue on the lock before A
        # writes its cooldown row.
        async with real_locked(*ids):
            # B's pre-lock probe runs without the lock so we just wait
            # for B to signal it has passed before A's body proceeds.
            # The waiter is A only on its first invocation; the second
            # waiter (B) already has the event set by then because B
            # set it before queueing on the lock.
            with suppress(TimeoutError):
                await asyncio.wait_for(b_passed_pre_lock_probe.wait(), timeout=1.0)
            yield

    lock_manager.locked = coordinating_locked  # type: ignore[method-assign]

    service = _make_service(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    async def coroutine_a() -> object:
        return await service.short(SHORTER, TARGET, 1)

    async def coroutine_b() -> object:
        # Yield once so A enters the lock first; then we signal we have
        # passed the pre-lock probe so A's cooldown write can land.
        await asyncio.sleep(0)
        b_passed_pre_lock_probe.set()
        try:
            return await service.short(SHORTER, TARGET, 1)
        except OnCooldown as exc:
            return exc

    with freeze_time(WEEKDAY_OPEN):
        results = await asyncio.gather(
            coroutine_a(), coroutine_b(), return_exceptions=True
        )

    successes = [r for r in results if not isinstance(r, Exception | OnCooldown)]
    cooldowns = [r for r in results if isinstance(r, OnCooldown)]
    assert len(successes) == 1
    assert len(cooldowns) == 1
    # A and B each made one pre-lock probe (returned ``None`` — no row).
    # B's IN-LOCK recheck saw A's row — that's the mechanism we are pinning.
    truthy_observations = [r for _, r in recheck_observations if r]
    assert len(truthy_observations) == 1, (
        "exactly one cooldown read should observe an active row — "
        "B's in-lock recheck of A's just-written cooldown"
    )


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
        # Baseline shapes — clean splits, single full-cover at the end.
        (10, Decimal("100.00"), [3, 4, 3]),
        (7, Decimal("125.00"), [2, 2, 3]),
        (9, Decimal("33.33"), [1, 4, 4]),
        (12, Decimal("99.99"), [5, 5, 2]),
        # Adversarial: irrational entry price that does NOT divide evenly
        # into cent precision. Locked split is also non-clean. Reproduces
        # the review's worked example (locked=50.07/49.93, shares=7).
        (7, Decimal("14.29"), [1, 2, 4]),  # ~ 100/7
        # Adversarial: many small partial covers stress the per-cover
        # quantisation. Each cover is 1 share so the proportional split
        # rounds each time; the global invariant must still hold.
        (10, Decimal("33.33"), [1, 1, 1, 1, 1, 1, 1, 1, 1, 1]),
        # Adversarial: a large ratio between shares being covered and
        # the position's residual size on each step (10-share position,
        # covers of 1+1+1+1+1+5 — the last one is half).
        (10, Decimal("77.77"), [1, 1, 1, 1, 1, 5]),
        # Adversarial: an irrational price with prime-share total, where
        # only the final full-close path can keep the totals exact.
        (11, Decimal("13.13"), [3, 3, 5]),
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

    The adversarial parametrisation pins this under irrational entry
    prices and many-small-covers sequences — the per-position
    ``locked_cash + locked_fund == shares * entry_price`` invariant may
    drift by ≤1¢ mid-sequence (documented in ``fund_math.py``), but the
    GLOBAL sum continues to hold bit-for-bit on every shape.
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


async def test_partial_cover_per_position_drift_is_bounded_to_one_cent(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """H2 documented drift: per-position invariant drifts ≤1¢ on partial covers.

    Reproduces the worked example from the review: a 7-share short at
    entry_price ~ 100/7 with a non-clean locked split (50.07 / 49.93).
    After a 1-share partial cover the *per-position* invariant
    ``locked_cash + locked_fund == shares * entry_price`` drifts by at
    most 1¢; the *global* released-totals + remaining-locked sum holds
    exactly. Pins both halves of the contract so future readers know
    which invariant is exact and which is approximate.
    """
    shares_total = 7
    # Entry price chosen so shares_total * entry_price = 100.03 (≠ 100/7 *
    # 7 exactly to two decimal places — the per-cover proportional
    # quantisation can drift here).
    entry_price = Decimal("14.29")
    initial_locked_cash = Decimal("50.07")
    initial_locked_fund = Decimal("49.96")  # 50.07 + 49.96 = 100.03
    notional = initial_locked_cash + initial_locked_fund
    assert notional == (entry_price * Decimal(shares_total)).quantize(Decimal("0.01"))

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

    with freeze_time(WEEKDAY_OPEN):
        result = await service.cover(COVERER, TARGET, 1)

    # Per-position invariant: drift bounded to 1¢ (documented in
    # ``fund_math.py``). Compute what the position's locked sum is now
    # vs. what it ``should`` be if the per-position invariant held bit-exact.
    after = await fake_user_repo.get(GUILD, COVERER)
    assert after is not None
    remaining = after.short_positions[TARGET]
    actual_position_sum = remaining.locked_cash + remaining.locked_fund
    expected_position_sum = (
        Decimal(remaining.shares) * remaining.entry_price
    ).quantize(Decimal("0.01"))
    per_position_drift = abs(actual_position_sum - expected_position_sum)
    assert per_position_drift <= Decimal("0.01"), (
        f"per-position drift {per_position_drift} exceeds the documented 1¢ bound"
    )

    # Global invariant: released_cash + released_fund + remaining_locked == notional.
    # Bit-exact even when per-position drifts.
    global_sum = (
        result.released_cash
        + result.released_fund
        + remaining.locked_cash
        + remaining.locked_fund
    )
    assert global_sum == notional, (
        "global released + remaining sum must equal initial notional bit-for-bit"
    )


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
