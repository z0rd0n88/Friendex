# Phase-8-fakes digest — in-memory fakes + service-test fixtures (for 8a–8f)

**Status:** CLEAN (gate green; all 8 ACs met; boundary tests RED-verified;
semantics match real adapters + Phase 6 digests; no new deps).

## Public surface (`tests/application/fakes/fake_repos.py`)

Six in-memory fakes; each conforms *structurally* to its `interfaces.py`
Protocol (asserted `repo: IXxxRepo = FakeXxx()` under mypy — never inherits).
All methods are `async`. Keying: `(guild_id, id)` per aggregate; `guild_id`
alone for system-state.

- `FakeUserRepo` — `get/upsert/delete/list_all` + `list_active_in_last(guild_id, seconds: float)` (`last_activity >= now - seconds`).
- `FakePriceRepo` — `get/upsert/delete/list_all`, `append_history`, `get_history(..., *, since=None)` (oldest-first, `>= since`), `prune_history_older_than(cutoff) -> int` (drops `< cutoff`, cross-guild).
- `FakeFundRepo` — `get/upsert/delete/list_all` + `ensure_events_wallet(guild_id) -> HedgeFund` (idempotent; `events_wallet`/`Events Wallet`/manager `"0"`/`Decimal("0.00")`).
- `FakePenaltyRepo` — `get/upsert/delete/list_all` (plain store; returns expired).
- `FakeTradeCooldownRepo` — `get(guild_id, user_id, *, now=None)` (active iff `expires_at > now`), `upsert(cooldown)` (scope in DTO), `delete`, `list_all` (incl. expired), `purge_expired(now) -> int` (`<= now` inclusive, cross-guild).
- `FakeSystemStateRepo` — `get(guild_id)/upsert(state)/delete(guild_id)/list_all()` (unscoped).

## Fixtures (`tests/application/conftest.py`) — fresh per test

`fake_user_repo`, `fake_price_repo`, `fake_fund_repo`, `fake_penalty_repo`,
`fake_cooldown_repo`, `fake_system_state_repo`, `lock_manager` (`LockManager`),
`default_settings` (`Settings(discord_token="test-token", _env_file=None)`;
`trade_cooldown_seconds == 900` etc. defaults).

## Conventions 8a–8f MUST honor

- **`asyncio_mode = "auto"`** — write `async def test_*` with no decorator;
  fixtures are plain sync factories returning fresh instances.
- **Immutability is load-bearing.** Fakes return the *stored* object reference
  (real repos rebuild fresh). Never mutate a returned aggregate in place;
  always change a copy and round-trip via `upsert`. Following this keeps fake
  and SQLite behavior identical (the one known divergence otherwise).
- **Decimal + UTC invariants:** money/price = `Decimal`; datetimes tz-aware UTC
  (`datetime.now(tz=UTC)`). Build test data with the `_account` / `_stock` /
  `_fund` helper shapes in `test_fake_repos.py`.
- **Per-guild keying (ADR-0001):** same `user_id` in two guilds = two
  independent rows. Pass `guild_id` explicitly (except cooldown/system-state
  `upsert`, which carry `guild_id` *inside* the DTO).
- **Boundaries match adapters:** cooldown active `expires_at > now`; purge
  `<= now`; prune keeps point-at-cutoff (`< cutoff`); active-window `>=`.
- **Import path:** `from tests.application.fakes.fake_repos import Fake...`
  (`tests/__init__.py` + `tests/application/__init__.py` exist).

## Carry-forward

phase-7-locks #000: cancel-mid-acquire lock leak (1 MEDIUM) — fix in the first
service phase that uses `LockManager.locked`.
