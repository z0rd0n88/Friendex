"""Repository ``Protocol`` interfaces for the application layer.

These are the *ports* of the hexagonal architecture: the application services
(Phases 7-9) depend on these structural interfaces, and the SQLAlchemy-backed
``SqlXxxRepository`` adapters (Phase 6 sub-units 6c-6f) satisfy them. Using
:class:`typing.Protocol` (structural typing) means the adapters do **not** need
to inherit from these classes — they conform by shape — which keeps the
dependency arrow pointing inward (``adapters -> application -> domain``) without
the application layer ever importing an adapter.

**Architecture invariant (enforced by review + a conformance test):** this
module imports **only** from :mod:`friendex.domain` plus the standard library /
``typing``. It must never import from :mod:`friendex.adapters`.

**Guild scoping (ADR-0001).** Every market is keyed by ``(guild_id, user_id)``.
The domain dataclasses are guild-agnostic, so each method takes ``guild_id`` as
an explicit parameter rather than baking it into the model. ``upsert`` therefore
takes ``guild_id`` plus the guild-agnostic domain object; the repository
attaches the scope when persisting.

**Async.** Every method is ``async`` because the backing store is async
SQLAlchemy 2.0 (``sqlite+aiosqlite``).

**Decimal / datetime invariants (Phase 3.1).** Money/price values flow as
:class:`decimal.Decimal` and timestamps as tz-aware UTC :class:`datetime`,
preserved unchanged across the boundary — these Protocols never widen them to
``float`` or naive datetimes.

Two tables in the ORM — ``system_state`` and ``trade_cooldowns`` — are pure
adapter bookkeeping with no domain dataclass mirror (see ``orm.py``). To keep
``interfaces.py`` free of any ORM import while still giving
:class:`ISystemStateRepo` / :class:`ITradeCooldownRepo` typed payloads, this
module defines two small frozen DTOs (:class:`SystemState`,
:class:`TradeCooldown`) here in the application layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import date, datetime

    from friendex.domain.models import (
        FundPenalty,
        HedgeFund,
        PricePoint,
        Stock,
        UserAccount,
    )

# ---------------------------------------------------------------------------
# Adapter-bookkeeping DTOs (no domain mirror; defined here, not in adapters)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SystemState:
    """Per-guild background-task reset bookkeeping.

    Mirrors ``SystemStateORM``. ``guild_id`` is the whole identity (one row per
    guild). Lives in the application layer because there is no domain dataclass
    for it and the interfaces must not import the ORM.

    ``last_monthly_rollover`` is a :class:`date` (year+month suffices for
    boundary checks) rather than a :class:`datetime` — month-granular
    bookkeeping reads "first of this month" naturally as ``date(y, m, 1)`` and
    avoids tz nuance for a field whose comparison is month-scope.

    ``last_portfolio_capture`` is a separate :class:`date` marker that
    advances **as soon as the portfolio capture succeeds** for a month, even
    if the fund-accrual step subsequently fails. The monthly rollover task
    uses it to skip portfolio capture on a retry-only-fund replay, sparing
    duplicate-but-idempotent work. The "both succeeded" guard is still
    ``last_monthly_rollover`` (only advanced after fund accrual lands too).
    """

    guild_id: str
    last_daily_reset: datetime | None = None
    last_weekly_reset: datetime | None = None
    last_monthly_rollover: date | None = None
    last_portfolio_capture: date | None = None


@dataclass(frozen=True)
class TradeCooldown:
    """A short/cover cooldown entry with a TTL via ``expires_at``.

    Mirrors ``TradeCooldownORM``. A row is considered *active* only while
    ``expires_at`` is in the future; :meth:`ITradeCooldownRepo.get` filters out
    expired rows so callers never see a stale cooldown.
    """

    guild_id: str
    user_id: str
    expires_at: datetime


# ---------------------------------------------------------------------------
# Repository Protocols
# ---------------------------------------------------------------------------


class IUserRepo(Protocol):
    """Persistence port for :class:`~friendex.domain.models.UserAccount`.

    A user aggregate owns its long/short positions and today/week activity
    buckets; ``upsert`` and ``get`` persist and rebuild the whole aggregate.
    ``delete`` relies on DB-level ``ON DELETE CASCADE`` (ADR-0002) to remove
    child rows — implementations do not hand-roll child cleanup.
    """

    async def get(self, guild_id: str, user_id: str) -> UserAccount | None:
        """Return the account for ``(guild_id, user_id)`` or ``None``.

        Returns ``None`` for a genuinely-absent user (the row does not
        exist in the store). **Persistence failures (engine error,
        connection drop, schema mismatch, etc.) MUST propagate** —
        implementations may not absorb an exception and return ``None``
        in its place. This contract is symmetric with :meth:`IFundRepo.get`
        and underwrites the ``_get_or_create_user`` auto-seed flow in
        :class:`TradingService`: "user absent" → seed defaults, "user
        read failed" → abort with the original exception.
        """
        ...

    async def upsert(self, guild_id: str, account: UserAccount) -> None:
        """Insert or replace ``account`` (and its children) under ``guild_id``."""
        ...

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the account; children cascade at the DB level."""
        ...

    async def list_all(self, guild_id: str) -> list[UserAccount]:
        """Return every account in ``guild_id``."""
        ...

    async def list_active_in_last(
        self, guild_id: str, seconds: float
    ) -> list[UserAccount]:
        """Return accounts whose ``last_activity`` is within ``seconds`` of now.

        Used by the activity-tick / inactivity-decay paths to scope work to
        recently-active users rather than scanning the whole guild.
        """
        ...


class IPriceRepo(Protocol):
    """Persistence port for :class:`~friendex.domain.models.Stock`.

    A stock owns an append-only price history. The scalar stock row and its
    history live in separate tables, so history has dedicated methods rather
    than being round-tripped wholesale on every ``upsert``.
    """

    async def get(self, guild_id: str, user_id: str) -> Stock | None:
        """Return the stock for ``(guild_id, user_id)`` or ``None``."""
        ...

    async def upsert(self, guild_id: str, stock: Stock) -> None:
        """Insert or replace the stock's scalar row under ``guild_id``."""
        ...

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the stock; price history cascades at the DB level."""
        ...

    async def list_all(self, guild_id: str) -> list[Stock]:
        """Return every stock in ``guild_id``."""
        ...

    async def append_history(
        self, guild_id: str, user_id: str, point: PricePoint
    ) -> None:
        """Append one :class:`PricePoint` to a stock's history (append-only)."""
        ...

    async def get_history(
        self, guild_id: str, user_id: str, *, since: datetime | None = None
    ) -> list[PricePoint]:
        """Return a stock's price history, oldest first.

        ``since`` (tz-aware UTC) restricts the result to points at or after that
        instant — used to compute the rolling 24h high/low dynamically rather
        than storing them (per the §Open-Q9 decision).
        """
        ...

    async def prune_history_older_than(self, cutoff: datetime) -> int:
        """Delete all price-history rows older than ``cutoff``; return the count.

        A single ``DELETE WHERE recorded_at < cutoff`` across every guild,
        called by the history-retention sweep.
        """
        ...


class IFundRepo(Protocol):
    """Persistence port for :class:`~friendex.domain.models.HedgeFund`.

    A fund owns its investor stakes (``investors`` dict); ``upsert`` and ``get``
    persist and rebuild the whole aggregate including investors.
    """

    async def get(self, guild_id: str, fund_id: str) -> HedgeFund | None:
        """Return the fund for ``(guild_id, fund_id)`` or ``None``.

        Returns ``None`` for a genuinely-absent fund (the row does not
        exist in the store).

        **Persistence failures (engine error, connection drop, schema
        mismatch, etc.) MUST propagate** — implementations may not
        absorb an exception and return ``None`` in its place. This
        contract underwrites :meth:`TradingService._get_fund_cash`'s
        ghost-fund guard (#84 H): the trading service distinguishes
        "fund absent" (legitimate $0 collateral) from "fund read failed"
        (abort the trade entirely, do NOT build a position against a
        phantom fund). Swallowing a failure into ``None`` silently
        re-introduces the ghost-fund regression.
        """
        ...

    async def upsert(self, guild_id: str, fund: HedgeFund) -> None:
        """Insert or replace ``fund`` (and its investors) under ``guild_id``."""
        ...

    async def delete(self, guild_id: str, fund_id: str) -> None:
        """Delete the fund; investor rows cascade at the DB level."""
        ...

    async def list_all(self, guild_id: str) -> list[HedgeFund]:
        """Return every fund in ``guild_id``."""
        ...

    async def ensure_events_wallet(self, guild_id: str) -> HedgeFund:
        """Return the guild's ``events_wallet`` pseudo-fund, creating it if absent.

        Idempotent: a repeat call returns the existing wallet without mutating
        its balance. The events wallet is the treasury target for
        ``/fund send_events`` (no APY penalty).
        """
        ...


class IPenaltyRepo(Protocol):
    """Persistence port for :class:`~friendex.domain.models.FundPenalty`.

    Early-withdrawal APY penalties, keyed by ``(guild_id, user_id)``. The
    penalty-decay task lists all penalties and re-``upsert``s or ``delete``s
    each as its window elapses.
    """

    async def get(self, guild_id: str, user_id: str) -> FundPenalty | None:
        """Return the penalty for ``(guild_id, user_id)`` or ``None``."""
        ...

    async def upsert(self, guild_id: str, penalty: FundPenalty) -> None:
        """Insert or replace ``penalty`` under ``guild_id``."""
        ...

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the penalty for ``(guild_id, user_id)``."""
        ...

    async def list_all(self, guild_id: str) -> list[FundPenalty]:
        """Return every penalty in ``guild_id``."""
        ...


class ITradeCooldownRepo(Protocol):
    """Persistence port for short/cover cooldowns with TTL semantics.

    Replaces the original Redis-native TTL: ``get`` excludes rows whose
    ``expires_at`` has passed, and :meth:`purge_expired` is the sweep that
    physically removes them. The payload is the application-layer
    :class:`TradeCooldown` DTO (``upsert`` carries ``guild_id`` inside it).
    """

    async def get(
        self, guild_id: str, user_id: str, *, now: datetime
    ) -> TradeCooldown | None:
        """Return the *active* cooldown, or ``None`` if absent or expired.

        ``now`` is keyword-only and required so the active-vs-expired filter
        is part of the contract — a row is considered expired (and excluded)
        once ``expires_at <= now``. Callers (the trading service, background
        sweeps, tests under ``freeze_time``) pass a deterministic UTC instant;
        relying on the repo to take ``datetime.now(UTC)`` itself would couple
        the contract to wall-clock time and leave a race window between the
        caller's "now" and the repo's "now". The SQLAlchemy adapter and the
        in-memory fake both accept this kwarg.
        """
        ...

    async def upsert(self, cooldown: TradeCooldown) -> None:
        """Insert or replace a cooldown (scope carried in the DTO)."""
        ...

    async def delete(self, guild_id: str, user_id: str) -> None:
        """Delete the cooldown for ``(guild_id, user_id)``."""
        ...

    async def list_all(self, guild_id: str) -> list[TradeCooldown]:
        """Return every cooldown row in ``guild_id`` (including expired ones)."""
        ...

    async def purge_expired(self, now: datetime) -> int:
        """Delete every cooldown whose ``expires_at <= now``; return the count."""
        ...


class ISystemStateRepo(Protocol):
    """Persistence port for per-guild background-task reset state.

    One :class:`SystemState` row per guild, read/written by the daily and
    weekly reset tasks. The payload is the application-layer DTO; ``upsert``
    carries ``guild_id`` inside it. ``list_all`` is unscoped because the reset
    tasks iterate every guild.
    """

    async def get(self, guild_id: str) -> SystemState | None:
        """Return the state row for ``guild_id`` or ``None``."""
        ...

    async def upsert(self, state: SystemState) -> None:
        """Insert or replace the state row (scope carried in the DTO)."""
        ...

    async def delete(self, guild_id: str) -> None:
        """Delete the state row for ``guild_id``."""
        ...

    async def list_all(self) -> list[SystemState]:
        """Return the state row for every guild."""
        ...
