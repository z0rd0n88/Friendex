# Remediation Plan — Issues #82 · #83 · #84

**Generated:** 2026-05-29 via three parallel multi-agent review passes over the full
Friendex codebase (`src/friendex/`, `tests/`, CI config). Each pass used a different
specialist lens:

| Pass | Agents | Issue |
|------|--------|-------|
| Pass 1 | `code-explorer` ×4 (layer fan-out) · `code-reviewer` ×4 · `python-reviewer` ×2 | [#82](https://github.com/z0rd0n88/Friendex/issues/82) |
| Pass 2 | `refactor-cleaner` · `code-simplifier` · `unused-code-cleaner` | [#83](https://github.com/z0rd0n88/Friendex/issues/83) |
| Pass 3 | `python-pro` ×2 · `ecc-security-reviewer` ×2 · `ecc-silent-failure-hunter` ×2 | [#84](https://github.com/z0rd0n88/Friendex/issues/84) |

Findings from all three passes were deduplicated and organised into **10 parallel
branches across 3 sequential waves**. Every branch owns a disjoint file set so
branches within the same wave can be implemented simultaneously without merge
conflicts.

**Total findings:** 3 CRITICAL · 27+ HIGH · 34+ MEDIUM · 23+ LOW

---

## Table of Contents

1. [How to use this document](#how-to-use-this-document)
2. [Recommended agents and skills](#recommended-agents-and-skills)
3. [Wave 1 — four parallel branches](#wave-1--four-parallel-branches-start-immediately)
4. [Wave 2 — three parallel branches](#wave-2--three-parallel-branches-after-wave-1-merges)
5. [Wave 3 — three parallel branches](#wave-3--three-parallel-branches-after-wave-2-merges)
6. [Verification gate](#verification-gate)
7. [Dependency graph](#dependency-graph)

---

## How to use this document

1. Pick any branch from the current wave (Wave 1 is open now).
2. Spin up the agents listed in that branch's section.
3. When **all** branches in a wave have merged, start the next wave.
4. Tick the corresponding checkboxes in issues #82 / #83 / #84 as you go.
5. One PR per branch. Use the branch name as the head; target `main`.
6. Every PR must pass the [verification gate](#verification-gate) before merge.

---

## Recommended agents and skills

### For every branch

| When | Use |
|------|-----|
| Before writing any Python | `/ecc-python-patterns` skill (project-mandatory) |
| Before writing code | `tdd-guide` agent — write failing test first |
| After writing code | `code-reviewer` agent + `python-reviewer` agent |
| After writing code | `security-reviewer` agent (for any branch touching auth/money/config) |
| Silent-failure fixes | `silent-failure-hunter` agent to verify the fix actually propagates errors |
| Before opening PR | `commit-guardian` agent — runs 10 pre-commit checks |
| Before opening PR | `superpowers:finishing-a-development-branch` skill |

### Specialist agents by branch type

| Branch type | Specialist agent |
|-------------|-----------------|
| Money / atomicity | `python-pro` agent |
| Task / async concurrency | `python-pro` agent |
| Discord adapter | `code-reviewer` agent |
| Persistence / SQL | `sql-pro` agent |
| Config / secrets | `security-reviewer` agent |
| Type system hardening | `python-pro` agent |
| Dead-code sweep | `refactor-cleaner` agent · `unused-code-cleaner` agent · `simplify` skill |
| CI / mypy | `tdd-guide` agent (coverage) |

---

## Wave 1 — four parallel branches (start immediately)

All four can be opened and worked concurrently — they own completely separate files.

---

### Branch: `fix/money-atomicity`

**Source issues:** #82 C1, C2, H1, H2, H3, M12 · #84 H (ghost fund, invest guard)

**Owns:** `application/trading_service.py` · `application/fund_service.py` · `domain/fund_math.py`

**Agents to use:**
- `python-pro` — unit-of-work seam design and Decimal invariant fixes
- `tdd-guide` — property tests for cover-sequence collateral invariant
- `security-reviewer` — verify no money is created or destroyed by the new transaction boundaries
- `silent-failure-hunter` — verify `_get_fund_cash` error now propagates

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #82 C1 | `trading_service.py:492-564, 608-618` | Move `_set_cooldown` inside the `async with self._locks.locked(...)` block so concurrent `/short` calls cannot both pass the pre-lock check |
| #82 C2 | `trading_service.py:553-562, 699-707`; `fund_service.py:280-281, 479-480` | Expose a unit-of-work seam from `adapters/persistence/` (shared `AsyncSession` threaded through the call). Wrap `short`, `_cover_internal`, `withdraw`, `invest`, `send_to_events` in a single session so mid-sequence failure rolls back rather than destroying money |
| #82 H1 | `fund_service.py:329` | `send_to_events` guard: replace `cash_balance` with `manager_balance = cash_balance - sum(investors.values())` (mirrors the guard already in `withdraw`) |
| #82 H2 | `trading_service.py:667-686` | Full-cover path must release exact `locked_cash`/`locked_fund` values, not proportional re-calculation. Partial-cover proportional math accumulates rounding error; add a property test asserting the invariant `locked_cash + locked_fund == shares * entry_price` across cover sequences |
| #82 H3 | `fund_service.py:403-406` | APY accrual: accumulate unquantised per-investor `Decimal` values, quantise the sum once — not each individual stake before summing |
| #82 M12 | `trading_service.py:492-493, 608-609` | Sample `now` inside the critical section, not before acquiring the lock, so cooldown `expires_at` is anchored to the actual write time |
| #84 H | `trading_service.py:250-260` | `_get_fund_cash` must not return `Decimal("0")` when the persistence call fails; raise or propagate the exception so cover cannot create a ghost fund |
| #84 H | `fund_service.py:448` | `invest()` self-block: compare `actor.id == loaded_fund.manager.id`, not `actor.id == fund_id` key |

---

### Branch: `fix/task-reliability`

**Source issues:** #82 C3, H5, H6, H7, H8, M3, M4 · #84 H (task fan-out, liquidation) · #84 M (base_task traceback)

**Owns:** `adapters/tasks/*.py` · `adapters/container.py` (task-wiring lines only) · `domain/` (SystemState extension for durable rollover)

**Agents to use:**
- `python-pro` — async task lifecycle and `asyncio` structured-concurrency patterns
- `tdd-guide` — integration test: simulate mid-sweep guild failure, assert other guilds processed
- `silent-failure-hunter` — verify every per-guild exception is logged and does not abort the sweep

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #82 C3 | `adapters/tasks/monthly_rollover_task.py:56-67` | Add `last_monthly_rollover: date` to `SystemState` (persisted). Only advance it after both `capture_month_start_net_worth` and `accrue_apy` succeed for a guild. On next tick: replay any guild whose stored date < current month. Mirrors the daily/weekly pattern |
| #82 H5 | `adapters/tasks/liquidation_task.py:68-69` | Wrap `await self._notifier(event)` in `await self._safe_run(...)` so one bad embed/permission does not abort remaining liquidation events |
| #82 H6 | `activity_tick_task.py:41-43`; `inactivity_decay_task.py:44-46`; `freeze_check_task.py:50-52` | Wrap each per-guild service call in `_safe_run`; one guild's exception must not silence the rest |
| #82 H7 | `monthly_rollover_task.py:61-67` | Wrap both `capture_month_start_net_worth` and `accrue_apy` per guild in separate `_safe_run` calls |
| #82 H8 | `daily_reset_task.py:74-77`; `weekly_reset_task.py:81-83` | Wrap `_try_reset` per guild in `_safe_run`; preserve the service-then-state ordering within each guild |
| #82 M3 | `adapters/tasks/task_runner.py` | After an unhandled exception stops a `discord.ext.tasks.loop`, add restart logic. Consider a `before_loop` / `after_loop` hook with exponential backoff, or check `task.failed()` in the Discord `on_error` hook |
| #82 M4 | `adapters/tasks/task_runner.py` | Stagger `runner.start()` calls by a small random offset (e.g. `asyncio.sleep(random.uniform(0, 2))` before first run) so all task cohorts do not hit SQLite simultaneously on startup |
| #84 H | All tasks with per-guild loops | Audit every remaining task for the same pattern; wrap any bare per-guild service call that is not already inside `_safe_run` |
| #84 M | `adapters/tasks/base_task.py:102-110` | Add `exc_info=True` (or `exc_info=e`) to the structlog call in `_safe_run`'s exception handler so the full traceback is captured, not just the exception string |

---

### Branch: `fix/entry-discord-boundary`

**Source issues:** #84 C (main.py×2, error_handler) · #82 H12, H13, H14, H15 · #84 H (container, voice_listener) · #82 M2

**Owns:** `main.py` · `adapters/discord_bot/bot.py` · `adapters/discord_bot/cogs/*.py` · `adapters/discord_bot/error_handler.py` · `adapters/discord_bot/listeners/voice_listener.py` · `adapters/container.py` (public-setter lines)

**Agents to use:**
- `code-reviewer` — Discord.py lifecycle patterns (intents, defer, error routing)
- `security-reviewer` — verify `CheckFailure` reply is ephemeral and leaks no internal state
- `python-pro` — `asyncio` resource cleanup in `main.py`

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #84 C | `main.py:47` | Replace bare `create_async_engine(settings.database_url)` with `build_engine(settings.database_url)`. The `build_engine()` factory in `adapters/persistence/db.py` installs the SQLite FK enforcement PRAGMA event listener; calling the engine constructor directly silently bypasses it |
| #84 C | `main.py:52` | Wrap `await bot.start(token)` in a `try/finally: await bot.close()` block. On exception the current code leaks the aiohttp connector and leaves the process hanging |
| #84 C | `error_handler.py:142-170` | Add an `isinstance(error, app_commands.CheckFailure)` branch before the unwrap loop. Reply ephemerally with a user-facing "you don't have permission for that" message; do not log at CRITICAL and do not pass to the "Unexpected error" path |
| #82 H12 | `adapters/discord_bot/bot.py:84` | Replace `Intents.all()` with explicit intent declaration: `message_content`, `members`, `voice_states`, `reactions`, `guilds`. Using `Intents.all()` enables the `presences` privileged intent which has no consumer and blocks bot verification past 100 guilds |
| #82 H13 | All `adapters/discord_bot/cogs/*.py` | Add `await interaction.response.defer(ephemeral=<bool>)` as the first line of every command handler before any service call. Discord requires an ack within 3 s; any service call that can exceed this will produce "interaction failed" with no error handler routing. Follow up with `interaction.followup.send(...)` instead of `interaction.response.send_message(...)` |
| #82 H14 / #84 M | `cogs/_interaction.py:25` | Replace `assert interaction.guild is not None` with `if interaction.guild is None: raise app_commands.NoPrivateMessage()`. Add `dm_permission=False` to the `@app_commands.command()` decorators on all guild-scoped commands |
| #82 H15 / #84 H | `adapters/container.py:489-491` | Add `bind_guild_id_provider(fn)` and `bind_notifier(fn)` public methods to the relevant task classes instead of direct `task._iter_guild_ids = ...` and `task._notifier = ...` attribute mutation |
| #84 H | `adapters/discord_bot/listeners/voice_listener.py:133-149` | In the SWITCH path (leave + re-join): wrap `await _do_leave()` in a try/except so an exception does not skip the subsequent `await _do_join()`. Log the leave failure at ERROR with `exc_info=True` then continue to the join |
| #82 M2 | `adapters/container.py:204-205` | Add an `on_guild_remove` listener that pops the departing guild's entries from `_voice_sessions` and `_ping_sessions` to prevent unbounded memory growth |

---

### Branch: `fix/config-settings`

**Source issues:** #84 H/M (config.py×4) · #82 H20 · #83 (micro-consolidations)

**Owns:** `adapters/config.py` · `application/stats_service.py` (deferred import) · `application/fund_service.py` (`_ZERO` constant)

**Agents to use:**
- `security-reviewer` — verify `SecretStr` is wired end-to-end and the redaction processor actually runs
- `python-pro` — pydantic-settings v2 patterns for `Decimal`-typed computed fields

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #84 H | `adapters/config.py:287` | Change `get_settings()` from `Settings.model_validate({})` to `Settings()`. The `model_validate({})` call bypasses pydantic-settings' env-source machinery; all environment variables are ignored and every field silently falls back to its default |
| #84 M | `adapters/config.py:63` | Change `discord_token: str` to `discord_token: SecretStr`. Update every call site that reads the token to use `.get_secret_value()`. This prevents the token from appearing in `repr()`, `str()`, logs, and exception tracebacks |
| #84 M | `adapters/config.py:218` | Fix the `redact_token` structlog processor: it currently watches the key `"token"` but the field on `Settings` is `"discord_token"`. Change the watched key to `"discord_token"` so the redaction actually fires |
| #84 M | `adapters/config.py:182-197` | In `parse_int_list`: catch `ValueError` per-token, log a structured warning identifying which token was malformed, and continue rather than silently dropping it |
| #82 H20 | `adapters/config.py:90-170` | Money fields (`initial_cash`, `min_price`, `trade_fee`, etc.) are typed `float` and converted at 20+ call sites via `Decimal(str(...))`. Add `Decimal`-typed computed fields (or a `@model_validator(mode="after")`) so services read pre-converted `Decimal` values directly, eliminating the per-call conversion and any risk of a forgotten `str()` wrapper |
| #83 | `application/stats_service.py:172-182` | Move the deferred `from decimal import Decimal` import to the module top level |
| #83 | `application/fund_service.py:256, 383` | Replace the three re-constructed `Decimal("0.00")` sum starters with a single module-level `_ZERO = Decimal("0")` constant |

---

## Wave 2 — three parallel branches (after Wave 1 merges)

All three can be opened concurrently once every Wave 1 PR is on `main`.

---

### Branch: `fix/persistence-hardening`

**Source issues:** #82 H9, H10, H11, M7 · #84 M (orm.py float comment) · #84 L (migrate symlink)

**Owns:** `adapters/persistence/user_repo.py` · `adapters/persistence/migrate_json_to_sqlite.py` · `adapters/persistence/types.py` · `adapters/persistence/orm.py`

**Agents to use:**
- `sql-pro` — IN-clause chunking strategy and batch-insert approach
- `tdd-guide` — test `_rebuild_many` with exactly 1000 user IDs to verify chunking
- `python-pro` — async session management for migrator batch

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #82 H9 | `adapters/persistence/user_repo.py:240-264` | `_rebuild_many` passes `user_ids` directly into an IN-clause. SQLite enforces a 999 bound-variable limit; any guild with ≥1000 opted-in users silently crashes `list_all` / `list_active_in_last`. Fix: chunk `user_ids` into batches of ≤999 and merge the results |
| #82 H10 | `adapters/persistence/user_repo.py:83-89` | Add an explicit `await session.flush()` after the `session.merge()` call in `upsert`. Currently relies on `autoflush=True` default; a future `autoflush=False` flip silently breaks the merge→delete→insert ordering |
| #82 H11 | `adapters/persistence/migrate_json_to_sqlite.py:393-401` | Migrator clears price history in one committed session, then re-appends point-by-point. A kill between clear and last insert leaves empty history. Fix: batch the clear + bulk insert in a single `async with maker() as session` block so they commit atomically |
| #82 M7 | `adapters/persistence/types.py:82-83` | `UtcDateTime.process_result_value` silently UTC-tags naive datetimes on read. This masks schema drift (a column written without tz info will produce wrong data rather than an error). Either raise `ValueError` on a naive datetime read, or at minimum log a structured warning |
| #84 M | `adapters/persistence/orm.py:228` | `voice_minutes` and `role_ping_join_minutes` are typed `float`. Add an inline comment explaining this is a deliberate exception to the Phase 3.1 Decimal invariant: these fields store aggregated durations (not money) and the accumulated float error at game scale is acceptable |
| #84 L | `adapters/persistence/migrate_json_to_sqlite.py:640` | Resolve the `--source` path via `Path(args.source).resolve()` before use. Unresolved symlinks can silently redirect the migration to a different fixture directory |

---

### Branch: `fix/error-logging-silent-failures`

**Source issues:** #84 H (stdlib logging) · #84 M/L (silent failures across application layer) · #82 L3

**Owns:** `adapters/discord_bot/error_handler.py` (logging lines) · `application/activity_service.py` · `application/voice_ping_service.py` · `application/price_tick_service.py` · `application/stats_service.py` · `application/daily_service.py` · `application/trading_service.py` (stub-overwrite lines) · `domain/market_hours.py` · `domain/models.py` · `adapters/discord_bot/listeners/reaction_listener.py`

**Agents to use:**
- `silent-failure-hunter` — verify every fix actually propagates or logs the failure with `exc_info`
- `python-pro` — structlog processor chain and context-var patterns
- `tdd-guide` — tests for naive-datetime rejection and negative net_worth validation

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #84 H | `adapters/discord_bot/error_handler.py:64` | Replace `stdlib logging.getLogger()` with `structlog.get_logger()` throughout the adapters layer. Structured fields passed via `extra={}` to a stdlib logger are silently dropped in JSON output; they must be passed as structlog keyword arguments |
| #84 M | `application/stats_service.py:103-104` | When a leaderboard user has no stock row, log a structured warning (`log.warning("leaderboard_ghost", user_id=..., guild_id=...)`) instead of silently returning a zero price |
| #84 M | `application/activity_service.py:268-281` | Voice-leave → stay-boost two-phase read-modify-write: the window between the leave lock release and the boost lock acquisition is unguarded. Document the invariant or consolidate the two operations under a single composite lock key |
| #84 M | `application/activity_service.py:275`; `application/voice_ping_service.py:275` | When `_apply_stay_boost` / `_apply_join_boost` finds no stock row, log a structured warning and return rather than silently dropping the earned boost |
| #84 M | `application/price_tick_service.py:280-281` | When the VC extra-responder boost finds no stock row, remove the entry from the survivors list and log a warning. Retaining it causes the boost to silently recur on every subsequent tick |
| #84 M | `application/trading_service.py:382, 453, 554, 699` | The double-`get` target-stub pattern calls `repo.get(target_id)` once to check existence, creates a stub, then calls `repo.get(target_id)` again. A concurrent `upsert` between the two calls can be overwritten by the stale stub. Replace with a `get_or_create` / upsert-if-absent pattern using the existing lock |
| #84 L | `application/daily_service.py:98-100` | At the exactly-48-hour streak reset boundary, emit a structured debug log distinguishing "streak reset" from "still in cooldown". This is a common support question |
| #84 L | `domain/models.py:112` | Extend `UserAccount.__post_init__` to assert `net_worth >= 0` and `month_start_net_worth >= 0` (with the same tolerance already used for `cash_balance`). Negative values currently pass silently |
| #84 L | `domain/market_hours.py:46` | `is_market_open` must reject naive datetimes: `if dt.tzinfo is None: raise ValueError("is_market_open requires a tz-aware datetime")`. A naive `datetime.now()` call at a call site currently produces a silently wrong market-hours decision |
| #84 L | `adapters/discord_bot/listeners/reaction_listener.py:68-70` | Add a guard: skip reactions where `message.author.id == self._bot.user.id` (bot-authored messages). An uncached partial message from the bot's own reaction can cause a latent crash |
| #82 L3 | `adapters/discord_bot/error_handler.py:136` | The `settings` parameter is accepted then immediately `del`-ed. Either drop the parameter from the function signature (and update the call site) or annotate `# noqa: ARG001` with a comment explaining why it is kept for future routing |

---

### Branch: `fix/economy-exploits`

**Source issues:** #84 M (economy×3) · #84 L (trading_cog bounds, allowed_mentions) · #82 M6, M8

**Owns:** `application/trading_service.py` (cover/opt-out section) · `application/voice_ping_service.py` (self-ping section) · `adapters/discord_bot/listeners/member_listener.py` · `adapters/discord_bot/cogs/trading_cog.py` · `adapters/discord_bot/cogs/account_cog.py` · `adapters/discord_bot/cogs/admin_cog.py` · `adapters/discord_bot/embeds.py`

**Agents to use:**
- `security-reviewer` — verify exploit mitigations are complete (no bypass path remains)
- `python-pro` — audit log design for discipline events
- `tdd-guide` — test the cover opt-out edge case; test self-ping block

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #84 M | `application/trading_service.py:651-652` | When a cover call hits a target who has opted out after the short was opened, the holder is trapped with no exit. Fix: allow cover even when target is opted out (the position predates the opt-out) — or add an admin `force_cover` escape hatch and document the invariant |
| #84 M | `application/voice_ping_service.py:169` | Block self-ping: if `host_id == responder_id`, skip the credit award. Also block if the responder holds the same VC role as the host (`responder_id in role.members`) to prevent alt-account farming |
| #84 M | `adapters/discord_bot/listeners/member_listener.py:58-65` | Discipline penalty (17% price drop on timeout/ban): add an audit log entry (`log.info("discipline_penalty_applied", guild_id=..., target_id=..., actor_id=..., reason=...)`) and a per-user cooldown so the same target cannot be penalised more than once per rolling window by the same moderator |
| #82 M6 | `application/voice_ping_service.py:279`; `application/activity_service.py:278` | `_apply_join_boost` and `_apply_stay_boost` upsert price but do not call `append_history`. Add `repo.append_history(PricePoint(...))` mirroring the guard in `_rmw_price`: `if new_price != stock.current` |
| #82 M8 | `application/fund_service.py` | Document (or guard) the `ensure_events_wallet` TOCTOU. The race is benign in single-process asyncio (the event loop is single-threaded and the lock covers the wallet creation). Add a comment to the code confirming this so future multi-process deployments know to revisit |
| #84 L | `adapters/discord_bot/cogs/trading_cog.py:84,111,138,165` | Replace `app_commands.Range[int, 1, None]` with a bounded range (e.g. `Range[int, 1, 1_000_000]`). An unbounded share count allows passing 2⁵³ - 1 shares, triggering expensive `Decimal` arithmetic that DoS's the event loop |
| #84 L | `adapters/discord_bot/cogs/account_cog.py:87,176`; `admin_cog.py:46,61` | Add `allowed_mentions=discord.AllowedMentions.none()` to the four static-content `interaction.response.send_message()` calls that are currently missing it. Consistent with the invariant already applied elsewhere |

---

## Wave 3 — three parallel branches (after Wave 2 merges)

---

### Branch: `refactor/domain-consolidation`

**Source issues:** #82 H4, H16, H17, M1 · #84 M (type hardening across domain) · #84 L (type literals, match)

**Owns:** `domain/activity.py` · `domain/errors.py` · `domain/models.py` · `domain/fund_math.py` · `application/lock_manager.py` · `application/interfaces.py` · `application/snapshot_models.py` · `application/liquidation_service.py`

**Agents to use:**
- `python-pro` — shared helper extraction, `kw_only` dataclass patterns, Protocol covariance
- `refactor-cleaner` — verify no dead call sites remain after helper extraction
- `tdd-guide` — property tests for tie-safe rank formula; tests for new error types

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #82 H4 | `domain/activity.py:104` | Replace `sorted_scores.index(score)` (first-match, order-dependent) with `sum(1 for s in all_scores if s > score) + 1` for a tie-safe percentile rank |
| #82 H16 | Multiple files | Extract three shared helpers: (1) `CENT`/`quantise()` from `domain/price_engine.py` — remove the 6 private copies; (2) `guild_lock_key(guild_id, user_id)` into `application/lock_manager.py` — remove the 9 f-string duplicates; (3) `_seed_user_account(user_id, settings, now)` into a shared application helper — remove the 4 per-service copies |
| #82 H17 | `domain/errors.py`; `application/fund_service.py:457` | Add `FundNotFound(DomainError)` with a `fund_id` field and `NotFundManager(DomainError)`. Update `invest`, `send_to_events`, and manager-only paths to raise these instead of repurposing `InvalidAmount` |
| #82 M1 | `application/liquidation_service.py:159` | `LiquidationService` calls `trading_service._cover_internal` (private). Promote to `cover_forced(position, ...)` with an assertion that the caller holds the relevant lock, or extract the body to a shared `_cover_positions` helper both services import |
| #84 M | `domain/models.py` | Add `kw_only=True` to all domain dataclasses (5–12 fields each). This prevents silent positional-argument construction errors when fields are added or reordered |
| #84 M | `application/interfaces.py:113,148,196,230,268,299` | Change Protocol method return types from `list[T]` to `Sequence[T]`. `list` return types lock all implementations to a concrete list; `Sequence` is covariant and allows any ordered immutable sequence |
| #84 M | `application/snapshot_models.py:49-50` | Change `positions: dict[str, PositionSnapshot]` to `positions: Mapping[str, PositionSnapshot]` — a read-only structural type |
| #84 M | `application/snapshot_models.py:104` | Change `UserStats.engagement_tier: str` to `Literal["Elite", "High", "Medium", "Low"]` to make the tier contract explicit and statically checkable |
| #84 L | `domain/errors.py:89` | Change `NoPosition.position_type: str` to `Literal["long", "short"]` |
| #84 L | `domain/fund_math.py:83` | Replace `if/elif/else` period dispatch in `compute_apy_accrual` with a `match` statement for static exhaustiveness checking |
| #84 L | `application/snapshot_models.py:123` | `FundInfoResult.fund` embeds a mutable `HedgeFund` aggregate in a frozen DTO. Replace with a read-only projection dataclass or use `types.MappingProxyType` for any `dict` fields |
| #84 L | `application/lock_manager.py:51-73` | Update the cancel-safety docstring to accurately describe which phase of `_ensure_lock` is cancellation-safe and which is not |

---

### Branch: `fix/typing-and-ci`

**Source issues:** #82 H18, H19, M9, M10 · #83 (mypy cleanups, `__future__` annotations) · #84 L (#82 L6 becomes valid after H18)

**Owns:** `mypy.ini` · `.github/workflows/ci.yml` · `tests/` (type annotation changes only) · `pyproject.toml` · `domain/models.py` (annotations import) · `domain/errors.py` (annotations import) · `application/liquidation_service.py` (annotations import) · `application/discipline_service.py` (annotations import)

**Agents to use:**
- `tdd-guide` — ensure coverage gate is established at current baseline before raising it
- `python-pro` — `cast()` patterns and mypy configuration for test modules

**Items:**

| ID | File:Line | Description |
|----|-----------|-------------|
| #82 H18 | `mypy.ini`; `.github/workflows/ci.yml:45` | Add `tests/` to the mypy invocation. Clean the 15 type errors it surfaces: generator-return annotations on autouse fixtures, `_BarrierUserRepo` needs a type stub or `# type: ignore[misc]` with a comment, `**dict[str, object]` splat into typed dataclasses |
| #82 H19 | `tests/adapters/discord_bot/cogs/conftest.py:165,177,189,201,213,225`; `tests/adapters/discord_bot/listeners/conftest.py:258,270,282,294,306,318,330,342` | Replace 14× `# type: ignore[return-value]` with `cast(ServiceClass, mock)` |
| #82 M9 | `.github/workflows/ci.yml:45` | Raise `--cov-fail-under` from `0` to the current measured baseline (run `uv run pytest --cov` first to establish it), then set the gate at that value. The project testing rules require 80% minimum |
| #82 M10 | `tests/test_scaffold.py` | Delete the Phase-1 vestigial scaffold test. Remove the redundant `@pytest.mark.asyncio` on `tests/integration/test_migration_realistic.py:98` (redundant under `asyncio_mode = "auto"`) |
| #83 | `mypy.ini` | Remove the now-flagged `[mypy-dpytest.*]` and `[mypy-freezegun.*]` sections if those packages are not in the test suite, or move them to the active sections list after H18 lands |
| #83 | `domain/models.py`; `domain/errors.py`; `application/liquidation_service.py`; `application/discipline_service.py` | Add `from __future__ import annotations` to the four modules that are currently missing it |
| #82 L6 | `mypy.ini` | The `[mypy-dpytest.*]` / `[mypy-freezegun.*]` sections become non-flagged once `tests/` joins the mypy run (H18); validate this after merge |

---

### Branch: `simplify/dead-code-sweep`

**Source issues:** #83 (all remaining consolidation targets) · #84 L (container generator, container dead code) · #82 LOWs

**Owns:** everything not claimed by `refactor/domain-consolidation` or `fix/typing-and-ci` — safe because this branch runs last and only deletes / renames / simplifies

**Agents to use:**
- `refactor-cleaner` — runs ruff/mypy/import analysis to identify dead code
- `unused-code-cleaner` — language-specific unused import/function/class detection
- `code-simplifier` — clarity and consistency refactors that preserve behaviour
- `simplify` skill — review changed code for reuse, quality, efficiency after each pass

**Items:**

| ID | File | Description |
|----|------|-------------|
| #83 | Multiple | Consolidate `_CENT`/`_quantise` call sites now that `domain/price_engine.py` exports the shared version (landed in `refactor/domain-consolidation`). Remove per-file private copies |
| #83 | Multiple | Consolidate `_lock_key` call sites to use `guild_lock_key()` from `application/lock_manager.py` |
| #83 | Multiple | Consolidate `_get_or_create_user` call sites to use the shared seed helper |
| #83 | `adapters/container.py:116-130` | Remove Phase-13 `_empty_guild_ids` and `_noop_notifier` no-op placeholders; the real Phase-14 wiring is in place |
| #84 L | `adapters/container.py:487` | Change `list(self._guild_ids)` (or equivalent) to a generator expression in `iter_guild_ids`. The current code allocates a new list on every task tick |
| #83 | All `*.py` | Run `ruff check --select F401 --fix` to remove dead imports; review each removal before committing |
| #83 | All `*.py` | Audit unused private helpers (`_foo` with zero call sites) using `refactor-cleaner`; remove with test suite verification after each deletion |
| #83 | `tests/` | Identify and deduplicate test fixtures that are defined locally but already exist at a higher conftest level |
| #83 | All `*.py` | Run `ruff check --select ARG` to surface shadowed or unused parameters |
| #82 L1 (deferred) | Various | Any remaining `from __future__ import annotations` gaps not covered by `fix/typing-and-ci` |
| #82 L2 | `application/price_tick_service.py:192, 229` | Make defensive closure capture consistent: both tick functions should either use default-arg capture or neither should |
| #82 L5 | `adapters/discord_bot/embeds.py:113-119` | Add `assert when.tzinfo is not None` in `_relative_timestamp` |

---

## Verification gate

Every PR must pass before merge:

```bash
uv run pytest                                          # all tests pass
uv run pytest --cov src/friendex --cov-report=term    # coverage >= baseline (Wave 3: >= 80%)
uv run ruff check .                                    # zero lint errors, no new ignores
uv run ruff format --check .                           # zero format violations
uv run mypy src/friendex                               # zero type errors (Wave 3: includes tests/)
```

Run these inside your worktree before opening the PR. The `commit-guardian` agent will
run the same checks automatically.

---

## Dependency graph

```
Wave 1 (all parallel — start now)
  fix/money-atomicity          ─┐
  fix/task-reliability           ├──→ Wave 2 (all parallel — after Wave 1 fully merged)
  fix/entry-discord-boundary     │      fix/persistence-hardening          ─┐
  fix/config-settings          ─┘      fix/error-logging-silent-failures    ├──→ Wave 3
                                        fix/economy-exploits               ─┘
                                               │
                                               ▼
                                    Wave 3 (all parallel — after Wave 2 fully merged)
                                      refactor/domain-consolidation
                                      fix/typing-and-ci
                                      simplify/dead-code-sweep  ← must run after
                                                                   refactor/domain-consolidation
                                                                   merges (call-site cleanup)
```

### Key conflict notes

- `simplify/dead-code-sweep` consolidates call sites created by `refactor/domain-consolidation`.
  Open it only after the domain-consolidation PR is on `main`.
- `fix/error-logging-silent-failures` and `fix/economy-exploits` both touch
  `activity_service.py` and `voice_ping_service.py` in different sections. Running them
  in the same wave is safe; just coordinate on line-level ownership or merge one before
  the other lands.
- The Wave 1 `fix/money-atomicity` branch and the Wave 2
  `fix/error-logging-silent-failures` branch both touch `trading_service.py` in
  completely different line ranges. The Wave 2 wait eliminates any merge conflict.
