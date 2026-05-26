# Phase 8a exit digest — `ActivityService` + `VoicePingService`

Source: code @ `b56bca9`; review `pass-baton/phase-8a/005-...-iter2-clean.md`.

## Public surface
```python
class ActivityService:
    def __init__(self, *, guild_id: str, user_repo, price_repo,
                 lock_manager: LockManager, settings: Settings,
                 voice_sessions: VoiceSessionStore) -> None
    async def record_message(self, *, author_id, has_attachment, is_reply, channel_id)
    async def record_reaction(self, *, target_user_id, reactor_id)
    async def record_voice_join(self, *, user_id, channel_id)
    async def record_voice_leave(self, *, user_id)
    async def reset_today_buckets(self) / reset_week_buckets(self)

class VoicePingService:
    def __init__(self, *, guild_id: str, user_repo, price_repo,
                 ping_sessions: VoicePingSessionStore, lock_manager: LockManager,
                 settings: Settings, clock: Clock) -> None
    async def handle_voice_join(self, *, host_id, joiner_id, channel_id, message_id)
    async def reward_voice_ping_response(self, *, responder_id, message_id, channel_id)

class VoiceSessionStore:      # volatile: set/get/pop/link_ping/list_all
class VoicePingSessionStore:  # volatile: get/set/pop/list_all
```

## New `Settings` defaults (from `docs/spec/original-skeleton.md`)
`voice_ping_first_n_joiners=10`, `voice_ping_join_boost=1.20`, `voice_stay_boost=1.50`, `voice_stay_bonus_minutes=60.0`, `voice_ping_base_points=5.0`, `voice_ping_fast/medium/slow_multiplier=3.0/2.0/1.0`, `voice_ping_host_credit=0.5`, `photo_bonus_points=10.0`.

## Conventions 8b–8f MUST honour
1. **`guild_id` is a `__init__` kwarg** on every per-guild service (ADR-0001 Approach C); captured once as `self._guild_id`; domain models stay guild-agnostic.
2. **Composite lock key at EVERY call site.** Define `def _lock_key(self, uid): return f"{self._guild_id}:{uid}"` and use `async with self._locks.locked(self._lock_key(uid)):` — never pass a bare id (Phase 14 shares ONE `LockManager`; bare keys break ADR-0001, proven by the iter-2 two-guild barrier test).
3. **`LockManager` is DI'd, never per-call**; do not touch `_locks` outside tests. **Non-reentrant** — no nested `locked()` on the same id.
4. **Persisted aggregates: read → `dataclasses.replace` → `repo.upsert(self._guild_id, replaced)`** — no in-place mutation.
5. **`Decimal` for money, UTC-aware datetimes** (Phase 3.1).

## Deferred to Phase 12
- `VoiceSessionStore.link_ping` in-place set mutation → rebuild via `replace(..., from_ping_message_ids=… | {mid})` under lock.
- `reward_voice_ping_response` RMW non-atomic across awaits → store-level or `message_id`-keyed lock spanning check-then-write.
