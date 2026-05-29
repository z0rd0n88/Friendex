"""Hedge-fund use cases for the Phase 8e service layer.

:class:`FundService` mediates between the ``/fund`` slash sub-commands (Phase
11 ``FundCog``) — ``create``, ``info``, ``withdraw``, ``send_events`` — and
the persistence ports, plus the monthly APY accrual the Phase 9
``MonthlyRolloverTask`` invokes on the 1st of each month.

**Game-rule envelope** (mirrors the original ``$fund`` command at
``docs/spec/original-skeleton.md:1359-1484``):

* ``create_or_rename`` is idempotent — an absent fund is created with the
  default name ``"Fund <user_id>"``; an existing fund is renamed if a
  ``name`` is supplied.
* ``withdraw`` moves cash from the user's personal hedge fund to their
  trading account. On the **1st of the calendar month** (the canonical
  monthly-rollover day, spec line 1434 ``if now.day != 1``) the
  early-withdrawal penalty is skipped; on any other day a fresh
  :class:`FundPenalty` row is upserted (or extended) at
  ``settings.early_withdraw_penalty`` for
  ``settings.penalty_duration_days`` days. Per the original
  ``apply_early_withdraw_penalty`` semantics (spec line 614), an
  already-active penalty has its APR **stacked**, not replaced.
* ``send_to_events`` transfers cash from the user's fund to the per-guild
  ``events_wallet`` pseudo-fund (created lazily via
  :meth:`IFundRepo.ensure_events_wallet`) and is exempt from the
  early-withdrawal penalty even mid-month (spec line 1475 — explicitly
  marked as "no APY penalty").
* ``accrue_apy(now)`` sweeps every personal fund in the guild and credits
  the cadence-based accrual computed by
  :func:`friendex.domain.fund_math.compute_apy_accrual`. The ``effective_apy``
  reflects any active :class:`FundPenalty` via
  :func:`friendex.domain.fund_math.compute_effective_apy`. Accrual is
  **split per stake**: the manager's balance (``cash_balance -
  sum(investors.values())``) earns its own accrual, and every investor
  stake earns its own accrual; the resulting deltas are credited back to
  the manager's balance share and to each investor's stake respectively
  so the principal that belongs to investors stays accounted-for. The
  ``events_wallet`` pseudo-fund is skipped (treasury, not an investor).
* ``invest(investor_id, fund_id, amount)`` moves ``amount`` from the
  investor's trading cash into the target fund. The investor's account is
  debited, the fund's ``cash_balance`` is credited, and the investor's
  stake (``fund.investors[investor_id]``) is set or incremented. A
  manager cannot invest in their own fund (Phase 17b §Q2:
  :class:`InvalidAmount`), a non-positive amount is rejected as
  :class:`InvalidAmount`, an absent fund is rejected as
  :class:`InvalidAmount`, and an investor without enough cash gets an
  :class:`InsufficientFunds` error.

**Guild scoping (ADR-0001 / Phase 8a digest).** ``guild_id`` is a constructor
argument captured once as ``self._guild_id``; domain models stay
guild-agnostic. Lock keys use the composite ``"<guild_id>:<user_id>"`` shape
built by :meth:`_lock_key`.

**Concurrency (Phase 7 / Phase 8b RMW discipline).** Every mutating method
takes ``async with self._locks.locked(self._lock_key(user_id))`` in ONE
critical section, reads the fresh aggregate inside the lock, and round-trips
via :func:`dataclasses.replace` — matching the
:class:`PriceTickService._rmw_price` shape (Phase 8b digest §convention 2).
``send_to_events`` locks both the user and the ``events_wallet`` pseudo-id in
one ``locked(user, "events_wallet")`` call so the actor and the treasury
update atomically without nested-lock deadlocks (the lock is non-reentrant —
Phase 7 digest). ``accrue_apy`` is a sweep: per-fund ``locked(...)`` one fund
at a time, never wrapping the whole loop (Phase 8c digest §convention 1).

**Decimal + UTC invariants** preserved end-to-end (Phase 3.1):
``Decimal(str(settings_float))`` for any float-sourced rate, and
``penalty_until`` is always a tz-aware UTC :class:`datetime`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import TYPE_CHECKING

from friendex.domain.errors import (
    FundInsufficientBalance,
    InsufficientFunds,
    InvalidAmount,
)
from friendex.domain.fund_math import (
    compute_apy_accrual,
    compute_effective_apy,
)
from friendex.domain.models import FundPenalty, HedgeFund

if TYPE_CHECKING:
    from datetime import datetime

    from friendex.adapters.config import Settings
    from friendex.application.interfaces import (
        IFundRepo,
        IPenaltyRepo,
        IUserRepo,
    )
    from friendex.application.lock_manager import LockManager
    from friendex.application.snapshot_models import FundInfoResult

# Currency quantisation unit — two decimal places, banker's rounding.
_CENT = Decimal("0.01")
# Personal fund cash floor when seeded by ``create_or_rename`` (matches the
# original spec at ``original-skeleton.md:302-308``).
_ZERO_CASH = Decimal("0.00")
# Shared ``Decimal`` zero used as the ``sum(..., start=_ZERO)`` starter for
# investor-stake totals.  Replaces the three per-call-site re-constructions
# of ``Decimal("0.00")`` at the sum boundary.
_ZERO = Decimal("0")
# Pseudo-fund identity for the per-guild events wallet (matches
# ``FakeFundRepo`` and ``SqlFundRepository.ensure_events_wallet``).
_EVENTS_WALLET_ID = "events_wallet"
# Calendar day-of-month treated as the no-penalty monthly-rollover day
# (spec line 1434).
_NO_PENALTY_DAY_OF_MONTH = 1


def _quantise(value: Decimal) -> Decimal:
    """Round ``value`` to two decimal places with banker's rounding."""
    return value.quantize(_CENT, rounding=ROUND_HALF_EVEN)


class FundService:
    """Hedge-fund management + monthly APY accrual use cases."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        fund_repo: IFundRepo,
        penalty_repo: IPenaltyRepo,
        lock_manager: LockManager,
        settings: Settings,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._fund_repo = fund_repo
        self._penalty_repo = penalty_repo
        self._locks = lock_manager
        self._settings = settings

    # -- internal helpers ---------------------------------------------------

    def _lock_key(self, user_id: str) -> str:
        """Return the composite ``"<guild>:<user>"`` lock key (ADR-0001)."""
        return f"{self._guild_id}:{user_id}"

    @staticmethod
    def _default_fund_name(user_id: str) -> str:
        """Default personal-fund name (matches spec line 304)."""
        return f"Fund {user_id}"

    async def _get_or_create_fund(self, user_id: str) -> HedgeFund:
        """Return the personal fund, creating a zero-balance one if absent."""
        existing = await self._fund_repo.get(self._guild_id, user_id)
        if existing is not None:
            return existing
        fund = HedgeFund(
            fund_id=user_id,
            name=self._default_fund_name(user_id),
            manager_id=user_id,
            cash_balance=_ZERO_CASH,
            investors={},
        )
        await self._fund_repo.upsert(self._guild_id, fund)
        return fund

    # -- public read use case (lockless) ------------------------------------

    async def fund_info(self, user_id: str, now: datetime) -> FundInfoResult | None:
        """Return ``user_id``'s fund as a display-ready :class:`FundInfoResult`.

        Best-effort read — no lock is held. Fetches both the
        :class:`HedgeFund` and any active :class:`FundPenalty` in two
        repository reads, computes ``effective_apy`` via
        :func:`~friendex.domain.fund_math.compute_effective_apy`, and
        packages everything the ``/fund info`` embed builder needs so the
        cog does not require access to :class:`Settings` or domain math.

        Returns ``None`` when the user has no personal fund.
        """
        from friendex.application.snapshot_models import FundInfoResult

        fund = await self._fund_repo.get(self._guild_id, user_id)
        if fund is None:
            return None
        penalty = await self._penalty_repo.get(self._guild_id, user_id)
        base_apy = self._settings.hedge_fund_base_apy
        effective_apy = compute_effective_apy(base_apy, penalty, now)
        has_penalty = penalty is not None and penalty.penalty_until > now
        return FundInfoResult(
            fund=fund,
            base_apy=base_apy,
            effective_apy=effective_apy,
            has_penalty=has_penalty,
        )

    # -- create / rename ----------------------------------------------------

    async def create_or_rename(
        self, user_id: str, name: str | None = None
    ) -> HedgeFund:
        """Create a personal fund or rename an existing one.

        With no existing fund:
            * Creates one at zero balance with name ``name`` (or the default
              ``"Fund <user_id>"`` when ``name is None``).

        With an existing fund:
            * Renames it to ``name`` if supplied (otherwise leaves it
              untouched and returns the current fund — a no-op idempotent
              path the original spec's ``$fund create`` also exposes at
              ``original-skeleton.md:1381``).

        The critical section reads the fund inside the lock so a concurrent
        ``withdraw`` cannot land between the read and the ``upsert``.
        """
        async with self._locks.locked(self._lock_key(user_id)):
            existing = await self._fund_repo.get(self._guild_id, user_id)
            if existing is None:
                fund = HedgeFund(
                    fund_id=user_id,
                    name=name if name is not None else self._default_fund_name(user_id),
                    manager_id=user_id,
                    cash_balance=_ZERO_CASH,
                    investors={},
                )
                await self._fund_repo.upsert(self._guild_id, fund)
                return fund
            if name is None:
                return existing
            renamed = replace(existing, name=name)
            await self._fund_repo.upsert(self._guild_id, renamed)
            return renamed

    # -- withdraw -----------------------------------------------------------

    async def withdraw(self, user_id: str, amount: Decimal, now: datetime) -> None:
        """Move ``amount`` from the user's personal fund to their trading cash.

        Applies the early-withdrawal penalty on any day except the 1st of the
        calendar month (spec line 1434). The penalty's APR is **stacked** on
        top of any existing active penalty (spec line 614). Raises
        :class:`InvalidAmount` for non-positive amounts and
        :class:`FundInsufficientBalance` when the personal fund cannot cover
        the requested withdrawal.

        Critical section reads both fund and account inside the lock and
        round-trips both via :func:`dataclasses.replace`.
        """
        if amount <= _ZERO_CASH:
            raise InvalidAmount("amount must be positive")
        quantised_amount = _quantise(amount)

        async with self._locks.locked(self._lock_key(user_id)):
            fund = await self._get_or_create_fund(user_id)
            # Phase 17b B2: cap the withdraw at the manager's own share —
            # investor principal (``sum(investors.values())``) is untouchable.
            manager_balance = _quantise(
                fund.cash_balance - sum(fund.investors.values(), _ZERO)
            )
            account = await self._user_repo.get(self._guild_id, user_id)
            if account is None:
                # ``withdraw`` requires a real account — surface a stable
                # error rather than auto-seed (the trading service owns the
                # auto-seed flow).
                raise FundInsufficientBalance(
                    need=quantised_amount, have=manager_balance
                )

            if manager_balance < quantised_amount:
                raise FundInsufficientBalance(
                    need=quantised_amount, have=manager_balance
                )

            new_fund = replace(
                fund,
                cash_balance=_quantise(fund.cash_balance - quantised_amount),
            )
            new_account = replace(
                account,
                cash_balance=_quantise(account.cash_balance + quantised_amount),
            )
            await self._fund_repo.upsert(self._guild_id, new_fund)
            await self._user_repo.upsert(self._guild_id, new_account)

            if now.day != _NO_PENALTY_DAY_OF_MONTH:
                await self._apply_early_withdraw_penalty(user_id, now)

    async def _apply_early_withdraw_penalty(self, user_id: str, now: datetime) -> None:
        """Stack the configured penalty on top of any active one (spec line 614)."""
        increment = Decimal(str(self._settings.early_withdraw_penalty))
        existing = await self._penalty_repo.get(self._guild_id, user_id)
        existing_apr = existing.penalty_apr if existing is not None else Decimal("0.00")
        new_apr = existing_apr + increment
        penalty_until = now + timedelta(days=self._settings.penalty_duration_days)
        await self._penalty_repo.upsert(
            self._guild_id,
            FundPenalty(
                user_id=user_id,
                penalty_apr=new_apr,
                penalty_until=penalty_until,
            ),
        )

    # -- send_to_events -----------------------------------------------------

    async def send_to_events(self, user_id: str, amount: Decimal) -> None:
        """Transfer ``amount`` from the user's fund to the events-wallet treasury.

        Exempt from the early-withdrawal penalty (spec line 1475). The events
        wallet is created lazily via
        :meth:`IFundRepo.ensure_events_wallet`. Raises
        :class:`InvalidAmount` for non-positive amounts and
        :class:`FundInsufficientBalance` when the personal fund cannot cover
        the transfer.

        Locks both the user and the events-wallet pseudo-id in a single
        ``locked(...)`` call so the treasury update cannot interleave with
        another guild member's send.
        """
        if amount <= _ZERO_CASH:
            raise InvalidAmount("amount must be positive")
        quantised_amount = _quantise(amount)

        async with self._locks.locked(
            self._lock_key(user_id),
            self._lock_key(_EVENTS_WALLET_ID),
        ):
            fund = await self._get_or_create_fund(user_id)
            wallet = await self._fund_repo.ensure_events_wallet(self._guild_id)

            if fund.cash_balance < quantised_amount:
                raise FundInsufficientBalance(
                    need=quantised_amount, have=fund.cash_balance
                )

            new_fund = replace(
                fund,
                cash_balance=_quantise(fund.cash_balance - quantised_amount),
            )
            new_wallet = replace(
                wallet,
                cash_balance=_quantise(wallet.cash_balance + quantised_amount),
            )
            await self._fund_repo.upsert(self._guild_id, new_fund)
            await self._fund_repo.upsert(self._guild_id, new_wallet)

    # -- monthly APY accrual ------------------------------------------------

    async def accrue_apy(self, now: datetime) -> None:
        """Credit the cadence-based APY accrual to every personal fund.

        Walks every fund via :meth:`IFundRepo.list_all`, skips the
        ``events_wallet`` pseudo-fund (treasury — never accrues), and
        per-fund takes ``self._locks.locked(self._lock_key(fund.fund_id))``,
        re-``get``s the fund inside the lock, computes the effective APY
        (factoring any active :class:`FundPenalty` via
        :func:`compute_effective_apy`), and runs a **per-stake split**:
        the manager's balance (``cash_balance - sum(investors.values())``)
        earns its own :func:`compute_apy_accrual`, and every investor
        stake earns its own. New investor stakes carry the per-stake
        accrual; the fund's new ``cash_balance`` is the sum of the
        post-accrual manager balance plus every post-accrual investor
        stake. A zero / sub-cent total accrual is skipped (idempotent
        no-op).

        Called by the Phase 9 ``MonthlyRolloverTask`` on the 1st of each
        month at hour 0; safe to retry — accrual for one ``(fund, now)``
        invocation is deterministic. The cadence is
        :attr:`Settings.hedge_fund_base_apy_period` (Phase 17a Open-Q8
        toggle — must thread through every ``compute_apy_accrual`` call).
        """
        funds = await self._fund_repo.list_all(self._guild_id)
        period = self._settings.hedge_fund_base_apy_period
        for fund in funds:
            if fund.fund_id == _EVENTS_WALLET_ID:
                continue
            async with self._locks.locked(self._lock_key(fund.fund_id)):
                fresh = await self._fund_repo.get(self._guild_id, fund.fund_id)
                if fresh is None:
                    continue
                penalty = await self._penalty_repo.get(self._guild_id, fund.fund_id)
                effective_apy = compute_effective_apy(
                    self._settings.hedge_fund_base_apy, penalty, now
                )
                manager_balance = fresh.cash_balance - sum(
                    fresh.investors.values(), _ZERO
                )
                manager_accrual = compute_apy_accrual(
                    manager_balance,
                    effective_apy,
                    period=period,
                )
                new_investors: dict[str, Decimal] = {}
                total_investor_accrual = Decimal("0.00")
                for investor_id, stake in fresh.investors.items():
                    investor_accrual = compute_apy_accrual(
                        stake,
                        effective_apy,
                        period=period,
                    )
                    total_investor_accrual += investor_accrual
                    new_investors[investor_id] = _quantise(stake + investor_accrual)
                if manager_accrual + total_investor_accrual < _CENT:
                    continue
                new_cash = _quantise(
                    manager_balance
                    + manager_accrual
                    + sum(new_investors.values(), _ZERO)
                )
                updated = replace(
                    fresh,
                    cash_balance=new_cash,
                    investors=new_investors,
                )
                await self._fund_repo.upsert(self._guild_id, updated)

    # -- invest -------------------------------------------------------------

    async def invest(
        self,
        investor_id: str,
        fund_id: str,
        amount: Decimal,
    ) -> None:
        """Move ``amount`` from ``investor_id``'s cash into ``fund_id``.

        Semantics:

        * ``amount <= 0`` raises :class:`InvalidAmount`.
        * A missing target fund raises :class:`InvalidAmount` (re-uses the
          existing taxonomy — no new error class).
        * A manager may not invest in their own fund (Phase 17b §Q2):
          raises :class:`InvalidAmount`.
        * A missing investor account, or an account that cannot cover the
          (quantised) amount, raises :class:`InsufficientFunds`.

        Atomic body (single critical section): debit investor cash, credit
        fund cash, set / increment ``fund.investors[investor_id]`` by the
        same quantised amount. The investors dict is cloned before
        mutation so the snapshot returned by :meth:`IFundRepo.get` is
        never modified in place. Lock keys cover both the investor and
        the fund in one ``self._locks.locked(...)`` call (the
        :class:`LockManager` is non-reentrant — never nest ``locked``).
        """
        if amount <= _ZERO_CASH:
            raise InvalidAmount("amount must be positive")
        quantised_amount = _quantise(amount)

        # Phase 17b §Q2 — a manager cannot invest in their own fund.
        if investor_id == fund_id:
            raise InvalidAmount("cannot invest in own fund")

        async with self._locks.locked(
            self._lock_key(investor_id),
            self._lock_key(fund_id),
        ):
            fund = await self._fund_repo.get(self._guild_id, fund_id)
            if fund is None:
                raise InvalidAmount("fund does not exist")

            account = await self._user_repo.get(self._guild_id, investor_id)
            if account is None or account.cash_balance < quantised_amount:
                have = account.cash_balance if account else Decimal("0.00")
                raise InsufficientFunds(need=quantised_amount, have=have)

            # Clone the investors dict — never mutate the snapshot the
            # repo handed us.
            new_investors = dict(fund.investors)
            new_investors[investor_id] = _quantise(
                new_investors.get(investor_id, Decimal("0.00")) + quantised_amount
            )
            new_fund = replace(
                fund,
                cash_balance=_quantise(fund.cash_balance + quantised_amount),
                investors=new_investors,
            )
            new_account = replace(
                account,
                cash_balance=_quantise(account.cash_balance - quantised_amount),
            )
            await self._user_repo.upsert(self._guild_id, new_account)
            await self._fund_repo.upsert(self._guild_id, new_fund)
