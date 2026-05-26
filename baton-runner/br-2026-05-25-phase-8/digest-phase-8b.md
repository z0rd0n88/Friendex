# Phase 8b exit digest — `PriceTickService`

Source: code in this worktree pre-commit; `feat/phase-8b-price-tick`.
Phase 4 + 8a + 8-fakes digests are still authoritative for the layers below.

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

## New `Settings` defaults (declared additions to `src/friendex/adapters/config.py`)

| Field | Default | Why |
|---|---|---|
| `activity_tick_k` | `0.5` | `K` for `ΔP = K · ln(1 + score)` in `compute_activity_return`. Distinct from `price_impact_k` (trade-impact, not activity gain) — collapsing the two would conflate two different tunables. |
| `vc_extra_boost_multiplier` | `1.03` | Periodic +3% boost for extra VC responders still in voice (original spec literal at `vc_extra_boost_step`). |

Both are referenced exactly once by `PriceTickService`; no other phase
behaviour changes.

## Conventions 8c–8f MUST honour

1. **Service is a pure orchestrator over `domain/price_engine` + repositories.**
   No math beyond the per-domain-function glue (`current * (1 + ret_pct/100)`).
   Future ticks (liquidation, freeze, fund) follow the same shape.
2. **Composite lock key `"<guild_id>:<user_id>"` at every per-user write** —
   inherited from 8a. Service exposes `_lock_key()` and routes all writes via
   the internal `_write_price(stock, new_price)` helper (one `locked()` call
   per critical section, non-reentrant).
3. **No-op short-circuit before locking.** If the new price equals the
   stored one (no domain change), the lock + upsert are skipped. Keeps
   the tick path quiet on stable stocks and avoids unnecessary contention
   when the activity-tick task and a trade race on the same user.
4. **Storage-by-parameter for volatile per-user state.** `vc_boost_tick`
   takes the `Iterable[VcExtraBoost]` as a method argument and returns the
   survivors. Storage ownership stays at the Phase 9 task layer (mirroring
   the original bot's volatile `vc_extra_boosts` dict). Phase 8a's stores
   (`VoiceSessionStore`, `VoicePingSessionStore`) handle volatile state
   that the *service* owns; this state is *task-owned*, so the service is
   stateless on it.
5. **Floor is enforced through the domain layer, not the service.** Every
   write path runs through `apply_floor_stall` / `apply_inactivity_decay` —
   neither returns below `min_price`. The service does not re-clamp.
6. **Decimal + UTC invariants** preserved end-to-end (Phase 3.1).

## Deferred (out-of-scope for 8b)

- A persistence-backed `VcExtraBoostStore` (parallel to `VoiceSessionStore`).
  Currently the Phase 9 task is expected to hold the list itself; if that
  proves awkward, the natural Phase 8d/9 follow-up is to add such a store.
- High-volume guild perf — `inactivity_decay_tick` does a full `list_all`
  sweep then filters in Python (mirroring the original). If real-world
  guilds grow large, an `IUserRepo.list_inactive_longer_than(seconds)`
  query is the obvious adapter-level optimisation (no service change).

## Verification (this run)

```
uv run ruff check src tests        → All checks passed!
uv run ruff format --check src tests → 66 files already formatted
uv run mypy src/friendex            → Success: no issues found in 32 source files
uv run pytest tests/application/test_price_tick_service.py -v → 9 passed
uv run pytest tests/application/    → 88 passed
uv run pytest                       → 439 passed in 6.40s
```

RED-first capture: initial run failed at collection
(`ModuleNotFoundError: friendex.application.price_tick_service`), satisfying
the gate for B1–B5 simultaneously before the service module existed. All
nine tests then went GREEN with the orchestrator implementation.
