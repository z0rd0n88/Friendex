"""Direct unit tests for :class:`FakeUnitOfWork` (review M4).

The application-service atomicity tests
(``tests/application/test_trading_service_atomicity.py``,
``tests/application/test_fund_service_atomicity.py``) exercise
:class:`FakeUnitOfWork` end-to-end through the trading and fund services.
This module is the direct unit-test pin: it stands the fake up in
isolation against a tiny stub repo and asserts the savepoint snapshot /
restore semantics, the commit/rollback counters, and the contract for
nested ``transaction()`` calls (currently: not supported — calling
``transaction()`` twice on the same instance with one active is
undefined behaviour the trading service does not exercise).

Pinning the fake directly here means a regression in the fake itself
(e.g. dropping the ``_history`` snapshot, breaking the deepcopy, or
silently swallowing an exception inside the rollback) surfaces here
before it slips through into the service tests as a spurious pass.
"""

from __future__ import annotations

import pytest

from tests.application.fakes.fake_repos import FakePriceRepo, FakeUserRepo
from tests.application.fakes.fake_unit_of_work import FakeUnitOfWork


class _StubRepoWithStore:
    """Minimal stub: a single ``_store`` dict so the snapshot path can run."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}


class _StubRepoWithStoreAndHistory:
    """Stub with both ``_store`` and ``_history`` to pin the dual-snapshot path."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._history: list[str] = []


class _StubRepoWithNeither:
    """Stub with neither ``_store`` nor ``_history`` — gracefully ignored."""

    def __init__(self) -> None:
        self.tag = "no-snapshot-attrs"


async def test_commit_on_clean_exit_increments_commits_counter() -> None:
    """A clean ``async with uow.transaction(): ...`` exit increments ``commits``."""
    repo = _StubRepoWithStore()
    uow = FakeUnitOfWork(repo)

    async with uow.transaction():
        repo._store["k"] = "v"

    assert uow.commits == 1
    assert uow.rollbacks == 0
    # The clean-exit path does NOT restore the store; the write is durable.
    assert repo._store == {"k": "v"}


async def test_rollback_on_exception_restores_store_and_increments_rollbacks() -> None:
    """An exception inside the block restores the pre-transaction store."""
    repo = _StubRepoWithStore()
    repo._store["seed"] = "initial"
    uow = FakeUnitOfWork(repo)

    with pytest.raises(RuntimeError):
        async with uow.transaction():
            repo._store["seed"] = "modified"
            repo._store["new"] = "added"
            raise RuntimeError("mid-sequence failure")

    assert uow.commits == 0
    assert uow.rollbacks == 1
    # The store is restored bit-for-bit to its pre-enter snapshot.
    assert repo._store == {"seed": "initial"}


async def test_rollback_restores_auxiliary_history_attribute() -> None:
    """The savepoint includes ``_history`` (FakePriceRepo's append log).

    Without the dual-attribute snapshot the trading service test that
    pins price-history-rollback would falsely pass — the history list
    would carry the dangling append across the rollback. This pins the
    auxiliary attribute is restored.
    """
    repo = _StubRepoWithStoreAndHistory()
    repo._store["k"] = "v"
    repo._history.append("baseline")
    uow = FakeUnitOfWork(repo)

    with pytest.raises(ValueError, match="boom"):
        async with uow.transaction():
            repo._store["k"] = "modified"
            repo._history.append("mid-tx")
            raise ValueError("boom")

    assert uow.rollbacks == 1
    assert repo._store == {"k": "v"}
    assert repo._history == ["baseline"]


async def test_rollback_isolates_multiple_repo_snapshots() -> None:
    """An exception restores EVERY participating repo, not just the first.

    The trading service test pins this end-to-end (user + fund + price +
    cooldown all roll back together). This is the direct pin so the
    multi-repo savepoint discipline is tested without going through the
    service layer.
    """
    repo_a = _StubRepoWithStore()
    repo_b = _StubRepoWithStore()
    repo_a._store["a-seed"] = "a"
    repo_b._store["b-seed"] = "b"
    uow = FakeUnitOfWork(repo_a, repo_b)

    with pytest.raises(RuntimeError):
        async with uow.transaction():
            repo_a._store["a-tx"] = "a2"
            repo_b._store["b-tx"] = "b2"
            raise RuntimeError("fail both")

    assert uow.rollbacks == 1
    assert repo_a._store == {"a-seed": "a"}
    assert repo_b._store == {"b-seed": "b"}


async def test_repo_without_snapshot_attrs_is_ignored_gracefully() -> None:
    """A repo with neither ``_store`` nor ``_history`` is silently skipped.

    The trading service threads in repos that may or may not have the
    expected snapshot shape (it's structural duck-typing). The fake must
    not crash on a repo without the attrs — it just skips snapshotting
    that repo.
    """
    repo = _StubRepoWithNeither()
    uow = FakeUnitOfWork(repo)

    async with uow.transaction():
        pass

    assert uow.commits == 1
    # Nothing was modified — the tag is unchanged.
    assert repo.tag == "no-snapshot-attrs"


async def test_real_fake_repos_round_trip_through_uow() -> None:
    """End-to-end smoke: stand the real FakeUserRepo + FakePriceRepo up in a UoW.

    Asserts the snapshot/restore mechanic works against the actual fake
    repos the service tests use — not just the stubs above.
    """
    user_repo = FakeUserRepo()
    price_repo = FakePriceRepo()

    # Seed a row in each so the snapshot has something to restore to.
    from datetime import UTC, datetime
    from decimal import Decimal

    from friendex.domain.models import (
        ActivityBucket,
        DailyProgress,
        PricePoint,
        Stock,
        UserAccount,
    )

    now = datetime.now(tz=UTC)
    seed_user = UserAccount(
        user_id="u1",
        cash_balance=Decimal("100.00"),
        net_worth=Decimal("100.00"),
        month_start_net_worth=Decimal("100.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(bucket_start=now),
        week=ActivityBucket(bucket_start=now),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
    )
    seed_stock = Stock(
        user_id="s1",
        current=Decimal("50.00"),
        history=[],
        high_24h=Decimal("50.00"),
        low_24h=Decimal("50.00"),
        all_time_high=Decimal("50.00"),
    )
    await user_repo.upsert("g1", seed_user)
    await price_repo.upsert("g1", seed_stock)
    await price_repo.append_history(
        "g1", "s1", PricePoint(price=Decimal("50.00"), timestamp=now)
    )

    uow = FakeUnitOfWork(user_repo, price_repo)

    with pytest.raises(RuntimeError):
        async with uow.transaction():
            # Modify both stores AND the price-history aux attribute.
            mutated = seed_user.__class__(
                **{**seed_user.__dict__, "cash_balance": Decimal("0.00")}
            )
            await user_repo.upsert("g1", mutated)
            await price_repo.append_history(
                "g1", "s1", PricePoint(price=Decimal("99.99"), timestamp=now)
            )
            raise RuntimeError("force rollback")

    # User cash restored.
    after_user = await user_repo.get("g1", "u1")
    assert after_user is not None
    assert after_user.cash_balance == Decimal("100.00")

    # Price history restored to one point (the seed), not two.
    history = await price_repo.get_history("g1", "s1")
    assert len(history) == 1
    assert history[0].price == Decimal("50.00")

    assert uow.commits == 0
    assert uow.rollbacks == 1
