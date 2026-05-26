"""Behavioural tests for the in-memory fake repositories.

Each acceptance criterion for the Phase 8 test-double infrastructure is pinned
here. The fakes exist so service tests can run without a database, so these
tests assert the *observable semantics* the services depend on — round-trip
equality, per-guild isolation, activity-window filtering, append-only history
with pruning, idempotent ``ensure_events_wallet``, and cooldown TTL purging —
rather than any storage internals.

The fakes deliberately mirror the SQLAlchemy adapters'
behaviour (``src/friendex/adapters/persistence``); where the real repo includes
the boundary (e.g. ``list_active_in_last`` uses ``>= now - window``,
``purge_expired`` uses ``expires_at <= now``) these tests pin the same
boundaries so a service that passes against the fake also passes against SQLite.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import get_type_hints

from friendex.adapters.config import Settings
from friendex.application.interfaces import (
    IFundRepo,
    IPenaltyRepo,
    IPriceRepo,
    ISystemStateRepo,
    ITradeCooldownRepo,
    IUserRepo,
    SystemState,
    TradeCooldown,
)
from friendex.application.lock_manager import LockManager
from friendex.domain.models import (
    ActivityBucket,
    DailyProgress,
    FundPenalty,
    HedgeFund,
    PricePoint,
    Stock,
    UserAccount,
)
from tests.application.fakes.fake_repos import (
    FakeFundRepo,
    FakePenaltyRepo,
    FakePriceRepo,
    FakeSystemStateRepo,
    FakeTradeCooldownRepo,
    FakeUserRepo,
)

GUILD_A = "100000000000000001"
GUILD_B = "200000000000000002"


def _account(user_id: str, *, last_activity: datetime | None = None) -> UserAccount:
    """Build a minimal valid :class:`UserAccount` for ``user_id``."""
    when = last_activity if last_activity is not None else datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=when),
        week=ActivityBucket(bucket_start=when),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=when,
    )


def _stock(user_id: str, *, current: Decimal = Decimal("100.00")) -> Stock:
    """Build a minimal valid :class:`Stock` for ``user_id`` with empty history."""
    return Stock(
        user_id=user_id,
        current=current,
        history=[],
        high_24h=current,
        low_24h=current,
        all_time_high=current,
    )


def _fund(fund_id: str, *, manager_id: str = "9001") -> HedgeFund:
    """Build a minimal valid :class:`HedgeFund`."""
    return HedgeFund(
        fund_id=fund_id,
        name=f"Fund {fund_id}",
        manager_id=manager_id,
        cash_balance=Decimal("0.00"),
        investors={},
    )


# --- AC1: each fake implements the FULL surface of its Protocol -------------
#
# Protocol classes carry no runtime behaviour, so the load-bearing conformance
# check is structural assignability under mypy. These runtime assertions anchor
# the method *surface* (names + async-ness + matching parameter lists) so a
# signature drift surfaces here, not only deep inside a service test. The
# typed assignments below are what mypy verifies.

_FAKE_PROTOCOL_PAIRS = (
    (FakeUserRepo, IUserRepo),
    (FakePriceRepo, IPriceRepo),
    (FakeFundRepo, IFundRepo),
    (FakePenaltyRepo, IPenaltyRepo),
    (FakeTradeCooldownRepo, ITradeCooldownRepo),
    (FakeSystemStateRepo, ISystemStateRepo),
)


def _protocol_methods(protocol: type) -> list[str]:
    """Return the public (non-dunder) method names a Protocol declares."""
    return [
        name
        for name in dir(protocol)
        if not name.startswith("_") and callable(getattr(protocol, name))
    ]


def test_fakes_are_assignable_to_their_protocols() -> None:
    user_repo: IUserRepo = FakeUserRepo()
    price_repo: IPriceRepo = FakePriceRepo()
    fund_repo: IFundRepo = FakeFundRepo()
    penalty_repo: IPenaltyRepo = FakePenaltyRepo()
    cooldown_repo: ITradeCooldownRepo = FakeTradeCooldownRepo()
    system_state_repo: ISystemStateRepo = FakeSystemStateRepo()

    # Touch each binding so the typed assignment is not flagged as unused.
    assert all(
        repo is not None
        for repo in (
            user_repo,
            price_repo,
            fund_repo,
            penalty_repo,
            cooldown_repo,
            system_state_repo,
        )
    )


def test_each_fake_declares_every_protocol_method_as_async() -> None:
    for fake_cls, protocol in _FAKE_PROTOCOL_PAIRS:
        for method_name in _protocol_methods(protocol):
            method = getattr(fake_cls, method_name, None)
            assert method is not None, f"{fake_cls.__name__} missing {method_name}"
            assert inspect.iscoroutinefunction(method), (
                f"{fake_cls.__name__}.{method_name} must be async"
            )


def test_fake_method_signatures_match_protocol_signatures() -> None:
    for fake_cls, protocol in _FAKE_PROTOCOL_PAIRS:
        for method_name in _protocol_methods(protocol):
            proto_params = list(
                inspect.signature(getattr(protocol, method_name)).parameters
            )
            fake_params = list(
                inspect.signature(getattr(fake_cls, method_name)).parameters
            )
            # Every parameter the Protocol declares must be accepted by the fake
            # (the fake may add keyword-only extras like cooldown ``now``).
            missing = set(proto_params) - set(fake_params)
            assert not missing, (
                f"{fake_cls.__name__}.{method_name} drops params {missing}"
            )


def test_fake_return_types_match_protocol_return_types() -> None:
    # Both ``interfaces.py`` and ``fake_repos.py`` use ``from __future__ import
    # annotations`` with ``TYPE_CHECKING``-guarded model imports, so their return
    # hints are forward-ref strings. Resolving them needs the domain model names
    # and the DTOs in scope — supply them as the shared resolution namespace.
    import friendex.domain.models as models
    from friendex.application import interfaces

    resolution_ns = {
        **vars(models),
        "SystemState": interfaces.SystemState,
        "TradeCooldown": interfaces.TradeCooldown,
        "datetime": datetime,
    }

    for fake_cls, protocol in _FAKE_PROTOCOL_PAIRS:
        for method_name in _protocol_methods(protocol):
            proto_ret = get_type_hints(
                getattr(protocol, method_name), localns=resolution_ns
            ).get("return")
            fake_ret = get_type_hints(
                getattr(fake_cls, method_name), localns=resolution_ns
            ).get("return")
            assert proto_ret == fake_ret, (
                f"{fake_cls.__name__}.{method_name} returns {fake_ret}, "
                f"Protocol says {proto_ret}"
            )


# --- AC2: round-trip — upsert then get; missing key -> None; delete removes --


async def test_user_repo_round_trip_upsert_then_get() -> None:
    repo = FakeUserRepo()
    account = _account("u1")

    await repo.upsert(GUILD_A, account)

    assert await repo.get(GUILD_A, "u1") == account


async def test_user_repo_get_missing_returns_none() -> None:
    repo = FakeUserRepo()

    assert await repo.get(GUILD_A, "missing") is None


async def test_user_repo_delete_removes_account() -> None:
    repo = FakeUserRepo()
    await repo.upsert(GUILD_A, _account("u1"))

    await repo.delete(GUILD_A, "u1")

    assert await repo.get(GUILD_A, "u1") is None


async def test_price_repo_round_trip_and_missing_and_delete() -> None:
    repo = FakePriceRepo()
    stock = _stock("u1")

    await repo.upsert(GUILD_A, stock)
    assert await repo.get(GUILD_A, "u1") == stock
    assert await repo.get(GUILD_A, "missing") is None

    await repo.delete(GUILD_A, "u1")
    assert await repo.get(GUILD_A, "u1") is None


async def test_fund_repo_round_trip_and_missing_and_delete() -> None:
    repo = FakeFundRepo()
    fund = _fund("f1")

    await repo.upsert(GUILD_A, fund)
    assert await repo.get(GUILD_A, "f1") == fund
    assert await repo.get(GUILD_A, "missing") is None

    await repo.delete(GUILD_A, "f1")
    assert await repo.get(GUILD_A, "f1") is None


async def test_penalty_repo_round_trip_and_missing_and_delete() -> None:
    repo = FakePenaltyRepo()
    penalty = FundPenalty(
        user_id="u1",
        penalty_apr=Decimal("0.0500"),
        penalty_until=datetime.now(tz=UTC) + timedelta(days=14),
    )

    await repo.upsert(GUILD_A, penalty)
    assert await repo.get(GUILD_A, "u1") == penalty
    assert await repo.get(GUILD_A, "missing") is None

    await repo.delete(GUILD_A, "u1")
    assert await repo.get(GUILD_A, "u1") is None


async def test_cooldown_repo_round_trip_and_missing_and_delete() -> None:
    repo = FakeTradeCooldownRepo()
    now = datetime.now(tz=UTC)
    expires = now + timedelta(minutes=15)
    cooldown = TradeCooldown(guild_id=GUILD_A, user_id="u1", expires_at=expires)

    await repo.upsert(cooldown)
    assert await repo.get(GUILD_A, "u1", now=now) == cooldown
    assert await repo.get(GUILD_A, "missing", now=now) is None

    await repo.delete(GUILD_A, "u1")
    assert await repo.get(GUILD_A, "u1", now=now) is None


async def test_system_state_repo_round_trip_and_missing_and_delete() -> None:
    repo = FakeSystemStateRepo()
    state = SystemState(
        guild_id=GUILD_A,
        last_daily_reset=datetime.now(tz=UTC),
        last_weekly_reset=None,
    )

    await repo.upsert(state)
    assert await repo.get(GUILD_A) == state
    assert await repo.get("missing-guild") is None

    await repo.delete(GUILD_A)
    assert await repo.get(GUILD_A) is None


# --- AC3: per-guild isolation — list_all returns only that guild's rows ------


async def test_user_repo_list_all_is_guild_scoped() -> None:
    repo = FakeUserRepo()
    await repo.upsert(GUILD_A, _account("a1"))
    await repo.upsert(GUILD_A, _account("a2"))
    await repo.upsert(GUILD_B, _account("b1"))

    a_ids = {acct.user_id for acct in await repo.list_all(GUILD_A)}
    b_ids = {acct.user_id for acct in await repo.list_all(GUILD_B)}

    assert a_ids == {"a1", "a2"}
    assert b_ids == {"b1"}


async def test_price_repo_list_all_is_guild_scoped() -> None:
    repo = FakePriceRepo()
    await repo.upsert(GUILD_A, _stock("a1"))
    await repo.upsert(GUILD_B, _stock("b1"))

    assert {s.user_id for s in await repo.list_all(GUILD_A)} == {"a1"}
    assert {s.user_id for s in await repo.list_all(GUILD_B)} == {"b1"}


async def test_fund_repo_list_all_is_guild_scoped() -> None:
    repo = FakeFundRepo()
    await repo.upsert(GUILD_A, _fund("a1"))
    await repo.upsert(GUILD_B, _fund("b1"))

    assert {f.fund_id for f in await repo.list_all(GUILD_A)} == {"a1"}
    assert {f.fund_id for f in await repo.list_all(GUILD_B)} == {"b1"}


async def test_same_user_id_in_two_guilds_does_not_collide() -> None:
    """A shared user id across guilds keys two independent rows (ADR-0001)."""
    repo = FakeUserRepo()
    shared = "777"
    await repo.upsert(GUILD_A, _account(shared))
    await repo.upsert(GUILD_B, _account(shared))

    await repo.delete(GUILD_A, shared)

    assert await repo.get(GUILD_A, shared) is None
    assert await repo.get(GUILD_B, shared) is not None


# --- AC4: list_active_in_last returns only users active within the window ----


async def test_list_active_in_last_excludes_users_outside_window() -> None:
    repo = FakeUserRepo()
    now = datetime.now(tz=UTC)
    recent = _account("recent", last_activity=now - timedelta(seconds=60))
    stale = _account("stale", last_activity=now - timedelta(seconds=3600))
    await repo.upsert(GUILD_A, recent)
    await repo.upsert(GUILD_A, stale)

    active = await repo.list_active_in_last(GUILD_A, seconds=300)

    assert {acct.user_id for acct in active} == {"recent"}


async def test_list_active_in_last_is_guild_scoped() -> None:
    repo = FakeUserRepo()
    now = datetime.now(tz=UTC)
    await repo.upsert(GUILD_A, _account("a", last_activity=now))
    await repo.upsert(GUILD_B, _account("b", last_activity=now))

    active = await repo.list_active_in_last(GUILD_A, seconds=300)

    assert {acct.user_id for acct in active} == {"a"}


# --- AC5: price history append/get order + prune_history_older_than ----------


async def test_get_history_returns_points_chronologically() -> None:
    repo = FakePriceRepo()
    base = datetime.now(tz=UTC)
    # Append out of chronological order; get_history must return oldest-first.
    later = PricePoint(price=Decimal("110.00"), timestamp=base + timedelta(minutes=10))
    earlier = PricePoint(price=Decimal("100.00"), timestamp=base)
    await repo.append_history(GUILD_A, "u1", later)
    await repo.append_history(GUILD_A, "u1", earlier)

    history = await repo.get_history(GUILD_A, "u1")

    assert [p.timestamp for p in history] == [earlier.timestamp, later.timestamp]


async def test_get_history_since_filters_older_points() -> None:
    repo = FakePriceRepo()
    base = datetime.now(tz=UTC)
    old = PricePoint(price=Decimal("90.00"), timestamp=base - timedelta(hours=2))
    fresh = PricePoint(price=Decimal("95.00"), timestamp=base)
    await repo.append_history(GUILD_A, "u1", old)
    await repo.append_history(GUILD_A, "u1", fresh)

    history = await repo.get_history(GUILD_A, "u1", since=base - timedelta(hours=1))

    assert [p.timestamp for p in history] == [fresh.timestamp]


async def test_prune_history_older_than_drops_only_older_and_returns_count() -> None:
    repo = FakePriceRepo()
    base = datetime.now(tz=UTC)
    old = PricePoint(price=Decimal("90.00"), timestamp=base - timedelta(hours=2))
    at_cutoff = PricePoint(price=Decimal("92.00"), timestamp=base - timedelta(hours=1))
    fresh = PricePoint(price=Decimal("95.00"), timestamp=base)
    for point in (old, at_cutoff, fresh):
        await repo.append_history(GUILD_A, "u1", point)

    cutoff = base - timedelta(hours=1)
    removed = await repo.prune_history_older_than(cutoff)

    # Inclusive cutoff: the point exactly at the cutoff is kept (matches adapter).
    assert removed == 1
    remaining = await repo.get_history(GUILD_A, "u1")
    assert [p.timestamp for p in remaining] == [at_cutoff.timestamp, fresh.timestamp]


async def test_prune_history_spans_every_guild() -> None:
    repo = FakePriceRepo()
    old = PricePoint(
        price=Decimal("90.00"), timestamp=datetime.now(tz=UTC) - timedelta(hours=5)
    )
    await repo.append_history(GUILD_A, "u1", old)
    await repo.append_history(GUILD_B, "u2", old)

    removed = await repo.prune_history_older_than(datetime.now(tz=UTC))

    assert removed == 2
    assert await repo.get_history(GUILD_A, "u1") == []
    assert await repo.get_history(GUILD_B, "u2") == []


# --- AC6: ensure_events_wallet is idempotent --------------------------------


async def test_ensure_events_wallet_creates_then_is_idempotent() -> None:
    repo = FakeFundRepo()

    first = await repo.ensure_events_wallet(GUILD_A)
    second = await repo.ensure_events_wallet(GUILD_A)

    assert first == second
    assert first.fund_id == "events_wallet"
    # Exactly one wallet exists for the guild — no duplicate created.
    wallets = [f for f in await repo.list_all(GUILD_A) if f.fund_id == "events_wallet"]
    assert len(wallets) == 1


async def test_ensure_events_wallet_does_not_clobber_existing_balance() -> None:
    repo = FakeFundRepo()
    funded = HedgeFund(
        fund_id="events_wallet",
        name="Events Wallet",
        manager_id="0",
        cash_balance=Decimal("250.00"),
        investors={},
    )
    await repo.upsert(GUILD_A, funded)

    returned = await repo.ensure_events_wallet(GUILD_A)

    assert returned.cash_balance == Decimal("250.00")


# --- AC7: purge_expired removes only expired entries, returns count ----------


async def test_purge_expired_removes_only_expired_and_returns_count() -> None:
    repo = FakeTradeCooldownRepo()
    now = datetime.now(tz=UTC)
    expired = TradeCooldown(
        guild_id=GUILD_A, user_id="gone", expires_at=now - timedelta(minutes=1)
    )
    active = TradeCooldown(
        guild_id=GUILD_A, user_id="live", expires_at=now + timedelta(minutes=15)
    )
    await repo.upsert(expired)
    await repo.upsert(active)

    removed = await repo.purge_expired(now)

    assert removed == 1
    survivors = {c.user_id for c in await repo.list_all(GUILD_A)}
    assert survivors == {"live"}


async def test_purge_expired_boundary_is_inclusive() -> None:
    """A cooldown whose ``expires_at`` equals ``now`` is purged (``<=``)."""
    repo = FakeTradeCooldownRepo()
    now = datetime.now(tz=UTC)
    at_boundary = TradeCooldown(guild_id=GUILD_A, user_id="edge", expires_at=now)
    await repo.upsert(at_boundary)

    removed = await repo.purge_expired(now)

    assert removed == 1


async def test_cooldown_get_hides_expired_but_list_all_keeps_it() -> None:
    repo = FakeTradeCooldownRepo()
    now = datetime.now(tz=UTC)
    expired = TradeCooldown(
        guild_id=GUILD_A, user_id="e", expires_at=now - timedelta(seconds=1)
    )
    await repo.upsert(expired)

    # get filters expired rows; list_all returns them raw (matches adapter).
    assert await repo.get(GUILD_A, "e", now=now) is None
    assert {c.user_id for c in await repo.list_all(GUILD_A)} == {"e"}


async def test_purge_expired_spans_every_guild() -> None:
    repo = FakeTradeCooldownRepo()
    now = datetime.now(tz=UTC)
    past = now - timedelta(minutes=1)
    await repo.upsert(TradeCooldown(guild_id=GUILD_A, user_id="a", expires_at=past))
    await repo.upsert(TradeCooldown(guild_id=GUILD_B, user_id="b", expires_at=past))

    removed = await repo.purge_expired(now)

    assert removed == 2


# --- AC8: conftest fixtures yield a fresh instance per test ------------------


async def test_conftest_fixtures_are_fresh_instances(
    fake_user_repo: FakeUserRepo,
    fake_price_repo: FakePriceRepo,
    fake_fund_repo: FakeFundRepo,
    fake_penalty_repo: FakePenaltyRepo,
    fake_cooldown_repo: FakeTradeCooldownRepo,
    fake_system_state_repo: FakeSystemStateRepo,
) -> None:
    # Each fixture is empty at the start of a test (proving per-test freshness).
    assert await fake_user_repo.list_all(GUILD_A) == []
    assert await fake_price_repo.list_all(GUILD_A) == []
    assert await fake_fund_repo.list_all(GUILD_A) == []
    assert await fake_penalty_repo.list_all(GUILD_A) == []
    assert await fake_cooldown_repo.list_all(GUILD_A) == []
    assert await fake_system_state_repo.list_all() == []


def test_lock_manager_and_settings_fixtures_resolve(
    lock_manager: LockManager,
    default_settings: Settings,
) -> None:
    assert isinstance(lock_manager, LockManager)
    assert isinstance(default_settings, Settings)
    # Default game tunables came through unchanged.
    assert default_settings.trade_cooldown_seconds == 900
