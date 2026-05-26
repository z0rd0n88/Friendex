# Phase 8b exit digest — `PriceTickService`

Source: `feat/phase-8b-price-tick` at HEAD `5888801` (post-iter-1 fixes).
Phase 4 + 8a + 8-fakes digests still authoritative for the layers below.

## Public surface

```python
class PriceTickService:
    def __init__(self, *, guild_id: str, user_repo: IUserRepo, price_repo: IPriceRepo,
                 lock_manager: LockManager, settings: Settings,
                 voice_sessions: VoiceSessionStore) -> None
    async def activity_price_tick(self) -> None
    async def inactivity_decay_tick(self) -> None
    async def vc_boost_tick(self, *, extra_boosts: Iterable[VcExtraBoost],
                            now: datetime) -> list[VcExtraBoost]
```

Per Phase 3a correction 4 the `reset_24h_high_low()` method is omitted —
`high_24h`/`low_24h` are computed dynamically from price history, not stored.

## New `Settings` defaults (additions to `src/friendex/adapters/config.py`)

| Field | Default | Why |
|---|---|---|
| `activity_tick_k` | `0.5` **(TBD/placeholder)** | `K` for `ΔP = K · ln(1 + score)`. Original spec leaves K parameterised; chosen to mirror `price_impact_k` *semantically*, not derived from spec. Docstring at `config.py:91-108` flags it; **must be back-solved by user before Phase 9 wiring** (carry-forward in `baton-runner/br-2026-05-23-p4p5`). |
| `vc_extra_boost_multiplier` | `1.03` | Periodic +3% boost for extra VC responders still in voice (original spec literal at `vc_extra_boost_step`). |

## Conventions 8c–8f MUST honour

1. **Service is a pure orchestrator over `domain/price_engine` + repositories.**
   No math beyond the per-domain-function glue (`current * (1 + ret_pct/100)`).
2. **RMW atomicity — the canonical pattern.** Every per-user price mutation
   uses `_rmw_price(user_id, compute: Callable[[Stock], Decimal])`:
   `async with locks.locked(lock_key): stock = await price_repo.get(...); ...;
   new_price = compute(stock); await price_repo.upsert(...);
   await price_repo.append_history(...)`. **Read INSIDE the lock**, not
   before. The outer pre-lock `get` may exist as a stockless-user pre-filter
   but must NOT feed the price arithmetic. Composite key
   `f"{guild_id}:{user_id}"` (8a). One `locked()` call per critical
   section, non-reentrant.
3. **Every successful price change appends `PricePoint` history AND ratchets
   `all_time_high`.** `_rmw_price` does both inside the same lock as the
   upsert. `all_time_high = max(stock.all_time_high, new_price)` — down-ticks
   never lower ATH. A no-op write (`new_price == stock.current`) skips BOTH
   upsert and history append. 8c-8f services that touch `Stock.current` must
   follow this — Phase 11 (`/price`, `/trending`) reads dynamic high/low
   from history.
4. **No-op short-circuit before writing.** If `new_price == stock.current`,
   skip upsert + history append. Keeps the tick path quiet on stable stocks.
5. **Storage-by-parameter for volatile per-user state.** `vc_boost_tick`
   takes `Iterable[VcExtraBoost]` and returns survivors; storage ownership
   stays at the Phase 9 task layer (mirroring the original bot's volatile
   `vc_extra_boosts` dict).
6. **Floor enforced through the domain layer.** `apply_floor_stall` (activity,
   VC boost) and `apply_inactivity_decay` (inactivity) own the `min_price`
   invariant; service does not re-clamp. NOTE: inactivity uses a hard floor
   (Phase-4-pinned divergence from the original's floor-stall); see module
   docstring.
7. **Closure late-binding trap.** When a compute closure captures a
   loop-variable, bind it via default-arg pattern
   (`def compute(stock_now, _x: T = x)`); when it only captures
   loop-invariants, no special handling needed.
8. **Decimal + UTC invariants** preserved end-to-end (Phase 3.1):
   `Decimal(str(settings_float))`, `datetime.now(tz=UTC)`.

## Deferred (out-of-scope for 8b)

- `activity_tick_k` calibration (M1 from iter-1 review) — value remains
  `0.5` placeholder; user must back-solve before Phase 9.
- A persistence-backed `VcExtraBoostStore` (parallel to `VoiceSessionStore`)
  if the Phase 9 task layer finds holding the list itself awkward.
- High-volume guild perf — `inactivity_decay_tick` does a full `list_all`
  sweep then filters; an `IUserRepo.list_inactive_longer_than(seconds)`
  query is the obvious adapter-level optimisation.

## Verification (iter-2)

```
pytest         → 445 passed (+6 over 439 baseline: 1 H1 race + 3 history + 2 ATH)
ruff check     → All checks passed!
ruff format    → 66 files already formatted
mypy           → Success: no issues found in 32 source files
```

H1 RED-first capture (pre-fix, commit `1e57910`): `_BarrierPriceRepo` test
observed `Decimal('101.91') < Decimal('200.00')` — stale pre-lock read
clobbered concurrent marker upsert. Post-fix: green, `after.current >= 200`.
