"""Conformance tests for the repository ``Protocol`` interfaces.

These tests are deliberately thin: ``Protocol`` classes carry no runtime
behaviour, so the *real* contract check is ``mypy``. What we assert here is

1. each Protocol is importable (the RED state before ``interfaces.py`` exists
   is an ``ImportError``);
2. each Protocol declares the members the SqlXxxRepository implementations
   (sub-units 6c-6f) must satisfy; and
3. a minimal in-memory fake can structurally satisfy one Protocol, anchoring
   the method signatures so a regression in the interface shows up as a type
   error here rather than only in the adapter layer.

The architecture invariant (``interfaces.py`` imports only from
``friendex.domain`` + stdlib, never ``friendex.adapters``) is asserted by
inspecting the compiled module's source imports.
"""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

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
from friendex.domain.models import (
    FundPenalty,
    HedgeFund,
    PricePoint,
    Stock,
    UserAccount,
)

# --- AC1: the six Protocols exist and are importable -----------------------

_PROTOCOLS = (
    IUserRepo,
    IPriceRepo,
    IFundRepo,
    IPenaltyRepo,
    ITradeCooldownRepo,
    ISystemStateRepo,
)

_COMMON_CRUD = ("get", "upsert", "delete", "list_all")


@pytest.mark.parametrize("protocol", _PROTOCOLS, ids=lambda p: p.__name__)
def test_protocol_declares_common_crud(protocol: type) -> None:
    """Every repo Protocol exposes the shared get/upsert/delete/list_all surface."""
    for method in _COMMON_CRUD:
        assert hasattr(protocol, method), f"{protocol.__name__} missing {method}"


@pytest.mark.parametrize(
    ("protocol", "method"),
    [
        (IPriceRepo, "append_history"),
        (IPriceRepo, "prune_history_older_than"),
        (IPriceRepo, "get_history"),
        (IUserRepo, "list_active_in_last"),
        (IFundRepo, "ensure_events_wallet"),
    ],
)
def test_protocol_declares_model_specific_method(protocol: type, method: str) -> None:
    """The plan's named model-specific methods are present on their Protocol."""
    assert hasattr(protocol, method), f"{protocol.__name__} missing {method}"


# --- AC3: architecture invariant — no import from friendex.adapters --------


def test_interfaces_does_not_import_adapters() -> None:
    """``interfaces.py`` must not reach outward into the adapters layer.

    Checks the parsed import graph rather than raw text, so prose mentions of
    ``friendex.adapters`` in the module docstring do not produce a false
    positive.
    """
    import friendex.application.interfaces as mod

    source = mod.__file__
    assert source is not None
    tree = ast.parse(Path(source).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    offenders = {name for name in imported if name.startswith("friendex.adapters")}
    assert not offenders, f"interfaces.py must not import adapters: {offenders}"


# --- AC2: a typed fake structurally satisfies IUserRepo --------------------


class _FakeUserRepo:
    """In-memory ``IUserRepo`` proving the signatures are implementable."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], UserAccount] = {}

    async def get(self, guild_id: str, user_id: str) -> UserAccount | None:
        return self._rows.get((guild_id, user_id))

    async def upsert(self, guild_id: str, account: UserAccount) -> None:
        self._rows[(guild_id, account.user_id)] = account

    async def delete(self, guild_id: str, user_id: str) -> None:
        self._rows.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[UserAccount]:
        return [v for (g, _), v in self._rows.items() if g == guild_id]

    async def list_active_in_last(
        self, guild_id: str, seconds: float
    ) -> list[UserAccount]:
        cutoff = datetime.now(tz=UTC).timestamp() - seconds
        return [
            account
            for (g, _), account in self._rows.items()
            if g == guild_id and account.last_activity.timestamp() >= cutoff
        ]


def _conforms_to_user_repo(repo: IUserRepo) -> IUserRepo:
    """Identity that only type-checks if ``repo`` satisfies ``IUserRepo``."""
    return repo


async def test_fake_user_repo_satisfies_protocol() -> None:
    """A minimal fake round-trips through the IUserRepo surface."""
    repo: IUserRepo = _conforms_to_user_repo(_FakeUserRepo())
    account = _make_account("alice")

    await repo.upsert("g1", account)
    fetched = await repo.get("g1", "alice")

    assert fetched is account
    assert list(await repo.list_all("g1")) == [account]
    assert list(await repo.list_active_in_last("g1", 60.0)) == [account]

    await repo.delete("g1", "alice")
    assert await repo.get("g1", "alice") is None


# ---------------------------------------------------------------------------
# #84 M — Sequence covariance on list_all / list_active_in_last
# ---------------------------------------------------------------------------


class _TupleReturningUserRepo:
    """Returns ``tuple`` rather than ``list`` from list_all-style methods.

    Pins that the #84 M widening of the Protocol's return type to
    :class:`~collections.abc.Sequence` lets implementations honour the
    contract with any ordered immutable container — pre-fix the Protocol
    locked every implementation to a concrete ``list``.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], UserAccount] = {}

    async def get(self, guild_id: str, user_id: str) -> UserAccount | None:
        return self._rows.get((guild_id, user_id))

    async def upsert(self, guild_id: str, account: UserAccount) -> None:
        self._rows[(guild_id, account.user_id)] = account

    async def delete(self, guild_id: str, user_id: str) -> None:
        self._rows.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> tuple[UserAccount, ...]:
        return tuple(account for (g, _), account in self._rows.items() if g == guild_id)

    async def list_active_in_last(
        self, guild_id: str, seconds: float
    ) -> tuple[UserAccount, ...]:
        cutoff = datetime.now(tz=UTC).timestamp() - seconds
        return tuple(
            account
            for (g, _), account in self._rows.items()
            if g == guild_id and account.last_activity.timestamp() >= cutoff
        )


async def test_protocol_accepts_tuple_returning_implementation() -> None:
    """Pin the Sequence covariance: a tuple-returning fake satisfies
    :class:`IUserRepo`. Pre-#84 M the Protocol returned ``list[T]``, which
    locked implementations to concrete lists; widening to ``Sequence[T]``
    permits any ordered immutable container.
    """
    repo: IUserRepo = _TupleReturningUserRepo()
    account = _make_account("alice")
    await repo.upsert("g1", account)

    rows = await repo.list_all("g1")
    # Returns an actual tuple — not just structurally a ``Sequence``.
    assert isinstance(rows, tuple)
    assert rows == (account,)

    active = await repo.list_active_in_last("g1", 3600.0)
    assert isinstance(active, tuple)
    assert active == (account,)


# --- AC2 anchors for the remaining Protocols (signature smoke checks) -------


class _FakePriceRepo:
    def __init__(self) -> None:
        self._stocks: dict[tuple[str, str], Stock] = {}
        self._history: dict[tuple[str, str], list[PricePoint]] = {}

    async def get(self, guild_id: str, user_id: str) -> Stock | None:
        return self._stocks.get((guild_id, user_id))

    async def upsert(self, guild_id: str, stock: Stock) -> None:
        self._stocks[(guild_id, stock.user_id)] = stock

    async def delete(self, guild_id: str, user_id: str) -> None:
        self._stocks.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[Stock]:
        return [v for (g, _), v in self._stocks.items() if g == guild_id]

    async def append_history(
        self, guild_id: str, user_id: str, point: PricePoint
    ) -> None:
        self._history.setdefault((guild_id, user_id), []).append(point)

    async def get_history(
        self, guild_id: str, user_id: str, *, since: datetime | None = None
    ) -> list[PricePoint]:
        points = self._history.get((guild_id, user_id), [])
        if since is None:
            return list(points)
        return [p for p in points if p.timestamp >= since]

    async def prune_history_older_than(self, cutoff: datetime) -> int:
        removed = 0
        for key, points in self._history.items():
            kept = [p for p in points if p.timestamp >= cutoff]
            removed += len(points) - len(kept)
            self._history[key] = kept
        return removed


def test_fake_price_repo_satisfies_protocol() -> None:
    repo: IPriceRepo = _FakePriceRepo()
    assert repo is not None


class _FakeFundRepo:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], HedgeFund] = {}

    async def get(self, guild_id: str, fund_id: str) -> HedgeFund | None:
        return self._rows.get((guild_id, fund_id))

    async def upsert(self, guild_id: str, fund: HedgeFund) -> None:
        self._rows[(guild_id, fund.fund_id)] = fund

    async def delete(self, guild_id: str, fund_id: str) -> None:
        self._rows.pop((guild_id, fund_id), None)

    async def list_all(self, guild_id: str) -> list[HedgeFund]:
        return [v for (g, _), v in self._rows.items() if g == guild_id]

    async def ensure_events_wallet(self, guild_id: str) -> HedgeFund:
        key = (guild_id, "events_wallet")
        existing = self._rows.get(key)
        if existing is not None:
            return existing
        wallet = HedgeFund(
            fund_id="events_wallet",
            name="Events Wallet",
            manager_id="events_wallet",
            cash_balance=Decimal("0.00"),
            investors={},
        )
        self._rows[key] = wallet
        return wallet


def test_fake_fund_repo_satisfies_protocol() -> None:
    repo: IFundRepo = _FakeFundRepo()
    assert repo is not None


class _FakePenaltyRepo:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], FundPenalty] = {}

    async def get(self, guild_id: str, user_id: str) -> FundPenalty | None:
        return self._rows.get((guild_id, user_id))

    async def upsert(self, guild_id: str, penalty: FundPenalty) -> None:
        self._rows[(guild_id, penalty.user_id)] = penalty

    async def delete(self, guild_id: str, user_id: str) -> None:
        self._rows.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[FundPenalty]:
        return [v for (g, _), v in self._rows.items() if g == guild_id]


def test_fake_penalty_repo_satisfies_protocol() -> None:
    repo: IPenaltyRepo = _FakePenaltyRepo()
    assert repo is not None


class _FakeCooldownRepo:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], TradeCooldown] = {}

    async def get(
        self, guild_id: str, user_id: str, *, now: datetime
    ) -> TradeCooldown | None:
        row = self._rows.get((guild_id, user_id))
        if row is None or row.expires_at <= now:
            return None
        return row

    async def upsert(self, cooldown: TradeCooldown) -> None:
        self._rows[(cooldown.guild_id, cooldown.user_id)] = cooldown

    async def delete(self, guild_id: str, user_id: str) -> None:
        self._rows.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[TradeCooldown]:
        return [v for (g, _), v in self._rows.items() if g == guild_id]

    async def purge_expired(self, now: datetime) -> int:
        expired = [k for k, v in self._rows.items() if v.expires_at <= now]
        for key in expired:
            del self._rows[key]
        return len(expired)


def test_fake_cooldown_repo_satisfies_protocol() -> None:
    repo: ITradeCooldownRepo = _FakeCooldownRepo()
    assert repo is not None


def test_cooldown_repo_get_declares_keyword_only_now() -> None:
    """``ITradeCooldownRepo.get`` must declare a keyword-only ``now: datetime``.

    The SQLAlchemy adapter (``SqlTradeCooldownRepository``) and the in-memory
    fake (``FakeTradeCooldownRepo``) both accept ``*, now=`` so the active-only
    filter is testable against a frozen clock; the Protocol previously failed to
    declare it, leaving the contract narrower than the implementations.
    """
    sig = inspect.signature(ITradeCooldownRepo.get)
    assert "now" in sig.parameters, (
        "ITradeCooldownRepo.get must accept a 'now' parameter so the active-only "
        "filter (return None when expires_at <= now) is part of the contract"
    )
    now_param = sig.parameters["now"]
    assert now_param.kind is inspect.Parameter.KEYWORD_ONLY, (
        "'now' must be keyword-only to match SqlTradeCooldownRepository.get and "
        "FakeTradeCooldownRepo.get"
    )


async def test_cooldown_repo_get_returns_none_for_expired_row() -> None:
    """An expired cooldown is filtered out — the protocol contract's whole point.

    Behavioural assertion against the production fake (which the trading
    service uses in tests): a row whose ``expires_at`` is at or before ``now``
    must surface as ``None``, identical to a missing key. Anchors the semantic
    half of the Protocol widening so the service can drop its compensating
    in-service "is expired" arithmetic.
    """
    from tests.application.fakes.fake_repos import FakeTradeCooldownRepo

    repo: ITradeCooldownRepo = FakeTradeCooldownRepo()
    now = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)

    # Active row — returned.
    active = TradeCooldown(
        guild_id="g1", user_id="alice", expires_at=now + timedelta(seconds=30)
    )
    await repo.upsert(active)
    assert await repo.get("g1", "alice", now=now) == active

    # Expired row — filtered out.
    expired = TradeCooldown(
        guild_id="g1", user_id="bob", expires_at=now - timedelta(seconds=1)
    )
    await repo.upsert(expired)
    assert await repo.get("g1", "bob", now=now) is None

    # Missing row — also None.
    assert await repo.get("g1", "missing", now=now) is None


class _FakeSystemStateRepo:
    def __init__(self) -> None:
        self._rows: dict[str, SystemState] = {}

    async def get(self, guild_id: str) -> SystemState | None:
        return self._rows.get(guild_id)

    async def upsert(self, state: SystemState) -> None:
        self._rows[state.guild_id] = state

    async def delete(self, guild_id: str) -> None:
        self._rows.pop(guild_id, None)

    async def list_all(self) -> list[SystemState]:
        return list(self._rows.values())


def test_fake_system_state_repo_satisfies_protocol() -> None:
    repo: ISystemStateRepo = _FakeSystemStateRepo()
    assert repo is not None


# --- Persistence-failure contract pins (review M2) -------------------------
#
# ``IFundRepo.get`` and ``IUserRepo.get`` MUST propagate persistence failures
# rather than swallow them into a ``None`` return — the latter silently
# re-introduces the #84 H ghost-fund regression because the trading service
# cannot distinguish "absent" from "read failed". The contract lives in the
# Protocol docstring (Protocols carry no runtime behaviour); these tests
# pin that the docstring contains the load-bearing language so a docstring
# rewrite that drops the contract fails CI before reviewers have to catch it.


@pytest.mark.parametrize(
    ("protocol", "load_bearing"),
    [
        (IFundRepo.get, "persistence failures"),
        (IFundRepo.get, "must propagate"),
        (IFundRepo.get, "ghost-fund"),
        (IUserRepo.get, "persistence failures"),
        (IUserRepo.get, "must propagate"),
    ],
)
def test_get_protocol_docstring_pins_failure_propagation_contract(
    protocol: object, load_bearing: str
) -> None:
    """Pin the load-bearing language in ``I*Repo.get`` Protocol docstrings.

    The persistence-failure-propagation contract is the contract that
    underwrites #84 H's ghost-fund guard. Rewording the docstring is
    fine, but removing the load-bearing terms ("persistence failures",
    "MUST propagate", "ghost-fund" for IFundRepo) breaks the contract.
    Case-insensitive match so the docstring can use sentence-case
    ("Persistence failures...") without the test falsely tripping.
    """
    doc = inspect.getdoc(protocol) or ""
    assert load_bearing.lower() in doc.lower(), (
        f"docstring must mention '{load_bearing}' — see review M2 / #84 H"
    )


# --- helpers ---------------------------------------------------------------


def _make_account(user_id: str) -> UserAccount:
    from friendex.domain.models import ActivityBucket, DailyProgress

    now = datetime.now(tz=UTC)
    return UserAccount(
        user_id=user_id,
        cash_balance=Decimal("10000.00"),
        net_worth=Decimal("10000.00"),
        month_start_net_worth=Decimal("10000.00"),
        long_positions={},
        short_positions={},
        today=ActivityBucket(),
        week=ActivityBucket(),
        daily=DailyProgress(last_claim=None, streak=0),
        last_activity=now,
    )
