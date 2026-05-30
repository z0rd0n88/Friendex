"""Application service that owns the buy / sell / short / cover use cases.

:class:`TradingService` is the most complex application-layer service in the
Friendex migration. It mediates between the four ``/buy``, ``/sell``,
``/short``, ``/cover`` Discord slash commands (Phase 11 cogs) and the
persistence ports, enforcing the full game-rule envelope around every trade:

* **Market hours** via :func:`~friendex.domain.market_hours.is_market_open`,
  with the original spec's Sunday-buy exception — buys are allowed on Sundays
  *only* (sell, short, and cover follow the normal weekday window).
* **Opt-in** — a target with ``opt_in=False`` is not tradable in any direction.
* **Self-trade** — the actor cannot trade their own stock.
* **Cash floor** — buys and covers require enough cash; shorts spread the
  notional across cash + at-most 50% of the shorter's personal hedge fund.
* **Short cooldown** — short and cover share a single per-user cooldown of
  ``settings.trade_cooldown_seconds`` (default 15 minutes). Buy and sell do
  NOT consume the cooldown (per the original ``set_trade_time`` callsites).
* **Short freeze** — a short position is frozen ``settings.short_freeze_minutes``
  after creation; a frozen position blocks manual cover and any short top-up.
  The :meth:`update_frozen_shorts` sweep (Phase 9 background loop) walks every
  account and flips ``frozen=True`` once the age threshold is crossed. The
  user-facing :meth:`cover` always raises :class:`PositionFrozen` on a frozen
  position; :class:`LiquidationService` bypasses the freeze guard via the
  public :meth:`cover_forced` (#82 M1 — was a direct reach into the private
  ``_cover_internal`` until this consolidation).

**Guild scoping (ADR-0001 / Phase 8a digest).** ``guild_id`` is a constructor
argument captured once as ``self._guild_id``; domain models stay
guild-agnostic. Every lock acquisition uses the composite
``"<guild_id>:<user_id>"`` key built by :meth:`_lock_key` so the single shared
:class:`~friendex.application.lock_manager.LockManager` Phase 14 injects across
every per-guild scope cannot serialise unrelated guilds against each other.

**Concurrency (Phase 7 / Phase 8b RMW discipline).** Every trade locks
*both* the actor and the target in a SINGLE
``async with self._locks.locked(actor, target):`` call — one critical section
per trade, never nested (the lock is non-reentrant and ``locked()`` sorts the
ids for deadlock-free acquisition). Inside the critical section the service
re-reads every aggregate it is about to mutate so a concurrent tick or trade
landing between the public-method entry and the lock acquire is never
clobbered.

**Price impact RMW (mirrors :class:`PriceTickService._rmw_price`).** Every
trade nudges the target's price via
:func:`~friendex.domain.price_engine.apply_trade_impact`. The price read,
new-price compute, ``Stock.upsert``, ``append_history`` call, and
``all_time_high`` ratchet all happen inside the already-held critical section
— the price RMW does NOT take the lock itself (the outer trade method already
holds it), so re-entering ``locked()`` is avoided.

**Collateral split (short).** Mirrors ``original-skeleton.md`` §$short
verbatim: ``fund_available = fund_cash * 0.5``;
``total_collateral = cash + fund_available``;
``locked_cash = min(cash, notional)``;
``locked_fund = min(fund_available, notional - locked_cash)``. The split is
recomputed on every short (including additions to an existing position), and
the released portion on cover is proportional to the shares being covered.

**Immutability.** Every persisted aggregate is replaced via
:func:`dataclasses.replace`; no in-place mutation of stored references (matches
the fake-repo / SQLite parity invariant from the Phase 8 fakes digest).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from friendex.application.account_seed import seed_user_account
from friendex.application.interfaces import TradeCooldown
from friendex.application.lock_manager import guild_lock_key
from friendex.application.trade_results import (
    BuyResult,
    CoverResult,
    SellResult,
    ShortResult,
)
from friendex.application.unit_of_work import NullUnitOfWork
from friendex.domain.errors import (
    InsufficientFunds,
    InsufficientShares,
    InvalidAmount,
    MarketClosed,
    NoPosition,
    OnCooldown,
    OptedOut,
    PositionFrozen,
    SelfTrade,
)
from friendex.domain.market_hours import is_market_open, is_sunday
from friendex.domain.models import (
    HedgeFund,
    LongPosition,
    PricePoint,
    ShortPosition,
    Stock,
    UserAccount,
)
from friendex.domain.price_engine import apply_trade_impact, quantise

if TYPE_CHECKING:
    from friendex.adapters.config import Settings
    from friendex.application.interfaces import (
        IFundRepo,
        IPriceRepo,
        ITradeCooldownRepo,
        IUserRepo,
    )
    from friendex.application.lock_manager import LockManager
    from friendex.application.unit_of_work import IUnitOfWork

# Fraction of the shorter's hedge fund that counts toward collateral.
_FUND_COLLATERAL_FRACTION = Decimal("0.5")


class TradingService:
    """Buy / sell / short / cover use cases for the Friendex economy."""

    def __init__(
        self,
        *,
        guild_id: str,
        user_repo: IUserRepo,
        price_repo: IPriceRepo,
        fund_repo: IFundRepo,
        cooldown_repo: ITradeCooldownRepo,
        lock_manager: LockManager,
        settings: Settings,
        unit_of_work: IUnitOfWork | None = None,
    ) -> None:
        self._guild_id = guild_id
        self._user_repo = user_repo
        self._price_repo = price_repo
        self._fund_repo = fund_repo
        self._cooldown_repo = cooldown_repo
        self._locks = lock_manager
        self._settings = settings
        # ``unit_of_work`` is the atomicity envelope wrapping every multi-write
        # use case (short / cover / cover_internal). Defaulting to
        # ``NullUnitOfWork`` keeps the existing DI container wiring valid;
        # production wiring (and any caller that needs SQL-level atomicity)
        # passes a ``SqlUnitOfWork`` from ``adapters/persistence``.
        self._uow: IUnitOfWork = (
            unit_of_work if unit_of_work is not None else NullUnitOfWork()
        )

    # -- internal helpers ---------------------------------------------------

    def _lock_key(self, user_id: str) -> str:
        """Return the composite ``"<guild>:<user>"`` lock key (ADR-0001).

        Thin shim around :func:`guild_lock_key` (#82 H16).
        """
        return guild_lock_key(self._guild_id, user_id)

    def _check_market_open(self, *, allow_sunday: bool) -> None:
        """Raise :class:`MarketClosed` when trading is not permitted now.

        ``allow_sunday=True`` mirrors the original ``/buy`` Sunday exception:
        Sunday is treated as a normal trading day for the time-of-day window
        check. For ``/sell``, ``/short``, ``/cover`` the caller passes
        ``allow_sunday=False`` and Sunday raises outright.
        """
        now = datetime.now(tz=UTC)
        if not allow_sunday and is_sunday(now):
            raise MarketClosed(
                open_at=self._settings.market_open,
                close_at=self._settings.market_close,
            )
        if not is_market_open(
            now,
            self._settings.market_open,
            self._settings.market_close,
            sunday_buy_allowed=allow_sunday,
        ):
            raise MarketClosed(
                open_at=self._settings.market_open,
                close_at=self._settings.market_close,
            )

    async def _check_cooldown(self, user_id: str, now: datetime) -> None:
        """Raise :class:`OnCooldown` if ``user_id`` is on a short/cover cooldown.

        Delegates the active-vs-expired filter to the repository: the
        :meth:`ITradeCooldownRepo.get` Protocol takes a keyword-only ``now``
        and returns ``None`` for an expired (or missing) row, so the service
        only has to translate a non-``None`` return into the remaining-time
        arithmetic for the user-facing :class:`OnCooldown` payload.
        """
        cooldown = await self._cooldown_repo.get(self._guild_id, user_id, now=now)
        if cooldown is None:
            return
        remaining = (cooldown.expires_at - now).total_seconds()
        raise OnCooldown(seconds_remaining=int(remaining))

    async def _set_cooldown(self, user_id: str, now: datetime) -> None:
        """Persist the short/cover cooldown TTL row for ``user_id``."""
        seconds = self._settings.trade_cooldown_seconds
        expires = now + timedelta(seconds=seconds)
        await self._cooldown_repo.upsert(
            TradeCooldown(
                guild_id=self._guild_id,
                user_id=user_id,
                expires_at=expires,
            )
        )

    async def _get_or_create_user(self, user_id: str) -> UserAccount:
        """Return the stored account for ``user_id`` (creating defaults if absent).

        Mirrors the original ``ensure_user`` — a never-seen user starts with
        the configured initial cash, flat net worth, empty positions, fresh
        zeroed buckets, and ``opt_in=True``. Wraps
        :meth:`_resolve_user` and discards the ``created`` flag for callers
        that just need the account; the target side uses
        :meth:`_resolve_user` directly so a created stub can be persisted
        without a second redundant ``get`` call (issue #84 M).
        """
        account, _created = await self._resolve_user(user_id)
        return account

    async def _resolve_user(self, user_id: str) -> tuple[UserAccount, bool]:
        """Return ``(account, created)`` for ``user_id``.

        ``created`` is :data:`True` iff the account was not yet persisted and
        a fresh default stub was built. Pre-fix the trade methods called
        ``get(target)`` once via :meth:`_get_or_create_user` and again at the
        stub-persist site (``if get(target) is None: upsert``) — the per-target
        lock made the race benign but the second read was wasted work. The
        ``created`` flag lets the caller persist the stub once if needed and
        skip the redundant second ``get`` entirely (issue #84 M, silent-
        failures branch).

        The fresh-account shape is delegated to the shared
        :func:`friendex.application.account_seed.seed_user_account` (#82 H16).
        """
        existing = await self._user_repo.get(self._guild_id, user_id)
        if existing is not None:
            return existing, False
        stub = seed_user_account(user_id, self._settings)
        return stub, True

    async def _get_or_create_stock(self, target_id: str) -> Stock:
        """Return the stored stock for ``target_id`` (creating defaults if absent).

        Mirrors the original ``ensure_price`` — a never-seen stock starts at
        ``settings.initial_price`` with that same value as the 24h high/low
        and all-time high, and an empty history. The created stock is NOT
        persisted here — the caller upserts it as part of its critical
        section.
        """
        existing = await self._price_repo.get(self._guild_id, target_id)
        if existing is not None:
            return existing
        initial = quantise(Decimal(str(self._settings.initial_price)))
        return Stock(
            user_id=target_id,
            current=initial,
            history=[],
            high_24h=initial,
            low_24h=initial,
            all_time_high=initial,
        )

    async def _get_fund_cash(self, user_id: str) -> Decimal:
        """Return the user's personal hedge-fund cash balance, or zero if absent.

        Personal funds are keyed by ``fund_id == user_id`` (per the original
        ``funds_data[user_id]`` shape). A user who has never created a fund
        contributes nothing to short collateral.

        Persistence failures (e.g. the SQL fund repository raising) MUST
        propagate — silently returning ``Decimal("0")`` on error would let
        ``short`` and ``cover`` build a position against a phantom fund
        (#84 H, ghost-fund guard). The ``None`` short-circuit covers only
        the genuinely-absent case and never wraps a try / except.
        """
        fund = await self._fund_repo.get(self._guild_id, user_id)
        if fund is None:
            return Decimal("0.00")
        return fund.cash_balance

    async def _apply_price_impact_unlocked(
        self,
        target_id: str,
        stock: Stock,
        shares: int,
        *,
        is_buy: bool,
    ) -> tuple[Decimal, Decimal, Stock]:
        """Compute and persist the immediate trade-price impact for ``target_id``.

        Returns the ``(old_price, new_price, replaced_stock)`` triple. The
        caller MUST already hold the target's lock — this helper does NOT
        re-enter ``locked()`` (the trade methods take both actor + target
        locks at the top of their critical section; the lock is non-reentrant
        per Phase 7).

        Mirrors :meth:`PriceTickService._rmw_price`: on a real price change
        the helper appends a :class:`PricePoint` to history and ratchets
        ``all_time_high`` (never lowered). A no-op (``new_price == current``)
        skips both the upsert and the history append.
        """
        min_price = Decimal(str(self._settings.min_price))
        k = self._settings.price_impact_k
        old_price = stock.current
        new_price = apply_trade_impact(old_price, shares, is_buy, k, min_price)
        if new_price == old_price:
            return old_price, new_price, stock
        new_ath = max(stock.all_time_high, new_price)
        replaced = replace(stock, current=new_price, all_time_high=new_ath)
        await self._price_repo.upsert(self._guild_id, replaced)
        await self._price_repo.append_history(
            self._guild_id,
            target_id,
            PricePoint(price=new_price, timestamp=datetime.now(tz=UTC)),
        )
        return old_price, new_price, replaced

    @staticmethod
    def _validate_shares(shares: int) -> None:
        """Reject non-positive share counts at the public-method boundary."""
        if shares <= 0:
            raise InvalidAmount(reason="shares must be positive")

    @staticmethod
    def _check_not_self(actor_id: str, target_id: str) -> None:
        """Reject a trade whose actor and target are the same user."""
        if actor_id == target_id:
            raise SelfTrade()

    def _check_opt_in(self, target: UserAccount) -> None:
        """Reject a trade whose target has opted out of being tradable.

        When ``settings.opt_out_blocks_trading`` is False (Open-Q3 toggle) the
        check is a no-op — opt-out becomes advisory (the user is still hidden
        from opt-in-only listings, but trades against them succeed). See
        ``docs/02-target-architecture.md`` §Open-Questions Q3.
        """
        if not self._settings.opt_out_blocks_trading:
            return
        if not target.opt_in:
            raise OptedOut(target_id=target.user_id)

    # -- public use cases ---------------------------------------------------

    async def buy(
        self,
        buyer_id: str,
        target_id: str,
        shares: int,
    ) -> BuyResult:
        """Open or add to a long position on ``target_id``.

        Sunday is allowed (original spec exception); requires market open
        otherwise, sufficient cash, target opted in, and ``buyer != target``.
        Adding to an existing position recomputes the weighted-average entry
        as ``((old_shares * old_avg) + (shares * px)) / new_shares``.
        """
        self._validate_shares(shares)
        self._check_not_self(buyer_id, target_id)
        self._check_market_open(allow_sunday=self._settings.sunday_buy_allowed)

        async with (
            self._locks.locked(self._lock_key(buyer_id), self._lock_key(target_id)),
            self._uow.transaction(),
        ):
            # ``buy`` writes the buyer's cash + long-position dict, the
            # target stub on first sight, the stock row + history. The
            # UoW envelope rolls every write back if any one fails
            # mid-sequence, matching the short/cover discipline (#82 C2
            # follow-up — review M1).
            target, target_created = await self._resolve_user(target_id)
            self._check_opt_in(target)
            buyer = await self._get_or_create_user(buyer_id)
            stock = await self._get_or_create_stock(target_id)
            price = stock.current
            cost = quantise(price * Decimal(shares))
            if buyer.cash_balance < cost:
                raise InsufficientFunds(need=cost, have=buyer.cash_balance)

            existing = buyer.long_positions.get(target_id)
            if existing is None:
                position = LongPosition(
                    target_user_id=target_id, shares=shares, avg_entry=price
                )
            else:
                new_shares = existing.shares + shares
                new_avg = quantise(
                    (
                        existing.avg_entry * Decimal(existing.shares)
                        + price * Decimal(shares)
                    )
                    / Decimal(new_shares)
                )
                position = LongPosition(
                    target_user_id=target_id,
                    shares=new_shares,
                    avg_entry=new_avg,
                )

            new_cash = quantise(buyer.cash_balance - cost)
            new_longs = {**buyer.long_positions, target_id: position}
            updated_buyer = replace(
                buyer, cash_balance=new_cash, long_positions=new_longs
            )
            # Persist the target stub first if it did not exist before, so the
            # opt-in check above is sticky for the next call. Using the
            # ``_resolve_user`` ``created`` flag avoids the redundant second
            # ``get(target_id)`` call from the pre-fix code (issue #84 M).
            if target_created:
                await self._user_repo.upsert(self._guild_id, target)
            await self._user_repo.upsert(self._guild_id, updated_buyer)
            # Make sure the stock row exists for `_apply_price_impact_unlocked`
            # to upsert against (history append is keyed off the row).
            if await self._price_repo.get(self._guild_id, target_id) is None:
                await self._price_repo.upsert(self._guild_id, stock)
            old_price, new_price, _ = await self._apply_price_impact_unlocked(
                target_id, stock, shares, is_buy=True
            )

        return BuyResult(
            buyer_id=buyer_id,
            target_id=target_id,
            shares=shares,
            price_per_share=price,
            total_cost=cost,
            old_price=old_price,
            new_price=new_price,
            new_cash_balance=new_cash,
            position_after=position,
        )

    async def sell(
        self,
        seller_id: str,
        target_id: str,
        shares: int,
    ) -> SellResult:
        """Close (some or all of) a long position on ``target_id``.

        Requires market open (no Sunday exception), target opted in, seller
        holds at least ``shares`` of the target's stock, and the actor is not
        the target. Position is *deleted* when shares hit zero.
        """
        self._validate_shares(shares)
        self._check_not_self(seller_id, target_id)
        self._check_market_open(allow_sunday=False)

        async with (
            self._locks.locked(self._lock_key(seller_id), self._lock_key(target_id)),
            self._uow.transaction(),
        ):
            # ``sell`` writes the seller's cash + long-position dict, the
            # target stub on first sight, the stock row + history. The
            # UoW envelope rolls every write back if any one fails
            # mid-sequence (#82 C2 follow-up — review M1).
            target, target_created = await self._resolve_user(target_id)
            self._check_opt_in(target)
            seller = await self._get_or_create_user(seller_id)
            existing = seller.long_positions.get(target_id)
            if existing is None or existing.shares < shares:
                held = 0 if existing is None else existing.shares
                raise InsufficientShares(requested=shares, held=held)
            stock = await self._get_or_create_stock(target_id)
            price = stock.current
            revenue = quantise(price * Decimal(shares))

            remaining = existing.shares - shares
            new_longs = dict(seller.long_positions)
            position_after: LongPosition | None
            if remaining == 0:
                del new_longs[target_id]
                position_after = None
            else:
                position_after = LongPosition(
                    target_user_id=target_id,
                    shares=remaining,
                    avg_entry=existing.avg_entry,
                )
                new_longs[target_id] = position_after

            new_cash = quantise(seller.cash_balance + revenue)
            updated_seller = replace(
                seller, cash_balance=new_cash, long_positions=new_longs
            )
            # Issue #84 M: persist the target stub via the ``created`` flag
            # instead of a redundant second ``get(target_id)`` call.
            if target_created:
                await self._user_repo.upsert(self._guild_id, target)
            await self._user_repo.upsert(self._guild_id, updated_seller)
            if await self._price_repo.get(self._guild_id, target_id) is None:
                await self._price_repo.upsert(self._guild_id, stock)
            old_price, new_price, _ = await self._apply_price_impact_unlocked(
                target_id, stock, shares, is_buy=False
            )

        return SellResult(
            seller_id=seller_id,
            target_id=target_id,
            shares=shares,
            price_per_share=price,
            total_revenue=revenue,
            old_price=old_price,
            new_price=new_price,
            new_cash_balance=new_cash,
            position_after=position_after,
        )

    async def short(
        self,
        shorter_id: str,
        target_id: str,
        shares: int,
    ) -> ShortResult:
        """Open or add to a short position on ``target_id``.

        Requires market open (no Sunday exception), target opted in, the
        shorter is not the target, and that the actor is not currently on the
        short/cover cooldown. Collateral splits across cash + 50% of the
        shorter's personal hedge fund per the original spec. Adding to a
        frozen short is rejected with :class:`PositionFrozen`.
        """
        self._validate_shares(shares)
        self._check_not_self(shorter_id, target_id)
        self._check_market_open(allow_sunday=False)
        # Cheap pre-lock probe so a known-cooled user fails fast without
        # contending on the lock; the authoritative check happens inside
        # the critical section against the in-lock ``now`` (see M12).
        await self._check_cooldown(shorter_id, datetime.now(tz=UTC))

        async with self._locks.locked(
            self._lock_key(shorter_id), self._lock_key(target_id)
        ):
            # Sample ``now`` AFTER lock acquisition so the cooldown row's
            # ``expires_at`` is anchored to the write time, not the
            # pre-lock probe time — preserves the cooldown TTL even
            # when callers queue up behind a slow lock (#82 M12).
            now = datetime.now(tz=UTC)
            async with self._uow.transaction():
                # Re-check the cooldown INSIDE the UoW transaction so
                # the read participates in the same session as the
                # cooldown row's write below (#82 C1 + C2 / review H4):
                # the in-lock recheck observes a sibling write that has
                # been flushed but not yet committed, and a failure
                # between recheck and write is rolled back atomically.
                await self._check_cooldown(shorter_id, now)
                target, target_created = await self._resolve_user(target_id)
                self._check_opt_in(target)
                shorter = await self._get_or_create_user(shorter_id)
                stock = await self._get_or_create_stock(target_id)
                price = stock.current
                notional = quantise(price * Decimal(shares))

                cash_available = shorter.cash_balance
                fund_cash = await self._get_fund_cash(shorter_id)
                fund_available = quantise(fund_cash * _FUND_COLLATERAL_FRACTION)
                total_collateral = cash_available + fund_available
                if total_collateral < notional:
                    raise InsufficientFunds(need=notional, have=total_collateral)

                locked_cash = min(cash_available, notional)
                locked_fund = min(fund_available, notional - locked_cash)
                locked_cash = quantise(locked_cash)
                locked_fund = quantise(locked_fund)

                existing = shorter.short_positions.get(target_id)
                if existing is not None:
                    if existing.frozen:
                        raise PositionFrozen(target_id=target_id)
                    new_shares = existing.shares + shares
                    new_entry = quantise(
                        (
                            existing.entry_price * Decimal(existing.shares)
                            + price * Decimal(shares)
                        )
                        / Decimal(new_shares)
                    )
                    position = ShortPosition(
                        target_user_id=target_id,
                        shares=new_shares,
                        entry_price=new_entry,
                        locked_cash=quantise(existing.locked_cash + locked_cash),
                        locked_fund=quantise(existing.locked_fund + locked_fund),
                        created_at=existing.created_at,
                        frozen=False,
                    )
                else:
                    position = ShortPosition(
                        target_user_id=target_id,
                        shares=shares,
                        entry_price=price,
                        locked_cash=locked_cash,
                        locked_fund=locked_fund,
                        created_at=now,
                        frozen=False,
                    )

                new_cash = quantise(cash_available - locked_cash)
                new_fund_cash = quantise(fund_cash - locked_fund)
                new_shorts = {**shorter.short_positions, target_id: position}
                updated_shorter = replace(
                    shorter, cash_balance=new_cash, short_positions=new_shorts
                )
                # Issue #84 M: persist the target stub via the ``created``
                # flag instead of a redundant second ``get(target_id)`` call.
                if target_created:
                    await self._user_repo.upsert(self._guild_id, target)
                await self._user_repo.upsert(self._guild_id, updated_shorter)
                await self._write_fund_cash(shorter_id, new_fund_cash)
                if await self._price_repo.get(self._guild_id, target_id) is None:
                    await self._price_repo.upsert(self._guild_id, stock)
                old_price, new_price, _ = await self._apply_price_impact_unlocked(
                    target_id, stock, shares, is_buy=False
                )
                # Cooldown write is inside the lock + the same UoW as the
                # money writes, so two racing shorts cannot both bypass the
                # pre-lock probe and a mid-sequence failure rolls the
                # cooldown row back along with the rest (#82 C1 + C2).
                await self._set_cooldown(shorter_id, now)

        return ShortResult(
            shorter_id=shorter_id,
            target_id=target_id,
            shares=shares,
            price_per_share=price,
            notional=notional,
            locked_cash=locked_cash,
            locked_fund=locked_fund,
            old_price=old_price,
            new_price=new_price,
            new_cash_balance=new_cash,
            new_fund_balance=new_fund_cash,
            position_after=position,
        )

    async def cover(
        self,
        coverer_id: str,
        target_id: str,
        shares: int,
    ) -> CoverResult:
        """Close (some or all of) a short position on ``target_id``.

        Requires market open (no Sunday exception), the coverer holds at
        least ``shares`` of an unfrozen short on the target, sufficient
        cash to pay the cover cost, the actor is not the target, and that
        the actor is not currently on the short/cover cooldown. Released
        collateral is proportional to the shares being covered; positive
        P&L is credited to cash on top of the released cash. The short
        position is *deleted* when shares hit zero.

        **Opt-out asymmetry vs. ``buy``/``sell``/``short`` (issue #84 M).**
        Cover deliberately does NOT require the target to be opted in.
        A short can outlive the target's consent (the target may have
        opted out after the short was opened); blocking cover would trap
        the holder with no exit and leave their locked collateral
        stranded indefinitely. The other three open-position directions
        still enforce the opt-in gate at the public-method boundary.
        See :meth:`_cover_internal` for the implementation note.

        The public method always rejects a frozen position with
        :class:`PositionFrozen`. The Phase 8f
        :class:`~friendex.application.liquidation_service.LiquidationService`
        bypasses the freeze guard by calling :meth:`_cover_internal`
        directly with ``force=True``; the ``force`` flag is deliberately
        NOT surfaced on this public method so user-facing trade commands
        cannot side-step the freeze window.
        """
        self._validate_shares(shares)
        self._check_not_self(coverer_id, target_id)
        self._check_market_open(allow_sunday=False)
        # Cheap pre-lock probe so a known-cooled user fails fast without
        # contending on the lock; the authoritative check happens inside
        # the critical section against the in-lock ``now`` (see M12).
        await self._check_cooldown(coverer_id, datetime.now(tz=UTC))

        async with self._locks.locked(
            self._lock_key(coverer_id), self._lock_key(target_id)
        ):
            # Sample ``now`` AFTER lock acquisition so the cooldown row's
            # ``expires_at`` is anchored to the write time, not the
            # pre-lock probe time (#82 M12).
            now = datetime.now(tz=UTC)
            async with self._uow.transaction():
                # Re-check the cooldown INSIDE the UoW transaction so
                # the read participates in the same session as the
                # cooldown write below (#82 C1 + C2 / review H4): a
                # failure between recheck and write is rolled back
                # atomically with every money write in `_cover_internal`.
                await self._check_cooldown(coverer_id, now)
                result = await self._cover_internal(
                    coverer_id, target_id, shares, force=False
                )
                # Cooldown write joins the same UoW as the money writes so
                # a mid-sequence failure rolls them all back together
                # (#82 C1 + C2).
                await self._set_cooldown(coverer_id, now)
        return result

    async def cover_forced(
        self,
        coverer_id: str,
        target_id: str,
        shares: int,
    ) -> CoverResult:
        """Force-cover a short position; bypasses the freeze guard.

        Public adapter over the inside-lock body shared with the user-facing
        :meth:`cover`. **Locking + UoW contract:** the caller MUST already
        hold ``self._locks.locked(coverer_id, target_id)`` and MUST envelope
        the call in any required UoW transaction —
        :class:`LiquidationService` does both at the call site (the lock
        wraps the in-lock re-read; the UoW gap is tracked in issue #95 per
        the comment on the body).

        Pre-#82 M1 the liquidation sweep reached directly into
        :meth:`_cover_internal` (a private helper), making the cross-service
        dependency opaque to ``mypy`` / ``ruff`` and tying liquidation to
        an implementation detail of the trading service. Exposing this
        thin public wrapper makes the dependency explicit and lets the
        force-cover surface evolve independently of the private body.

        ``force=True`` is hard-coded here; user-facing ``/cover`` cannot
        reach this method by name (the cog wires :meth:`cover` instead).
        """
        return await self._cover_internal(coverer_id, target_id, shares, force=True)

    async def _cover_internal(
        self,
        coverer_id: str,
        target_id: str,
        shares: int,
        *,
        force: bool,
    ) -> CoverResult:
        """Inside-lock body of the cover use case; caller MUST hold the locks.

        Encapsulates the post-lock RMW shared by :meth:`cover` and
        :class:`LiquidationService`. **Locking contract:** this helper does
        NOT acquire ``locked()`` — the caller is responsible for holding
        ``self._locks.locked(coverer_id, target_id)`` for the entire
        invocation. The :class:`~friendex.application.lock_manager.LockManager`
        is non-reentrant per the Phase 7 digest, so calling this helper from
        inside an outer ``locked()`` (as :class:`LiquidationService` does)
        would deadlock if the helper tried to re-enter the lock.

        ``force=True`` skips the :class:`PositionFrozen` guard; the public
        :meth:`cover` always passes ``force=False`` so user-facing
        ``/cover`` cannot bypass the freeze window. ``force=True`` is
        reserved for liquidation, where the freeze is irrelevant (the
        position is being auto-covered against the holder's will).

        The cooldown set/clear is NOT done here — :meth:`cover` sets the
        cooldown after the lock release on success; liquidation does not
        set a cooldown at all (a force-cover is a system action, not a
        user-initiated short/cover).

        **Opt-out exemption (issue #84 M).** Cover deliberately does NOT
        call :meth:`_check_opt_in` on the target. A short can outlive the
        target's consent: the target may have opted out *after* the short
        was opened. Blocking cover would trap the holder with no exit and
        leave their locked collateral stranded indefinitely. Open-position
        directions (``buy``/``sell``/``short``) still enforce the opt-in
        gate at the public-method boundary.

        **UoW envelope responsibility (PR #94 review L2 — pre-existing
        gap, tracked in issue #95).** ``cover()`` wraps its call to
        this helper in ``async with self._uow.transaction()`` so the
        writes (user upsert, fund cash, price upsert, cooldown set)
        commit atomically. :class:`LiquidationService` does NOT — it
        relies on the underlying persistence adapter's autocommit
        behaviour, so a mid-sequence failure inside this helper invoked
        from liquidation produces a narrow inconsistency window where
        the target stub (persisted via ``target_created``) could land
        without the accompanying money writes. The gap is intentionally
        narrow — the target stub is a fresh zero-state row that any
        subsequent trade would resurrect, and no money is misstated —
        but it is real, and wrapping the liquidation call site in a UoW
        envelope is the canonical fix. Crosses Wave 1 #88's territory
        (atomicity of money flows), so it is deferred to issue #95
        rather than bundled into this Wave 2 silent-failures PR.
        """
        target, target_created = await self._resolve_user(target_id)
        coverer = await self._get_or_create_user(coverer_id)
        existing = coverer.short_positions.get(target_id)
        if existing is None:
            raise NoPosition(target_id=target_id, position_type="short")
        if existing.shares < shares:
            raise InsufficientShares(requested=shares, held=existing.shares)
        if existing.frozen and not force:
            raise PositionFrozen(target_id=target_id)
        stock = await self._get_or_create_stock(target_id)
        price = stock.current
        cost = quantise(price * Decimal(shares))
        if coverer.cash_balance < cost:
            raise InsufficientFunds(need=cost, have=coverer.cash_balance)

        remaining = existing.shares - shares
        pnl = quantise((existing.entry_price - price) * Decimal(shares))

        new_shorts = dict(coverer.short_positions)
        position_after: ShortPosition | None
        if remaining == 0:
            # Full cover: release the position's exact locked values rather
            # than recomputing them proportionally. Quantising
            # ``locked * 1.0`` is exact for an unchanged ``Decimal`` but
            # quantising ``locked * (n / n)`` can drift if ``n / n`` is
            # represented inexactly during intermediate arithmetic; pinning
            # the exact values keeps the collateral invariant
            # ``locked_cash + locked_fund == shares * entry_price`` (#82 H2).
            released_cash = existing.locked_cash
            released_fund = existing.locked_fund
            del new_shorts[target_id]
            position_after = None
        else:
            proportion = Decimal(shares) / Decimal(existing.shares)
            released_cash = quantise(existing.locked_cash * proportion)
            released_fund = quantise(existing.locked_fund * proportion)
            position_after = ShortPosition(
                target_user_id=target_id,
                shares=remaining,
                entry_price=existing.entry_price,
                locked_cash=quantise(existing.locked_cash - released_cash),
                locked_fund=quantise(existing.locked_fund - released_fund),
                created_at=existing.created_at,
                frozen=existing.frozen,
            )
            new_shorts[target_id] = position_after

        cash_after_pay = quantise(coverer.cash_balance - cost + released_cash)
        if pnl > 0:
            cash_after_pay = quantise(cash_after_pay + pnl)

        fund_cash = await self._get_fund_cash(coverer_id)
        new_fund_cash = quantise(fund_cash + released_fund)
        updated_coverer = replace(
            coverer, cash_balance=cash_after_pay, short_positions=new_shorts
        )
        # Issue #84 M: persist the target stub via the ``created`` flag
        # instead of a redundant second ``get(target_id)`` call.
        if target_created:
            await self._user_repo.upsert(self._guild_id, target)
        await self._user_repo.upsert(self._guild_id, updated_coverer)
        await self._write_fund_cash(coverer_id, new_fund_cash)
        if await self._price_repo.get(self._guild_id, target_id) is None:
            await self._price_repo.upsert(self._guild_id, stock)
        old_price, new_price, _ = await self._apply_price_impact_unlocked(
            target_id, stock, shares, is_buy=False
        )

        return CoverResult(
            coverer_id=coverer_id,
            target_id=target_id,
            shares=shares,
            price_per_share=price,
            cost=cost,
            pnl=pnl,
            released_cash=released_cash,
            released_fund=released_fund,
            old_price=old_price,
            new_price=new_price,
            new_cash_balance=cash_after_pay,
            new_fund_balance=new_fund_cash,
            position_after=position_after,
        )

    async def update_frozen_shorts(self) -> None:
        """Freeze every short position older than ``short_freeze_minutes``.

        Mirrors the original ``short_freeze_check`` task (5-min loop): walk
        every account in the guild and, for any non-frozen short whose
        ``created_at`` is at least ``settings.short_freeze_minutes`` in the
        past, replace it with a copy that has ``frozen=True``. The sweep takes
        the lock per-account (one user at a time, like the tick services) so
        unrelated accounts never serialise.
        """
        threshold = timedelta(minutes=self._settings.short_freeze_minutes)
        now = datetime.now(tz=UTC)
        for account in await self._user_repo.list_all(self._guild_id):
            async with self._locks.locked(self._lock_key(account.user_id)):
                fresh = await self._user_repo.get(self._guild_id, account.user_id)
                if fresh is None:
                    continue
                new_shorts: dict[str, ShortPosition] = {}
                changed = False
                for tid, position in fresh.short_positions.items():
                    age = now - position.created_at
                    if position.frozen or age < threshold:
                        new_shorts[tid] = position
                        continue
                    new_shorts[tid] = replace(position, frozen=True)
                    changed = True
                if changed:
                    await self._user_repo.upsert(
                        self._guild_id, replace(fresh, short_positions=new_shorts)
                    )

    # -- fund-cash write (small enough to inline) ---------------------------

    async def _write_fund_cash(self, user_id: str, new_cash: Decimal) -> None:
        """Replace the user's personal hedge-fund cash balance.

        Creates the fund row if it does not yet exist (a user shorting
        without an explicit ``/fund create`` is supported; the personal fund
        is keyed by ``fund_id == user_id``). No-ops when ``new_cash`` would
        equal the existing balance and the row already exists.

        **Asymmetry with :meth:`_get_fund_cash` (review M5).**
        :meth:`_get_fund_cash` returns ``Decimal("0")`` when the fund is
        legitimately absent and propagates persistence failures; this
        method auto-creates the fund on first short. The asymmetry is
        intentional: the read path participates in the collateral
        calculation (treat missing fund as zero collateral), the write
        path participates in the trade-completion side-effect (create
        the row so subsequent reads can find the locked balance). If
        ``_fund_repo.get`` raises here, the exception propagates the
        same way as in ``_get_fund_cash`` — the UoW envelope on
        ``short`` / ``_cover_internal`` rolls every prior write back.
        """
        fund = await self._fund_repo.get(self._guild_id, user_id)
        if fund is None:
            if new_cash == 0:
                return
            fund = HedgeFund(
                fund_id=user_id,
                name=user_id,
                manager_id=user_id,
                cash_balance=new_cash,
                investors={},
            )
            await self._fund_repo.upsert(self._guild_id, fund)
            return
        if fund.cash_balance == new_cash:
            return
        await self._fund_repo.upsert(
            self._guild_id, replace(fund, cash_balance=new_cash)
        )
