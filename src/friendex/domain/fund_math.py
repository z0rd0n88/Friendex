"""Pure hedge-fund and net-worth math for Friendex.

Every function here is a pure function of its arguments — no globals, no I/O, no
mutation of inputs. They mirror the fund/penalty logic of the original monolith
(see ``docs/spec/original-skeleton.md`` §HEDGE FUND PENALTY & EVENTS and the
``balance`` command's net-worth roll-up).

**Numeric typing (Phase 3.1 invariant — Decimal at the boundary):**

* Money parameters and return values (balances, accruals, net worth) are
  :class:`~decimal.Decimal`, quantised to currency precision (:data:`CENT` =
  ``Decimal('0.01')``) with banker's rounding (``ROUND_HALF_EVEN``).
* Rate values (APYs) stay ``float`` to match ``Settings.hedge_fund_base_apy``.
  A model's ``Decimal`` ``penalty_apr`` is converted to ``float`` only at the
  point it is combined with the float base rate.

**Net-worth valuation convention** (``compute_net_worth``):

``net_worth = cash``
``  + sum over longs  (shares * current_price)``
``  + sum over shorts (locked_cash + locked_fund - shares * current_price)``
``  + the account's own hedge-fund stake``

A short's contribution is the collateral that was locked when it opened (and is
released on cover) minus the cost to buy the shares back at the current price,
i.e. collateral plus unrealised short PnL. The current price for a position is
read from ``prices[target_user_id].current``.

This is an equivalent collateral-based decomposition of the original spec's
short term ``entry_value - current_value`` (``shares * entry_price -
shares * current_price``; the spec *does* define this — see
``docs/spec/original-skeleton.md`` line 320, ``calculate_net_worth``). The two
forms coincide exactly **only while** the invariant
``locked_cash + locked_fund == shares * entry_price`` holds — collateral locked
at open equals the short's notional, and partial covers release it
proportionally. The Phase-7/8 short service MUST preserve this invariant, or
``compute_net_worth`` will diverge from the spec's valuation.

**Per-position vs. global drift on partial covers.** The trading service's
partial-cover path quantises ``released_cash`` / ``released_fund`` per cover,
so the **per-position** invariant ``locked_cash + locked_fund == shares *
entry_price`` may drift by up to one cent per partial cover when the entry
price does not divide evenly into the locked-cash / locked-fund split. The
**global** invariant ``Σ released_cash + Σ released_fund + final remaining ==
initial notional`` is preserved exactly across the full close — the full-cover
path releases the position's exact stored locked values rather than
recomputing them proportionally, so the totals reconcile bit-for-bit.
``compute_net_worth`` reads ``locked_cash + locked_fund`` directly and
inherits the per-position drift mid-sequence; consumers should treat
mid-cover net-worth displays as accurate to within ``cents-per-partial-cover``
of the spec's valuation, not bit-exact. The 2026-05-29 review's H2 case
(``locked_cash=50.07``, ``locked_fund=49.93``, ``shares=7``, ``entry_price
= 100/7``) demonstrates a 1¢ per-position drift that fully reconciles on
close.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Literal

from friendex.domain.price_engine import CENT, quantise

if TYPE_CHECKING:
    from datetime import datetime

    from friendex.domain.models import (
        FundPenalty,
        HedgeFund,
        Stock,
        UserAccount,
    )

# Re-export the canonical money-quantisation seam (#82 H16) for callers that
# previously imported ``CENT`` from this module.
__all__ = [
    "CENT",
    "compute_apy_accrual",
    "compute_apy_accrual_raw",
    "compute_effective_apy",
    "compute_net_worth",
]

# Months per year — monthly accrual is the annual rate spread evenly.
_MONTHS_PER_YEAR = Decimal("12")

# Domestic alias so the module body's existing call sites read naturally;
# the public-facing helper lives in :mod:`friendex.domain.price_engine`.
_quantise = quantise


def compute_apy_accrual(
    balance: Decimal,
    apy: float,
    period: Literal["monthly", "annual"],
) -> Decimal:
    """Return the interest accrued on ``balance`` at ``apy`` over ``period``.

    * ``"annual"`` accrues ``balance * apy``.
    * ``"monthly"`` accrues ``balance * apy / 12``.

    The float ``apy`` is converted to ``Decimal`` via its string form to avoid
    IEEE-754 noise, the product is quantised to cents, and ``balance`` is never
    mutated.
    """
    return _quantise(compute_apy_accrual_raw(balance, apy, period))


def compute_apy_accrual_raw(
    balance: Decimal,
    apy: float,
    period: Literal["monthly", "annual"],
) -> Decimal:
    """Return the unquantised accrual; used when a per-stake sum is taken.

    Application code that accumulates many small per-stake accruals quantises
    *the sum*, not each individual term — quantising each term first would
    round every sub-cent accrual down to zero and silently destroy money over
    many investors (#82 H3). This helper exposes the raw multiplication so
    callers can defer quantisation to the end.

    For a single-shot accrual, prefer :func:`compute_apy_accrual` — it
    quantises immediately and is the right tool everywhere there is no sum.

    **Period dispatch (#84 L).** Uses ``match`` rather than ``if/else`` so a
    static type-checker can verify exhaustiveness against the ``Literal``
    type — adding a new period (e.g. ``"weekly"``) without extending the
    match block would surface as a static error rather than silently falling
    through to the monthly branch.
    """
    rate = Decimal(str(apy))
    annual = balance * rate
    match period:
        case "annual":
            return annual
        case "monthly":
            return annual / _MONTHS_PER_YEAR


def compute_effective_apy(
    base_apy: float,
    penalty: FundPenalty | None,
    now: datetime,
) -> float:
    """Return the effective APY after applying any active penalty.

    With no penalty, or a penalty whose ``penalty_until`` is at/before ``now``
    (expired), the ``base_apy`` is returned unchanged. An active penalty
    subtracts ``float(penalty.penalty_apr)`` from ``base_apy``, floored at
    ``0.0`` so the rate never goes negative. The return value is a ``float``
    rate to match ``Settings.hedge_fund_base_apy``.
    """
    if penalty is None or penalty.penalty_until <= now:
        return base_apy
    return max(0.0, base_apy - float(penalty.penalty_apr))


def compute_net_worth(
    account: UserAccount,
    prices: dict[str, Stock],
    fund: HedgeFund | None,
) -> Decimal:
    """Return ``account``'s total net worth as a quantised ``Decimal``.

    Sums cash, long positions valued at their current price, short positions
    (locked collateral minus current buy-back cost), and the account's own
    hedge-fund stake when ``fund`` is supplied and the account appears in its
    investor map. The current price for a position is read from
    ``prices[target_user_id].current``; a position with no matching ``Stock`` in
    ``prices`` contributes nothing for its price-valued component. Inputs are
    never mutated.
    """
    total = account.cash_balance

    for long in account.long_positions.values():
        stock = prices.get(long.target_user_id)
        if stock is not None:
            total += Decimal(long.shares) * stock.current

    for short in account.short_positions.values():
        collateral = short.locked_cash + short.locked_fund
        stock = prices.get(short.target_user_id)
        buyback = (
            Decimal(short.shares) * stock.current if stock is not None else Decimal(0)
        )
        total += collateral - buyback

    if fund is not None:
        total += fund.investors.get(account.user_id, Decimal(0))

    return _quantise(total)
