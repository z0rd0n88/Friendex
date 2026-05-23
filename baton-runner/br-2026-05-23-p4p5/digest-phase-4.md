# Phase 4 digest — Domain pure functions (CLEAN)

Public surface added under `src/friendex/domain/` (all pure: no I/O, no clock,
no globals, no input mutation). Reviewed independently; gate green
(pytest/ruff/ruff-format/mypy), mutation-verified, no new dependencies.

## Public surface

`price_engine.py`
- `apply_trade_impact(current: Decimal, shares: int, is_buy: bool, k: float, min_price: Decimal) -> Decimal`
- `apply_floor_stall(current: Decimal, proposed: Decimal, min_price: Decimal) -> Decimal`
- `compute_activity_return(bucket: ActivityBucket, k: float) -> Decimal`
- `apply_inactivity_decay(current: Decimal, decay: float, min_price: Decimal) -> Decimal`

`activity.py`
- `calculate_trending_score(bucket: ActivityBucket) -> float`
- `get_engagement_tier(score: float, all_scores: list[float]) -> str`
- `reset_activity_bucket(bucket: ActivityBucket, now: datetime) -> ActivityBucket`

`market_hours.py`
- `is_trading_day(dt: datetime) -> bool`
- `is_sunday(dt: datetime) -> bool`
- `is_market_open(dt: datetime, market_open: time, market_close: time, sunday_buy_allowed: bool = False) -> bool`

`fund_math.py`
- `compute_apy_accrual(balance: Decimal, apy: float, period: Literal["monthly","annual"]) -> Decimal`
- `compute_effective_apy(base_apy: float, penalty: FundPenalty | None, now: datetime) -> float`
- `compute_net_worth(account: UserAccount, prices: dict[str, Stock], fund: HedgeFund | None) -> Decimal`

## Conventions later phases MUST honor

- **Decimal-at-boundary:** money/price params + returns are `Decimal` quantised
  to `0.01` (`ROUND_HALF_EVEN`); rate/factor tunables (`k`, `decay`, `apy`) stay
  `float`. `ln` computed in float (`math.log1p`) then converted back to Decimal
  via `str()`. Float→Decimal always goes through `str()` to dodge IEEE-754 noise.
  Activity *scores* are plain `float` (dimensionless, not money).
- **Purity / immutability:** domain takes tunables (`min_price`, `market_open`/
  `close`, `k`, `decay`, APYs) as arguments — sourced from `Settings` at the call
  site, never read here. Inputs are never mutated (`reset_activity_bucket` returns
  a fresh `dataclasses.replace`d bucket with a non-aliased channel list).
- **Net-worth contract:** short contribution = `locked_cash + locked_fund -
  shares*current_price` (collateral minus buy-back). This is an equivalent
  decomposition of the original spec's `shares*entry_price - shares*current_price`
  (spec:320-338) and matches **only while** the invariant
  `locked_cash + locked_fund == shares*entry_price` holds. The Phase-7/8 short
  service MUST preserve that invariant (lock notional at open; release
  proportionally on cover) or net worth will diverge from the original.
- **Market window:** Mon–Sat trading (Sun closed); daily window is
  `[open, close)` and wraps past midnight when `open >= close` (06:30→04:30).
  `sunday_buy_allowed` only un-blocks Sunday for the time window — it does not
  force-open regardless of hour.
- **Activity weighting source:** weights, soft-caps, and tier percentile cuts are
  lifted verbatim from `docs/spec/original-skeleton.md` §ENGAGEMENT/TRENDING and
  live as module constants in `activity.py` (a future pass may promote them — and
  the `price_engine` floor-stall constants — to `Settings`). Trending-score
  age-decay is deliberately omitted; `compute_activity_return` uses
  `k·ln(1+score)` per the task spec, not the original's `log10` form.

## Non-blocking follow-ups (from review baton 003)

- Correct `fund_math` docstring + batons: spec DOES define `calculate_net_worth`
  (spec:320) — the "undefined" claim is wrong (math is still correct).
- Strengthen `apply_floor_stall` test: add an exact-value assertion away from the
  floor to pin the attenuation magnitude (`_ATTENUATION_DISTANCE = 10.0`).
