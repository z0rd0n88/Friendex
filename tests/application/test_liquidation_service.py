"""Behavioural tests for :class:`LiquidationService` (Phase 8f).

The liquidation service sweeps every account in the guild for short positions
whose target price has rallied to at least
``entry_price * settings.liquidation_threshold`` (default 1.5x). Each such
short is auto-covered via :meth:`TradingService.cover_forced` (#82 M1 —
the previous direct reach into the private ``_cover_internal`` was
promoted to a public wrapper) so the freeze guard is bypassed (a
liquidation cannot be blocked by the short's post-open freeze window).

Acceptance criteria pinned here:

* **F1** — a short at 149% of entry is NOT liquidated (just under the 150%
  threshold).
* **F2** — a short at exactly 150% of entry IS liquidated.
* **F3** — a FROZEN short IS still liquidated (the public
  :meth:`TradingService.cover` would raise :class:`PositionFrozen`; the
  liquidation path bypasses it via :meth:`cover_forced`).
* **F4** — :class:`LiquidationEvent` payload correctness (holder, target,
  shares, entry, exit, collateral returned, P&L) for a concrete scenario.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from friendex.application.liquidation_events import LiquidationEvent
from friendex.application.liquidation_service import LiquidationService
from friendex.application.trading_service import TradingService
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
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
HOLDER = "holder-1"
TARGET = "target-1"

NOW = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
# Far enough in the past that a default 30-minute freeze window has elapsed.
SHORT_CREATED_AT = NOW - timedelta(hours=2)


def _account(
    user_id: str,
    *,
    cash: Decimal = Decimal("10000.00"),
    short_positions: dict[str, ShortPosition] | None = None,
    opt_in: bool = True,
) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for the liquidation tests."""
    return UserAccount(
        user_id=user_id,
        cash_balance=cash,
        net_worth=cash,
        month_start_net_worth=cash,
        long_positions={},
        short_positions=short_positions or {},
        today=ActivityBucket(bucket_start=NOW),
        week=ActivityBucket(bucket_start=NOW),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=NOW,
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


def _short(
    target_id: str,
    *,
    shares: int,
    entry_price: Decimal,
    locked_cash: Decimal,
    locked_fund: Decimal = Decimal("0.00"),
    frozen: bool = False,
) -> ShortPosition:
    """Build a minimal valid :class:`ShortPosition` for the liquidation tests."""
    return ShortPosition(
        target_user_id=target_id,
        shares=shares,
        entry_price=entry_price,
        locked_cash=locked_cash,
        locked_fund=locked_fund,
        created_at=SHORT_CREATED_AT,
        frozen=frozen,
    )


def _make_services(
    *,
    user_repo: FakeUserRepo,
    price_repo: FakePriceRepo,
    fund_repo: FakeFundRepo,
    cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    settings: Settings,
    unit_of_work: object | None = None,
) -> tuple[TradingService, LiquidationService]:
    """Build :class:`TradingService` + :class:`LiquidationService` wired together.

    ``unit_of_work`` is threaded into BOTH services when provided so the
    savepoint spans every write in the cover path: the
    :class:`LiquidationService` opens the envelope around its
    ``cover_forced`` call, and the inner ``_cover_internal`` body lands its
    user / fund / price / history writes through the same fakes the
    :class:`FakeUnitOfWork` snapshots. Defaults to ``None`` so the
    existing F1-F4 tests (which exercise the happy-path semantics, not
    rollback) continue to use the services' built-in :class:`NullUnitOfWork`
    fallback.
    """
    trading = TradingService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=lock_manager,
        settings=settings,
        unit_of_work=unit_of_work,  # type: ignore[arg-type]
    )
    liquidation = LiquidationService(
        guild_id=GUILD,
        user_repo=user_repo,
        price_repo=price_repo,
        fund_repo=fund_repo,
        cooldown_repo=cooldown_repo,
        lock_manager=lock_manager,
        settings=settings,
        trading_service=trading,
        unit_of_work=unit_of_work,  # type: ignore[arg-type]
    )
    return trading, liquidation


# ---------------------------------------------------------------------------
# F1 — short at 149% of entry is NOT liquidated
# ---------------------------------------------------------------------------


async def test_short_below_threshold_not_liquidated(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F1: entry 100, price 149 (149%) → no liquidation (threshold 1.5x)."""
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
    )
    await fake_user_repo.upsert(
        GUILD, _account(HOLDER, short_positions={TARGET: short})
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("149.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert events == []
    holder = await fake_user_repo.get(GUILD, HOLDER)
    assert holder is not None
    # Short is untouched.
    assert TARGET in holder.short_positions
    assert holder.short_positions[TARGET].shares == 10


# ---------------------------------------------------------------------------
# F2 — short at exactly 150% of entry IS liquidated
# ---------------------------------------------------------------------------


async def test_short_at_threshold_is_liquidated(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F2: entry 100, price 150 (exactly 150%) → liquidated."""
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
    )
    await fake_user_repo.upsert(
        GUILD, _account(HOLDER, short_positions={TARGET: short})
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("150.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert len(events) == 1
    holder = await fake_user_repo.get(GUILD, HOLDER)
    assert holder is not None
    # Short fully covered → position deleted (Phase 8c convention).
    assert TARGET not in holder.short_positions


# ---------------------------------------------------------------------------
# F3 — a FROZEN short is still liquidated (force=True bypasses PositionFrozen)
# ---------------------------------------------------------------------------


async def test_frozen_short_still_liquidated(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F3: a frozen short past 1.5x IS liquidated (the public cover would refuse)."""
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
        frozen=True,
    )
    await fake_user_repo.upsert(
        GUILD, _account(HOLDER, short_positions={TARGET: short})
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("160.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert len(events) == 1
    holder = await fake_user_repo.get(GUILD, HOLDER)
    assert holder is not None
    assert TARGET not in holder.short_positions


# ---------------------------------------------------------------------------
# F4 — LiquidationEvent payload correctness
# ---------------------------------------------------------------------------


async def test_liquidation_event_payload_correct(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """F4: event carries holder, target, shares, entry, exit, collateral, P&L.

    Concrete scenario: holder shorted 10 shares at entry $100 with $1000 cash
    collateral (no fund collateral). The target price rallies to $150 (1.5x).
    Cover cost = 10 * 150 = $1500. Released collateral = $1000 (full close).
    P&L = (entry - exit) * shares = (100 - 150) * 10 = -$500 (loss).
    """
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
        locked_fund=Decimal("0.00"),
    )
    # Holder needs cash to pay the cover cost ($1500).
    await fake_user_repo.upsert(
        GUILD,
        _account(
            HOLDER,
            cash=Decimal("2000.00"),
            short_positions={TARGET: short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("150.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, LiquidationEvent)
    assert event.guild_id == GUILD
    assert event.holder_id == HOLDER
    assert event.target_id == TARGET
    assert event.shares == 10
    assert event.entry_price == Decimal("100.00")
    assert event.exit_price == Decimal("150.00")
    assert event.collateral_returned == Decimal("1000.00")
    # P&L = (entry - exit) * shares = (100 - 150) * 10 = -500 (a loss).
    assert event.pnl == Decimal("-500.00")
    # Timestamp comes from the now passed to check_and_liquidate_shorts.
    assert event.timestamp == NOW


# ---------------------------------------------------------------------------
# Auxiliary — ensure a totally unrelated holder is not iterated needlessly
# (defensive coverage; the loop must skip accounts with no shorts).
# ---------------------------------------------------------------------------


async def test_account_with_no_shorts_is_skipped(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """An account without any short positions produces no events."""
    await fake_user_repo.upsert(GUILD, _account("bystander"))
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("200.00")))

    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=fake_price_repo,
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
    )

    events = await liquidation.check_and_liquidate_shorts(NOW)

    assert events == []


# ---------------------------------------------------------------------------
# Issue #95 — mid-helper persistence failure during liquidation rolls back
# ---------------------------------------------------------------------------


class _ExplodingPriceRepo:
    """Decorator price repo that delegates to the inner fake then explodes.

    Mirrors the ``_ExplodingPriceRepo`` pattern at
    ``tests/application/test_trading_service_atomicity.py:300`` — used to
    simulate a mid-helper persistence failure inside the
    ``_cover_internal`` body invoked from
    :meth:`LiquidationService._maybe_liquidate`. With the issue #95 fix
    in place the :class:`LiquidationService` wraps the ``cover_forced``
    call in ``self._uow.transaction()`` so the :class:`FakeUnitOfWork`
    rolls every fake repo back to its pre-transaction snapshot:
    the holder's cash is restored, the short position stays in full,
    no target stub is upserted, and no :class:`LiquidationEvent` is
    returned.
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


async def test_liquidation_rolls_back_on_mid_helper_failure(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    """Issue #95: an injected mid-helper persistence failure during a
    liquidation cover MUST leave NO partial writes.

    Holder owns a 10-share short on the target with $1000 cash collateral
    locked. The target's price has rallied to $150 (1.5x entry) so the
    short is exactly at the liquidation threshold. A :class:`_ExplodingPriceRepo`
    is wrapped around the underlying fake price repo with ``fail_after=1`` —
    the first ``upsert`` (the stock-row priming inside ``_cover_internal``)
    succeeds, the second persistence call (the price-impact ``upsert`` /
    ``append_history``) raises ``RuntimeError``. A :class:`FakeUnitOfWork`
    is threaded into BOTH services so the savepoint snapshots every fake
    repo before the cover body opens its critical section.

    After the failure propagates out of
    :meth:`LiquidationService.check_and_liquidate_shorts`, the rollback
    contract demands:

    * Holder's cash balance is unchanged (cover cost was NOT debited).
    * Holder's short position is still present in full (10 shares).
    * No :class:`LiquidationEvent` is returned (the function raised).
    * The :class:`FakeUnitOfWork` recorded exactly one rollback.
    """
    starting_cash = Decimal("5000.00")
    short = _short(
        TARGET,
        shares=10,
        entry_price=Decimal("100.00"),
        locked_cash=Decimal("1000.00"),
        locked_fund=Decimal("0.00"),
    )
    await fake_user_repo.upsert(
        GUILD,
        _account(
            HOLDER,
            cash=starting_cash,
            short_positions={TARGET: short},
        ),
    )
    await fake_user_repo.upsert(GUILD, _account(TARGET))
    await fake_price_repo.upsert(GUILD, _stock(TARGET, current=Decimal("150.00")))

    exploding_price_repo = _ExplodingPriceRepo(fake_price_repo, fail_after=1)
    uow = FakeUnitOfWork(
        fake_user_repo, fake_fund_repo, fake_price_repo, fake_cooldown_repo
    )
    _, liquidation = _make_services(
        user_repo=fake_user_repo,
        price_repo=exploding_price_repo,  # type: ignore[arg-type]
        fund_repo=fake_fund_repo,
        cooldown_repo=fake_cooldown_repo,
        lock_manager=lock_manager,
        settings=default_settings,
        unit_of_work=uow,
    )

    with pytest.raises(RuntimeError):
        await liquidation.check_and_liquidate_shorts(NOW)

    after_holder = await fake_user_repo.get(GUILD, HOLDER)
    assert after_holder is not None
    # Holder's cash unchanged — the cover cost ($1500) was rolled back.
    assert after_holder.cash_balance == starting_cash
    # Short position still present in full (10 shares, unchanged).
    assert TARGET in after_holder.short_positions
    assert after_holder.short_positions[TARGET].shares == 10
    # The :class:`FakeUnitOfWork` captured exactly one rollback — proves
    # the cover_forced call site opened the transaction envelope.
    assert uow.rollbacks == 1
