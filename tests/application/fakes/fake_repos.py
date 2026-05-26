"""In-memory fake repositories implementing the application persistence ports.

Each fake satisfies one ``Protocol`` from
:mod:`friendex.application.interfaces` *structurally* (it is asserted assignable
to the Protocol in the tests, never inherits from it) and stores aggregates in a
plain ``dict`` keyed the same way the real SQLAlchemy adapter keys its table:
``(guild_id, id)`` for guild-scoped aggregates, ``guild_id`` alone for
:class:`FakeSystemStateRepo`.

Behaviour mirrors the adapters in ``src/friendex/adapters/persistence`` so a
service that passes against a fake also passes against SQLite:

* **Immutable storage.** Stored aggregates are frozen-style dataclasses; the
  fakes hold references and never mutate them in place. ``upsert`` replaces the
  stored reference wholesale (delete-then-insert in the real repos).
* **Price history is append-only** and read back oldest-first;
  :meth:`FakePriceRepo.get_history` honours the ``since`` keyword and
  :meth:`FakePriceRepo.prune_history_older_than` drops only older points across
  every guild, returning the pruned count.
* **``ensure_events_wallet`` is idempotent** — a repeat call returns the
  existing wallet untouched.
* **Cooldown TTL.** :meth:`FakeTradeCooldownRepo.get` hides rows whose
  ``expires_at <= now`` (exclusive ``>`` boundary, matching the adapter) and
  :meth:`FakeTradeCooldownRepo.purge_expired` removes them (inclusive ``<=``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.domain.models import HedgeFund

if TYPE_CHECKING:
    from friendex.application.interfaces import SystemState, TradeCooldown
    from friendex.domain.models import (
        FundPenalty,
        PricePoint,
        Stock,
        UserAccount,
    )

# Events-wallet identity, mirroring ``SqlFundRepository`` so service tests see
# the same pseudo-fund the production adapter creates.
_EVENTS_WALLET_ID = "events_wallet"
_EVENTS_WALLET_NAME = "Events Wallet"
_EVENTS_WALLET_MANAGER_ID = "0"
_ZERO_CASH = Decimal("0.00")


class FakeUserRepo:
    """In-memory :class:`~friendex.application.interfaces.IUserRepo`."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], UserAccount] = {}

    async def get(self, guild_id: str, user_id: str) -> UserAccount | None:
        """Return the account for ``(guild_id, user_id)`` or ``None``."""
        return self._store.get((guild_id, user_id))

    async def upsert(self, guild_id: str, account: UserAccount) -> None:
        """Insert or replace ``account`` under ``guild_id``."""
        self._store[(guild_id, account.user_id)] = account

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the account for ``(guild_id, user_id)`` if present."""
        self._store.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[UserAccount]:
        """Return every account in ``guild_id``."""
        return [
            account
            for (stored_guild, _), account in self._store.items()
            if stored_guild == guild_id
        ]

    async def list_active_in_last(
        self, guild_id: str, seconds: float
    ) -> list[UserAccount]:
        """Return accounts whose ``last_activity`` is within ``seconds`` of now.

        Mirrors the adapter's ``last_activity >= now - seconds`` (inclusive
        boundary): an account exactly on the cutoff still counts as active.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=seconds)
        return [
            account
            for account in await self.list_all(guild_id)
            if account.last_activity >= cutoff
        ]


class FakePriceRepo:
    """In-memory :class:`~friendex.application.interfaces.IPriceRepo`.

    The scalar :class:`Stock` row and its append-only price history live in
    separate dicts, exactly as the adapter splits ``stocks`` and
    ``price_history`` into separate tables.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Stock] = {}
        self._history: dict[tuple[str, str], list[PricePoint]] = {}

    async def get(self, guild_id: str, user_id: str) -> Stock | None:
        """Return the stock for ``(guild_id, user_id)`` or ``None``."""
        return self._store.get((guild_id, user_id))

    async def upsert(self, guild_id: str, stock: Stock) -> None:
        """Insert or replace the stock's scalar row (history is untouched)."""
        self._store[(guild_id, stock.user_id)] = stock

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the stock and its history (cascade) for ``(guild_id, user_id)``."""
        self._store.pop((guild_id, user_id), None)
        self._history.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[Stock]:
        """Return every stock in ``guild_id``."""
        return [
            stock
            for (stored_guild, _), stock in self._store.items()
            if stored_guild == guild_id
        ]

    async def append_history(
        self, guild_id: str, user_id: str, point: PricePoint
    ) -> None:
        """Append one :class:`PricePoint` to a stock's history (append-only)."""
        self._history.setdefault((guild_id, user_id), []).append(point)

    async def get_history(
        self, guild_id: str, user_id: str, *, since: datetime | None = None
    ) -> list[PricePoint]:
        """Return a stock's price history, oldest first.

        ``since`` (tz-aware UTC) restricts the result to points at or after that
        instant, matching the adapter's ``recorded_at >= since`` filter.
        """
        points = sorted(
            self._history.get((guild_id, user_id), []),
            key=lambda p: p.timestamp,
        )
        if since is not None:
            points = [p for p in points if p.timestamp >= since]
        return points

    async def prune_history_older_than(self, cutoff: datetime) -> int:
        """Delete every history point older than ``cutoff``; return the count.

        Mirrors the adapter's single ``DELETE WHERE recorded_at < cutoff`` sweep
        across *every* guild: a point exactly at ``cutoff`` is kept.
        """
        removed = 0
        for key, points in self._history.items():
            kept = [p for p in points if p.timestamp >= cutoff]
            removed += len(points) - len(kept)
            self._history[key] = kept
        return removed


class FakeFundRepo:
    """In-memory :class:`~friendex.application.interfaces.IFundRepo`."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], HedgeFund] = {}

    async def get(self, guild_id: str, fund_id: str) -> HedgeFund | None:
        """Return the fund for ``(guild_id, fund_id)`` or ``None``."""
        return self._store.get((guild_id, fund_id))

    async def upsert(self, guild_id: str, fund: HedgeFund) -> None:
        """Insert or replace ``fund`` (and its investors) under ``guild_id``."""
        self._store[(guild_id, fund.fund_id)] = fund

    async def delete(self, guild_id: str, fund_id: str) -> None:
        """Delete the fund for ``(guild_id, fund_id)`` if present."""
        self._store.pop((guild_id, fund_id), None)

    async def list_all(self, guild_id: str) -> list[HedgeFund]:
        """Return every fund in ``guild_id``."""
        return [
            fund
            for (stored_guild, _), fund in self._store.items()
            if stored_guild == guild_id
        ]

    async def ensure_events_wallet(self, guild_id: str) -> HedgeFund:
        """Return the guild's ``events_wallet`` pseudo-fund, creating it if absent.

        Idempotent: a repeat call returns the existing wallet without mutating
        its balance, matching :meth:`SqlFundRepository.ensure_events_wallet`.
        """
        existing = await self.get(guild_id, _EVENTS_WALLET_ID)
        if existing is not None:
            return existing
        wallet = HedgeFund(
            fund_id=_EVENTS_WALLET_ID,
            name=_EVENTS_WALLET_NAME,
            manager_id=_EVENTS_WALLET_MANAGER_ID,
            cash_balance=_ZERO_CASH,
            investors={},
        )
        await self.upsert(guild_id, wallet)
        return wallet


class FakePenaltyRepo:
    """In-memory :class:`~friendex.application.interfaces.IPenaltyRepo`.

    A plain store, not a filter: an expired penalty is still returned by
    :meth:`get` and surfaced by :meth:`list_all`, mirroring the adapter — expiry
    is a domain decision, not a persistence one.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], FundPenalty] = {}

    async def get(self, guild_id: str, user_id: str) -> FundPenalty | None:
        """Return the penalty for ``(guild_id, user_id)`` or ``None``."""
        return self._store.get((guild_id, user_id))

    async def upsert(self, guild_id: str, penalty: FundPenalty) -> None:
        """Insert or replace ``penalty`` under ``guild_id``."""
        self._store[(guild_id, penalty.user_id)] = penalty

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the penalty for ``(guild_id, user_id)`` if present."""
        self._store.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[FundPenalty]:
        """Return every penalty in ``guild_id`` (live and expired alike)."""
        return [
            penalty
            for (stored_guild, _), penalty in self._store.items()
            if stored_guild == guild_id
        ]


class FakeTradeCooldownRepo:
    """In-memory :class:`~friendex.application.interfaces.ITradeCooldownRepo`.

    TTL semantics mirror the adapter: :meth:`get` returns a row only while
    ``expires_at > now`` (active), :meth:`list_all` returns every row including
    expired ones, and :meth:`purge_expired` removes rows with
    ``expires_at <= now``. The payload carries ``guild_id`` inside the DTO, so
    :meth:`upsert` takes no separate scope argument.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], TradeCooldown] = {}

    async def get(
        self, guild_id: str, user_id: str, *, now: datetime | None = None
    ) -> TradeCooldown | None:
        """Return the *active* cooldown, or ``None`` if absent or expired.

        ``now`` defaults to the current UTC instant; pass an explicit value for
        a deterministic clock (the adapter exposes the same keyword).
        """
        cutoff = now if now is not None else datetime.now(tz=UTC)
        cooldown = self._store.get((guild_id, user_id))
        if cooldown is None or cooldown.expires_at <= cutoff:
            return None
        return cooldown

    async def upsert(self, cooldown: TradeCooldown) -> None:
        """Insert or replace a cooldown (scope carried in the DTO)."""
        self._store[(cooldown.guild_id, cooldown.user_id)] = cooldown

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the cooldown for ``(guild_id, user_id)`` if present."""
        self._store.pop((guild_id, user_id), None)

    async def list_all(self, guild_id: str) -> list[TradeCooldown]:
        """Return every cooldown row in ``guild_id`` (including expired ones)."""
        return [
            cooldown
            for (stored_guild, _), cooldown in self._store.items()
            if stored_guild == guild_id
        ]

    async def purge_expired(self, now: datetime) -> int:
        """Delete every cooldown whose ``expires_at <= now``; return the count.

        Across every guild, matching the adapter's unscoped bulk sweep. The
        ``<=`` boundary matches :meth:`get` so a row is never both hidden by
        ``get`` and a survivor of the purge.
        """
        expired = [
            key for key, cooldown in self._store.items() if cooldown.expires_at <= now
        ]
        for key in expired:
            del self._store[key]
        return len(expired)


class FakeSystemStateRepo:
    """In-memory :class:`~friendex.application.interfaces.ISystemStateRepo`.

    One row per guild (``guild_id`` is the whole key), so :meth:`upsert` on a
    repeat ``guild_id`` is an in-place replacement — never a duplicate row.
    ``list_all`` is unscoped because the reset tasks iterate every guild.
    """

    def __init__(self) -> None:
        self._store: dict[str, SystemState] = {}

    async def get(self, guild_id: str) -> SystemState | None:
        """Return the state row for ``guild_id`` or ``None`` (never reset)."""
        return self._store.get(guild_id)

    async def upsert(self, state: SystemState) -> None:
        """Insert or replace the state row (scope carried in the DTO)."""
        self._store[state.guild_id] = state

    async def delete(self, guild_id: str) -> None:
        """Delete the state row for ``guild_id`` if present."""
        self._store.pop(guild_id, None)

    async def list_all(self) -> list[SystemState]:
        """Return the state row for every guild (unscoped)."""
        return list(self._store.values())
