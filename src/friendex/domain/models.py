"""Domain dataclass models for Friendex.

Each model is a plain :class:`dataclass` with construction-time invariant
checks via ``__post_init__``. Invariants ``raise ValueError`` rather than
using ``assert`` so they remain enforced when the interpreter runs with
``-O`` (which strips ``assert`` statements).

Signatures are derived from ``docs/02-target-architecture.md`` §Domain Model.

**Numeric typing — quantisation policy (Phase 3.1 migration):**

Monetary and price fields use :class:`decimal.Decimal` to avoid IEEE-754
accounting drift in trade math. The recommended quantisation at the service
boundary is:

* **Currency** (cash, prices, locked collateral): ``Decimal('0.01')`` — two
  decimal places, banker's rounding (``ROUND_HALF_EVEN``) is fine.
* **Rates** (``penalty_apr``): ``Decimal('0.0001')`` — four decimal places.

Construction-time invariants do **not** auto-quantise; callers are
responsible for passing already-quantised values. ``Decimal`` supports
``:,.2f`` formatting natively, so user-facing message templates do not
need to change.

``voice_minutes``, ``role_ping_joins``, and ``role_ping_join_minutes``
deliberately remain ``float`` — these are duration/count measurements,
not money.

Datetime defaults use ``datetime.now(tz=UTC)`` (timezone-aware). The
deprecated ``datetime.utcnow`` is avoided so naive/aware datetimes do
not leak through the persistence boundary in Phase 4.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal


@dataclass
class ActivityBucket:
    text_msgs: int = 0
    media_msgs: int = 0
    voice_minutes: float = 0.0
    voice_unique_channels: list[str] = field(default_factory=list)
    reaction_count: int = 0
    reply_count: int = 0
    role_ping_joins: float = 0.0
    role_ping_join_minutes: float = 0.0
    bucket_start: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def __post_init__(self) -> None:
        self.voice_unique_channels = [str(c) for c in self.voice_unique_channels]


@dataclass
class DailyProgress:
    last_claim: datetime | None
    streak: int

    def __post_init__(self) -> None:
        if self.streak < 0:
            raise ValueError("streak must be non-negative")


@dataclass
class LongPosition:
    target_user_id: str
    shares: int
    avg_entry: Decimal

    def __post_init__(self) -> None:
        if self.shares <= 0:
            raise ValueError("shares must be positive")
        if self.avg_entry <= 0:
            raise ValueError("avg_entry must be positive")


@dataclass
class ShortPosition:
    target_user_id: str
    shares: int
    entry_price: Decimal
    locked_cash: Decimal
    locked_fund: Decimal
    created_at: datetime
    frozen: bool = False

    def __post_init__(self) -> None:
        if self.shares <= 0:
            raise ValueError("shares must be positive")
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if self.locked_cash < 0 or self.locked_fund < 0:
            raise ValueError("locked collateral must be non-negative")


@dataclass
class UserAccount:
    """Per-guild user account: cash, positions, activity buckets, and daily streak.

    **Invariants.** ``cash_balance`` MUST be non-negative — a negative cash
    balance is always a bug (the trading service holds the actor's cash at the
    open of every short and refunds at cover, so the ledger never sinks
    below zero for legitimate game state).

    ``net_worth`` and ``month_start_net_worth`` are **measurements**, not
    balances: ``net_worth = cash_balance + sum(long_value) + sum(short_pnl)
    + fund_stake`` per :func:`friendex.domain.fund_math.compute_net_worth`.
    The short-position term is ``shares * (entry_price - current_price)``
    — legitimately negative when the position is underwater, since
    ``current_price`` has no ceiling. The 1.5x liquidation threshold caps
    the exposure window, but liquidation runs on a periodic sweep
    (:class:`~friendex.adapters.tasks.liquidation_task.LiquidationTask`),
    so a sharp price tick between sweeps can drive ``net_worth`` deeply
    negative until the next liquidation closes the position. **That is
    real game state — measuring a deeply-underwater holder as bankrupt is
    the correct behaviour for the leaderboard signal, not a constraint
    violation.** The previous strict ``>= 0`` invariant crashed
    :meth:`PortfolioService.capture_month_start_net_worth` mid-rollover
    when a holder happened to be underwater at the month boundary;
    relaxing the invariant lets the measurement round-trip through
    ``replace()`` cleanly while still rejecting impossible cash balances
    at the boundary.

    The PR #94 review (M2) flagged this — see the review body for the
    full derivation. Off-by-one bugs in net-worth math now surface via
    the structured ``leaderboard_ghost`` / ``stay_boost_no_stock`` log
    family instead of via a raise that the operator only sees once
    monthly.
    """

    user_id: str
    cash_balance: Decimal
    net_worth: Decimal
    month_start_net_worth: Decimal
    long_positions: dict[str, LongPosition]
    short_positions: dict[str, ShortPosition]
    today: ActivityBucket
    week: ActivityBucket
    daily: DailyProgress
    last_activity: datetime
    opt_in: bool = True
    intro_shown: bool = False

    def __post_init__(self) -> None:
        if self.cash_balance < 0:
            raise ValueError("cash_balance must be non-negative")
        # ``net_worth`` and ``month_start_net_worth`` are measurements, not
        # balances — they CAN go negative when a holder's shorts are
        # underwater between liquidation ticks. Cash, by contrast, never
        # legitimately goes negative; that invariant stays strict. See the
        # class docstring for the full rationale (PR #94 review M2).


@dataclass
class PricePoint:
    price: Decimal
    timestamp: datetime


@dataclass
class Stock:
    user_id: str
    current: Decimal
    history: list[PricePoint]
    high_24h: Decimal
    low_24h: Decimal
    all_time_high: Decimal

    def __post_init__(self) -> None:
        if self.current < 0:
            raise ValueError("price must be non-negative")


@dataclass
class HedgeFund:
    fund_id: str
    name: str
    manager_id: str
    cash_balance: Decimal
    investors: dict[str, Decimal]

    def __post_init__(self) -> None:
        if self.cash_balance < 0:
            raise ValueError("fund cash must be non-negative")


@dataclass
class FundPenalty:
    user_id: str
    penalty_apr: Decimal
    penalty_until: datetime


@dataclass
class VoiceSession:
    user_id: str
    channel_id: int
    start: datetime
    from_ping_message_ids: set[int]


@dataclass
class VoicePingSession:
    message_id: int
    host_id: str
    channel_id: int
    timestamp: datetime
    first_10_joiners: list[str]
    extra_joiners: list[str]


@dataclass
class VcExtraBoost:
    user_id: str
    ping_time: datetime
    last_boost: datetime
    end_time: datetime
