# Friendex — Phased Migration & Build Plan

> **Historical document.** This phased build plan is **complete as of 2026-05-28** —
> all 18 phases (Phase 0 through Phase 17) merged to `main`. The bot is fully built
> and deployable. This document is preserved for historical context and to explain
> architectural decisions made during construction.
> See [docs/deployment-guide.md](./deployment-guide.md) to deploy the bot.

## Executive Summary

This document is the concrete, file-by-file build roadmap for greenfield construction of Friendex from the spec, organised as eighteen incremental phases (Phase 0 through Phase 17) each delivered as its own GitHub PR against `main`. Phase 0 creates the master tracking issue; Phase 1 lays down packaging and tooling; Phases 2 through 14 build the system bottom-up (config, domain, persistence, concurrency, services, tasks, Discord adapters, and finally the bot entry point); Phases 15-17 cover migration verification, production cutover, and hardening of the items deferred by the target architecture. Every phase has a deterministic verification gate (`ruff`, `mypy`, `pytest`) that must pass before the phase PR may be merged, every PR body references the master tracking issue with `Refs #<master-id>`, and every PR is independently revertible so a regression in any single phase rolls back without disturbing earlier work. Calendar total: ~5-7 working weeks single-engineer, ~3-4 weeks if Phases 8a-8f and 11 are parallelised across two engineers.

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Table of Contents](#table-of-contents)
3. [Pre-flight: Master Tracking Issue](#pre-flight-master-tracking-issue)
4. [Phase Overview Table](#phase-overview-table)
5. [Phase 0 — Master Tracking Issue](#phase-0--master-tracking-issue)
6. [Phase 1 — Packaging & Tooling Scaffold](#phase-1--packaging--tooling-scaffold)
7. [Phase 2 — Config & Constants](#phase-2--config--constants)
8. [Phase 3 — Domain Models & Errors](#phase-3--domain-models--errors)
9. [Phase 4 — Domain Pure Functions](#phase-4--domain-pure-functions)
10. [Phase 5 — Persistence: ORM & Alembic Baseline](#phase-5--persistence-orm--alembic-baseline)
11. [Phase 6 — Persistence: Repositories & JSON Migrator](#phase-6--persistence-repositories--json-migrator)
12. [Phase 7 — Concurrency Primitives](#phase-7--concurrency-primitives)
13. [Phase 8a — Activity & Voice Ping Services](#phase-8a--activity--voice-ping-services)
14. [Phase 8b — Price Tick Service](#phase-8b--price-tick-service)
15. [Phase 8c — Trading Service](#phase-8c--trading-service)
16. [Phase 8d — Portfolio & Stats Services](#phase-8d--portfolio--stats-services)
17. [Phase 8e — Fund & Daily Services](#phase-8e--fund--daily-services)
18. [Phase 8f — Liquidation & Discipline Services](#phase-8f--liquidation--discipline-services)
19. [Phase 9 — Background Tasks](#phase-9--background-tasks)
20. [Phase 10 — Discord Embed Builders](#phase-10--discord-embed-builders)
21. [Phase 11 — Discord Cogs](#phase-11--discord-cogs)
22. [Phase 12 — Discord Listeners](#phase-12--discord-listeners)
23. [Phase 13 — Error Handler & Container Wiring](#phase-13--error-handler--container-wiring)
24. [Phase 14 — Bot Factory & Entry Point](#phase-14--bot-factory--entry-point)
25. [Phase 15 — JSON-to-SQLite Migration Verification](#phase-15--json-to-sqlite-migration-verification)
26. [Phase 16 — Production Smoke Test (Cutover)](#phase-16--production-smoke-test-cutover)
27. [Phase 17 — Hardening & Deferred Items](#phase-17--hardening--deferred-items)
28. [Verification Gate Matrix](#verification-gate-matrix)
29. [Risk & Rollback](#risk--rollback)
30. [Estimated Calendar](#estimated-calendar)

---

## Pre-flight: Master Tracking Issue

Phase 0 establishes a single GitHub issue that acts as the spine of the entire refactor. Every PR's body references it with `Refs #<master-id>`, which makes the GitHub UI render a backlink on the master issue, giving anyone a one-click view of the full chain of work.

### Convention

- **Branch naming:** `feat/<phase-slug>` per phase. Examples: `feat/phase-0-scaffold` is reserved as a no-op marker but is not actually opened (Phase 0 is issue-only); real branches start at `feat/phase-1-scaffold`, `feat/phase-2-config`, `feat/phase-3-domain-models`, etc.
- **PR-per-phase:** Each phase is one PR merged into `main` after review and after its verification gate passes locally and in CI.
- **Backlink:** Every PR body must contain a `Refs #<master-id>` line. Every commit message in the body (not the title) should also carry the reference so the issue timeline picks up commit cross-references.
- **Closing keyword:** The final phase (Phase 17) PR body uses `Closes #<master-id>` so merging it auto-closes the master issue and populates `closedByPullRequestsReferences` in the GraphQL API for project tracking.

### Master Issue Creation Command

```bash
gh issue create \
  --repo z0rd0n88/Friendex \
  --title "Refactor: Greenfield build of Friendex from spec" \
  --label "epic,refactor" \
  --body "$(cat <<'EOF'
## Goal

Build Friendex from the Phase 2 target architecture. The repository currently contains only the spec document and the Phase 1-3 planning docs (`docs/01-*`, `docs/02-*`, `docs/04-*`); there is no `bot.py`. This issue tracks the full phased construction.

## Convention

- One PR per phase, each branched from `main` as `feat/phase-N-<slug>`.
- Every PR body contains `Refs #<this-issue-number>`.
- Every PR has a verification gate that must pass in CI before merge.
- PRs are merged in strict numeric order — a later phase cannot ship before its dependency.
- The final phase PR closes this issue with `Closes #<this-issue-number>`.

## Phase Checklist

- [ ] Phase 1 — Packaging & tooling scaffold (`pyproject.toml`, CI, src tree)
- [ ] Phase 2 — Config & constants (`adapters/config.py` + `Settings`)
- [ ] Phase 3 — Domain models & errors (`domain/models.py`, `domain/errors.py`)
- [ ] Phase 4 — Domain pure functions (price engine, activity, market hours, fund math)
- [ ] Phase 5 — Persistence: ORM + Alembic baseline
- [ ] Phase 6 — Persistence: repository interfaces + JSON-to-SQLite migrator
- [ ] Phase 7 — Concurrency primitives (`LockManager`)
- [ ] Phase 8a — `activity_service` + `voice_ping_service`
- [ ] Phase 8b — `price_tick_service`
- [ ] Phase 8c — `trading_service`
- [ ] Phase 8d — `portfolio_service` + `stats_service`
- [ ] Phase 8e — `fund_service` + `daily_service`
- [ ] Phase 8f — `liquidation_service` + `discipline_service`
- [ ] Phase 9 — Background tasks (incl. new daily/weekly reset, liquidation, monthly rollover)
- [ ] Phase 10 — Discord embed builders
- [ ] Phase 11 — Discord cogs (trading, portfolio, fund, daily, stats, account, admin)
- [ ] Phase 12 — Discord listeners (message, voice, reaction, member)
- [ ] Phase 13 — Error handler & container wiring
- [ ] Phase 14 — Bot factory & entry point
- [ ] Phase 15 — JSON-to-SQLite migration verification with synthetic fixtures
- [ ] Phase 16 — Production smoke test (cutover)
- [ ] Phase 17 — Hardening: /fund invest, APY accrual, deferred open questions

## Sub-issue Convention (Optional)

If granular tracking is wanted, open a sub-issue per phase and replace each checklist item with \`- [ ] Phase N — title (#sub-issue-number)\`. Otherwise, leave checkbox-only; commits and PRs will still link back via \`Refs\`.

## Reference Documents

- \`docs/01-current-state.md\` — Phase 1 spec analysis
- \`docs/02-target-architecture.md\` — Phase 2 target architecture
- \`docs/04-migration-plan.md\` — this file's expanded source
- \`docs/spec/original-skeleton.md\` — original spec
EOF
)"
```

After creation, capture the returned issue number as `<master-id>` and substitute it everywhere `Refs #<master-id>` appears below.

---

## Phase Overview Table

| Phase | Name | Branch | Depends on | Files (+/~) | Complexity |
|------:|------|--------|-----------|------------:|:----------:|
| 0  | Master tracking issue | — (no branch) | none | 0 | S |
| 1  | Packaging & tooling scaffold | `feat/phase-1-scaffold` | 0 | 18 | M |
| 2  | Config & constants | `feat/phase-2-config` | 1 | 4 | S |
| 3  | Domain models & errors | `feat/phase-3-domain-models` | 2 | 6 | M |
| 4  | Domain pure functions | `feat/phase-4-domain-funcs` | 3 | 10 | M |
| 5  | Persistence: ORM + Alembic baseline | `feat/phase-5-orm` | 3 | 8 | M |
| 6  | Persistence: repos + JSON migrator | `feat/phase-6-repos` | 5 | 11 | L |
| 7  | Concurrency primitives | `feat/phase-7-locks` | 3 | 3 | S |
| 8a | Activity + voice ping services | `feat/phase-8a-activity` | 4, 6, 7 | 5 | M |
| 8b | Price tick service | `feat/phase-8b-price-tick` | 8a | 3 | M |
| 8c | Trading service | `feat/phase-8c-trading` | 8b | 3 | L |
| 8d | Portfolio + stats services | `feat/phase-8d-portfolio` | 8c | 5 | M |
| 8e | Fund + daily services | `feat/phase-8e-fund-daily` | 8d | 5 | M |
| 8f | Liquidation + discipline services | `feat/phase-8f-liq-disc` | 8e | 5 | M |
| 9  | Background tasks | `feat/phase-9-tasks` | 8a–8f | 18 | L |
| 10 | Discord embed builders | `feat/phase-10-embeds` | 8a–8f | 3 | M |
| 11 | Discord cogs | `feat/phase-11-cogs` | 9, 10 | 16 | L |
| 12 | Discord listeners | `feat/phase-12-listeners` | 11 | 9 | M |
| 13 | Error handler & container wiring | `feat/phase-13-container` | 12 | 5 | M |
| 14 | Bot factory & entry point | `feat/phase-14-bot-factory` | 13 | 4 | S |
| 15 | JSON-to-SQLite migration verification | `feat/phase-15-migrate-verify` | 6, 14 | 4 | M |
| 16 | Production smoke test (cutover) | `feat/phase-16-cutover` | 15 | 2 | S |
| 17 | Hardening & deferred items | `feat/phase-17-hardening` | 16 | 8+ | M |

S = ~1 day; M = ~2–3 days; L = ~1 week.

---

## Phase 0 — Master Tracking Issue

**Goal:** Establish the GitHub issue that all subsequent phases reference; no code.

**Branch name:** none (issue-only).

**Files created:** none.

**Files modified:** none.

**Verification gate:**
```bash
gh issue view <master-id> --json number,title,state -q '.state == "OPEN"'
```
Output must be `true`.

**Commit boundary guidance:** Not applicable.

**References:** Self.

---

## Phase 1 — Packaging & Tooling Scaffold

**Goal:** Establish the empty `src/friendex/` package layout, dependency declarations, lint/type/test runners, and CI — so every subsequent phase merges into a workable repo.

**Branch name:** `feat/phase-1-scaffold`

**Files created:**

- `/home/alex/Friendex/pyproject.toml` — project metadata, `[project.dependencies]` (`discord.py>=2.4`, `sqlalchemy[asyncio]>=2.0`, `aiosqlite>=0.20`, `alembic>=1.13`, `pydantic-settings>=2.4`, `structlog>=24.1`, `python-dotenv>=1.0`); `[dependency-groups.dev]` (`pytest>=8`, `pytest-asyncio>=0.24`, `pytest-cov>=5`, `mypy>=1.11`, `ruff>=0.6`, `dpytest>=0.7`, `freezegun>=1.5`).
- `/home/alex/Friendex/uv.lock` — generated by `uv lock`; committed.
- `/home/alex/Friendex/.python-version` — pins `3.11`.
- `/home/alex/Friendex/.gitignore` — excludes `.venv/`, `data/`, `*.db`, `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `htmlcov/`, `.env`, `.worktrees/`.
- `/home/alex/Friendex/.gitattributes` — `* text=auto eol=lf`, `*.sh text eol=lf`, `uv.lock binary`.
- `/home/alex/Friendex/.env.example` — full template from `02-target-architecture.md` §Config and Secrets.
- `/home/alex/Friendex/ruff.toml` — `line-length = 100`, `target-version = "py311"`, enabled rule families `E,F,W,I,B,UP,SIM,RUF`.
- `/home/alex/Friendex/mypy.ini` — `strict = True`, `python_version = 3.11`, `plugins = pydantic.mypy`, per-module overrides for `discord.*` (`ignore_missing_imports = True`).
- `/home/alex/Friendex/.pre-commit-config.yaml` — `ruff`, `ruff-format`, `mypy`, `trailing-whitespace`, `end-of-file-fixer`, `check-yaml`.
- `/home/alex/Friendex/.github/workflows/ci.yml` — matrix on Python 3.11/3.12; jobs: `lint` (ruff), `typecheck` (mypy), `test` (pytest with coverage).
- `/home/alex/Friendex/src/friendex/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/domain/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/application/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/adapters/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/adapters/persistence/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/listeners/__init__.py` — empty.
- `/home/alex/Friendex/src/friendex/adapters/tasks/__init__.py` — empty.
- `/home/alex/Friendex/tests/__init__.py` — empty.
- `/home/alex/Friendex/tests/conftest.py` — sets `pytest_plugins = ["pytest_asyncio"]`, `asyncio_mode = "auto"`.
- `/home/alex/Friendex/tests/test_scaffold.py` — single test `def test_package_importable(): import friendex` ensuring the package resolves.

**Files modified:** none.

**Verification gate:**
```bash
uv sync --all-extras
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest tests/test_scaffold.py -v
uv run pre-commit run --all-files
```
All four must exit zero. GitHub Actions CI run on the PR must be green.

**Commit boundary guidance:** Three commits — (1) `chore: project scaffold and pyproject`, (2) `chore: add ruff/mypy/pre-commit config`, (3) `chore: add CI workflow and scaffold test`.

**References:** `Refs #<master-id>`.

---

## Phase 2 — Config & Constants

**Goal:** Implement the `Settings` class with full validation; lift every magic number from the spec into named config fields.

**Branch name:** `feat/phase-2-config`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/config.py` — `Settings` `BaseSettings` class with every field defined in `02-target-architecture.md` §Config and Secrets, plus `configure_logging()` helper that wires `structlog`. Includes a `Settings.model_validate()` call in a top-level `get_settings()` function memoised via `functools.lru_cache`.
- `/home/alex/Friendex/tests/adapters/__init__.py`
- `/home/alex/Friendex/tests/adapters/test_config.py` — covers: (a) loads required fields from a temp `.env`; (b) raises `ValidationError` when `DISCORD_TOKEN` is missing; (c) parses `VC_PING_ROLE_IDS` as a comma-separated list of ints; (d) parses `MARKET_OPEN` / `MARKET_CLOSE` as `datetime.time`; (e) defaults match the documented values.
- `/home/alex/Friendex/tests/adapters/fixtures/test.env` — sample env file used as a fixture.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/config.py tests/adapters/
uv run mypy src/friendex/adapters/config.py
uv run pytest tests/adapters/test_config.py -v --cov=src/friendex/adapters/config --cov-fail-under=90
```

**Commit boundary guidance:** Two commits — (1) `feat: settings class with pydantic-settings`, (2) `test: settings validation coverage`.

**References:** `Refs #<master-id>`.

---

## Phase 3 — Domain Models & Errors

**Goal:** Implement every dataclass in `domain/models.py` and the full exception taxonomy in `domain/errors.py`. No behaviour — just typed shape and invariants.

**Branch name:** `feat/phase-3-domain-models`

**Files created:**

- `/home/alex/Friendex/src/friendex/domain/models.py` — `ActivityBucket`, `DailyProgress`, `LongPosition`, `ShortPosition`, `UserAccount`, `PricePoint`, `Stock`, `HedgeFund`, `FundPenalty`, `VoiceSession`, `VoicePingSession`, `VcExtraBoost` (exact signatures from `02-target-architecture.md` §Domain Model). Each carries `__post_init__` invariant checks via `raise ValueError(...)` per Phase 3a correction.
- `/home/alex/Friendex/src/friendex/domain/errors.py` — `DomainError(user_facing_message: str)` base; `InsufficientFunds`, `MarketClosed`, `PositionFrozen`, `OnCooldown`, `OptedOut`, `NoPosition`, `InsufficientShares`, `SelfTrade`, `InvalidAmount`, `FundInsufficientBalance`, `AlreadyOptedIn`, `AlreadyOptedOut`. Also `FriendexError`, `PersistenceError`, `DiscordError` base for infrastructure errors.
- `/home/alex/Friendex/tests/domain/__init__.py`
- `/home/alex/Friendex/tests/domain/test_models.py` — per-model: happy-path construction, each `__post_init__` invariant fails with `ValueError` on bad input, equality semantics, `voice_unique_channels` int→str normalisation.
- `/home/alex/Friendex/tests/domain/test_errors.py` — covers: each `DomainError` subclass carries the expected user-facing message; `MarketClosed(open_at, close_at).user_facing_message` is well-formed; `PersistenceError` does NOT inherit from `DomainError`.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/domain/ tests/domain/
uv run mypy src/friendex/domain/
uv run pytest tests/domain/ -v --cov=src/friendex/domain --cov-fail-under=95
```

**Commit boundary guidance:** Three commits — (1) `feat(domain): dataclass models with invariants`, (2) `feat(domain): error taxonomy`, (3) `test(domain): models and errors`.

**References:** `Refs #<master-id>`.

---

## Phase 4 — Domain Pure Functions

**Goal:** Implement every piece of pure game math as a plain function that takes `Settings` (or relevant fields) as an argument. No globals, no I/O. Money and price parameters/returns are `Decimal` (Phase 3.1 invariant — Decimal at the boundary); rate and factor tunables (`k`, `decay`, `apy`) and dimensionless engagement scores stay `float`.

**Branch name:** `feat/phase-4-domain-funcs`

**Files created:**

- `/home/alex/Friendex/src/friendex/domain/price_engine.py` — `apply_trade_impact(current: Decimal, shares: int, is_buy: bool, k: float, min_price: Decimal) -> Decimal`; `apply_floor_stall(current: Decimal, proposed: Decimal, min_price: Decimal) -> Decimal`; `compute_activity_return(activity: ActivityBucket) -> Decimal`; `apply_inactivity_decay(current: Decimal, decay: float, min_price: Decimal) -> Decimal`.
- `/home/alex/Friendex/src/friendex/domain/activity.py` — `calculate_trending_score(bucket: ActivityBucket) -> float`; `get_engagement_tier(score: float, all_scores: list[float]) -> str`; `reset_activity_bucket(bucket: ActivityBucket, now: datetime) -> ActivityBucket` (returns new bucket — no mutation).
- `/home/alex/Friendex/src/friendex/domain/market_hours.py` — `is_trading_day(dt: datetime) -> bool`; `is_sunday(dt: datetime) -> bool`; `is_market_open(dt: datetime, market_open: time, market_close: time, sunday_buy_allowed: bool = False) -> bool`. The function signature takes the times from `Settings`; no module-level constants.
- `/home/alex/Friendex/src/friendex/domain/fund_math.py` — `compute_apy_accrual(balance: Decimal, apy: float, period: Literal["monthly", "annual"]) -> Decimal`; `compute_effective_apy(base_apy: float, penalty: FundPenalty | None, now: datetime) -> float`; `compute_net_worth(account: UserAccount, prices: dict[str, Stock], fund: HedgeFund | None) -> Decimal`.
- `/home/alex/Friendex/tests/domain/test_price_engine.py` — parametrised tests covering buy/sell impact directions, min-price floor clamp, decay arithmetic, activity return formula edges (zero, max).
- `/home/alex/Friendex/tests/domain/test_activity.py` — score is monotonic in each input, tier boundaries, reset returns new instance.
- `/home/alex/Friendex/tests/domain/test_market_hours.py` — Sunday closed, Saturday open during window, Monday open during window, overnight wrap (close=04:30 next day), sunday_buy_allowed branch, edge cases at exact open/close minute.
- `/home/alex/Friendex/tests/domain/test_fund_math.py` — monthly vs annual accrual; effective APY with expired penalty (penalty ignored), active penalty (subtracted), no penalty; net worth zero-position; net worth with mixed long/short.
- `/home/alex/Friendex/tests/domain/conftest.py` — fixture `frozen_now` via `freezegun` returning a fixed `datetime`; fixture `default_settings` builds a `Settings()` from a static dict.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/domain/ tests/domain/
uv run mypy src/friendex/domain/
uv run pytest tests/domain/ -v --cov=src/friendex/domain --cov-fail-under=95
```

**Commit boundary guidance:** Five commits, one per domain file, then one consolidating test commit if needed. Each module + its tests can be committed atomically because they have no inter-module dependencies beyond `models.py`.

**References:** `Refs #<master-id>`.

---

## Phase 5 — Persistence: ORM & Alembic Baseline

**Goal:** Define SQLAlchemy 2.0 declarative ORM mirrors of every domain model, create the Alembic baseline migration, and prove `Base.metadata.create_all()` produces the expected schema.

**Branch name:** `feat/phase-5-orm`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/persistence/db.py` — `create_async_engine(settings.database_url)`, `async_sessionmaker`, declarative `Base` class.
- `/home/alex/Friendex/src/friendex/adapters/persistence/orm.py` — every table from `02-target-architecture.md` §Persistence Strategy Option B (`UserORM`, `LongPositionORM`, `ShortPositionORM`, `ActivityBucketORM`, `VoiceUniqueChannelORM`, `StockORM`, `PriceHistoryORM`, `HedgeFundORM`, `FundInvestorORM`, `FundPenaltyORM`, `SystemStateORM` for `last_daily_reset` / `last_weekly_reset`, `TradeCooldownORM`). Each ORM class includes `to_domain()` and `from_domain()` helpers — but those mappers live next to the ORM rather than in the repository, so repositories stay thin.
- `/home/alex/Friendex/alembic.ini` — points at `alembic/`, `script_location = alembic`, `sqlalchemy.url = ${DATABASE_URL}`.
- `/home/alex/Friendex/alembic/env.py` — async-aware env config that reads `DATABASE_URL` from `os.environ`, imports `Base` from `db.py`, sets `target_metadata = Base.metadata`.
- `/home/alex/Friendex/alembic/script.py.mako` — default Alembic template.
- `/home/alex/Friendex/alembic/versions/0001_baseline.py` — `upgrade()` creates every table; `downgrade()` drops every table.
- `/home/alex/Friendex/tests/adapters/persistence/__init__.py`
- `/home/alex/Friendex/tests/adapters/persistence/test_orm.py` — uses an in-memory SQLite (`sqlite+aiosqlite:///:memory:`); asserts `Base.metadata.create_all` runs; asserts a row can be inserted and round-tripped through `to_domain()` / `from_domain()` for each model.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/persistence/ tests/adapters/persistence/ alembic/
uv run mypy src/friendex/adapters/persistence/
uv run pytest tests/adapters/persistence/test_orm.py -v
DATABASE_URL=sqlite+aiosqlite:///tmp/alembic-check.db uv run alembic upgrade head
DATABASE_URL=sqlite+aiosqlite:///tmp/alembic-check.db uv run alembic downgrade base
rm -f /tmp/alembic-check.db
```
Last two prove the baseline migration is reversible.

**Commit boundary guidance:** Three commits — (1) `feat(persistence): SQLAlchemy declarative base and engine`, (2) `feat(persistence): ORM models with domain mappers`, (3) `feat(persistence): alembic baseline migration`.

**References:** `Refs #<master-id>`.

---

## Phase 6 — Persistence: Repositories & JSON Migrator

**Goal:** Implement the `IUserRepo` / `IPriceRepo` / `IFundRepo` / `IPenaltyRepo` interfaces and their SQLAlchemy-backed implementations; provide the one-shot JSON-to-SQLite migration script with tests against synthetic fixtures. Also activates SQLite FK enforcement (decided in [ADR-0002](./adr/0002-sqlite-fk-enforcement.md)) and carries forward two non-blocking findings from the Phase 5 review (Decimal quantisation assertions + first real migration drift test).

**Branch name:** `feat/phase-6-repos`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/interfaces.py` — abstract `Protocol` classes `IUserRepo`, `IPriceRepo`, `IFundRepo`, `IPenaltyRepo`, `ITradeCooldownRepo`, `ISystemStateRepo`. Each declares `get`, `upsert`, `delete`, `list_all`, plus model-specific methods (`IPriceRepo.append_history`, `IPriceRepo.prune_history_older_than`, `IUserRepo.list_active_in_last(seconds)`, etc.). No imports from `adapters`.
- `/home/alex/Friendex/src/friendex/adapters/persistence/user_repo.py` — `SqlUserRepository` implements `IUserRepo` against `UserORM` + `LongPositionORM` + `ShortPositionORM` + `ActivityBucketORM` + `VoiceUniqueChannelORM`.
- `/home/alex/Friendex/src/friendex/adapters/persistence/price_repo.py` — `SqlPriceRepository`; `prune_history_older_than` uses a single `DELETE WHERE recorded_at < ?`.
- `/home/alex/Friendex/src/friendex/adapters/persistence/fund_repo.py` — `SqlFundRepository`; `ensure_events_wallet()` is idempotent.
- `/home/alex/Friendex/src/friendex/adapters/persistence/penalty_repo.py` — `SqlPenaltyRepository`.
- `/home/alex/Friendex/src/friendex/adapters/persistence/cooldown_repo.py` — `SqlTradeCooldownRepository`; reads/writes `TradeCooldownORM` with TTL via `expires_at`.
- `/home/alex/Friendex/src/friendex/adapters/persistence/system_state_repo.py` — `SqlSystemStateRepository`; holds the single-row state for `last_daily_reset` / `last_weekly_reset`.
- `/home/alex/Friendex/src/friendex/adapters/persistence/migrate_json_to_sqlite.py` — CLI entry point `python -m friendex.adapters.persistence.migrate_json_to_sqlite --source data/ --target sqlite+aiosqlite:///data/friendex.db`. Idempotent via `session.merge()`. Logs row counts per table.
- `/home/alex/Friendex/tests/adapters/persistence/test_user_repo.py` — round-trip `UserAccount` with long+short positions and both activity buckets; assert deletion cascades to positions.
- `/home/alex/Friendex/tests/adapters/persistence/test_price_repo.py` — append history; prune retains only records inside the window.
- `/home/alex/Friendex/tests/adapters/persistence/test_fund_repo.py` — events wallet idempotency; investor add/remove.
- `/home/alex/Friendex/tests/adapters/persistence/test_penalty_repo.py` — penalty insert + read; expired penalty handling.
- `/home/alex/Friendex/tests/adapters/persistence/test_cooldown_repo.py` — TTL semantics (expired rows excluded from `get`).
- `/home/alex/Friendex/tests/adapters/persistence/test_migrate_json.py` — synthetic JSON fixtures in `tests/fixtures/json/` (`users.json`, `prices.json`, `funds.json`, `fund_penalties.json`); migrator produces expected row counts and round-trips each record; second run is idempotent (no duplicates).
- `/home/alex/Friendex/tests/fixtures/json/{users,prices,funds,fund_penalties}.json` — small (5–10 record) test fixtures.

**Files modified:**
- `/home/alex/Friendex/src/friendex/adapters/persistence/db.py` — add `@event.listens_for(engine.sync_engine, "connect")` listener that runs `PRAGMA foreign_keys=ON` on every connection (see [ADR-0002](./adr/0002-sqlite-fk-enforcement.md)).
- `/home/alex/Friendex/src/friendex/adapters/persistence/__init__.py` — re-export repo classes.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/persistence/ src/friendex/application/interfaces.py tests/adapters/persistence/
uv run mypy src/friendex/application/interfaces.py src/friendex/adapters/persistence/
uv run pytest tests/adapters/persistence/ -v --cov=src/friendex/adapters/persistence --cov-fail-under=85
```

**Alembic migration (Phase 6):** add `ON DELETE CASCADE` to all child FK columns so the DB-level enforcement from the PRAGMA listener fires correctly on parent deletes.

**Carry-forward from Phase 5 review (both non-blocking, resolve here):**
- MEDIUM: add Decimal quantisation assertions to ORM tests — at minimum one `assert result.<field>.as_tuple().exponent == Decimal('…').as_tuple().exponent` per `DecimalText` column, plus one fixture value SQLite-float cannot represent exactly so a `Numeric`-type regression goes RED.
- LOW: once this phase introduces the first hand-authored incremental migration, add a `compare_metadata`-based (or explicit-DDL) drift test so the column-level check is no longer tautological.

**Commit boundary guidance:** Seven commits — (1) `feat(persistence): PRAGMA foreign_keys=ON + ON DELETE CASCADE migration`, (2) `feat(application): repository protocol interfaces`, (3-6) `feat(persistence): <X>Repository`, one per repo, (7) `feat(persistence): json-to-sqlite migrator + fixtures`.

**References:** `Refs #<master-id>`.

---

## Phase 7 — Concurrency Primitives

**Goal:** Implement `LockManager` exactly as specified in the target architecture, with the inlined `locked()` pattern from Phase 3a correction (no public `acquire()`).

**Branch name:** `feat/phase-7-locks`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/lock_manager.py` — `LockManager` class with a single public `locked(*user_ids)` async context manager. Lock acquisition order is sorted by `user_id` to prevent deadlock. Private `_ensure_lock(uid)` creates locks under the meta lock.
- `/home/alex/Friendex/tests/application/__init__.py`
- `/home/alex/Friendex/tests/application/test_lock_manager.py` — covers: (a) two `locked(uid)` contexts on the same user serialize; (b) `locked(a, b)` and `locked(b, a)` produce the same acquisition order (deadlock prevention proof — run both concurrently and assert no timeout); (c) reentrant attempt on the same user blocks (timeout test); (d) two different users do not block each other.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/application/lock_manager.py tests/application/test_lock_manager.py
uv run mypy src/friendex/application/lock_manager.py
uv run pytest tests/application/test_lock_manager.py -v --cov=src/friendex/application/lock_manager --cov-fail-under=95
```

**Commit boundary guidance:** Two commits — (1) `feat(application): per-user asyncio lock manager`, (2) `test(application): lock manager deadlock safety`.

**References:** `Refs #<master-id>`.

---

## Phase 8a — Activity & Voice Ping Services

**Goal:** First application services. `ActivityService` records messages, reactions, voice joins/leaves into the today + week buckets; `VoicePingService` handles VC ping detection and responder bonuses. All side effects go through repositories under per-user locks.

**Branch name:** `feat/phase-8a-activity`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/activity_service.py` — `ActivityService` class; constructor takes `user_repo: IUserRepo`, `price_repo: IPriceRepo`, `lock_manager: LockManager`, `settings: Settings`. Methods: `record_message(author_id, has_attachment, is_reply, channel_id)`, `record_reaction(user_id)`, `handle_voice_join(user_id, channel_id, joined_from_ping)`, `handle_voice_leave(user_id, channel_id, stay_minutes, joined_from_ping)`, `mark_intro_shown(user_id)`, `reset_today_buckets()`, `reset_week_buckets()`, `set_opt_in(user_id, value)`.
- `/home/alex/Friendex/src/friendex/application/voice_ping_service.py` — `VoicePingService` class; constructor takes `user_repo`, `price_repo`, `lock_manager`, `settings`, and an in-memory `VoicePingSessionStore` (just a dict guarded by `asyncio.Lock` since this state is intentionally volatile per the spec). Methods: `register_ping_message(message_id, host_id, channel_id, timestamp)`, `reward_voice_ping_response(responder_id, channel_id, now)`, `cleanup_expired_pings(now)`.
- `/home/alex/Friendex/src/friendex/application/voice_session_store.py` — `VoiceSessionStore` and `VoicePingSessionStore` thin wrappers around dicts, used by `activity_service` and `voice_ping_service`. State is volatile (matches Phase 1 design).
- `/home/alex/Friendex/tests/application/test_activity_service.py` — covers: text msg increments `today.text_msgs` and `week.text_msgs`; media msg increments media; photo-bonus channel grants role_ping bonus; reply increments reply_count; voice leave with stay_minutes >= 60 applies 50% price boost; `reset_today_buckets` resets only today, not week.
- `/home/alex/Friendex/tests/application/test_voice_ping_service.py` — covers: first 10 joiners get fast/medium/slow tier bonuses; 11th+ joiner is added to `extra_joiners`; expired pings are evicted by cleanup; reward is idempotent (joining twice doesn't double-pay).

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/application/activity_service.py src/friendex/application/voice_ping_service.py src/friendex/application/voice_session_store.py tests/application/
uv run mypy src/friendex/application/activity_service.py src/friendex/application/voice_ping_service.py
uv run pytest tests/application/test_activity_service.py tests/application/test_voice_ping_service.py -v --cov=src/friendex/application --cov-fail-under=85
```

**Commit boundary guidance:** Four commits — (1) `feat(application): voice session in-memory stores`, (2) `feat(application): activity service`, (3) `feat(application): voice ping service`, (4) `test(application): activity + voice ping`.

**References:** `Refs #<master-id>`.

---

## Phase 8b — Price Tick Service

**Goal:** Service that the activity-tick and inactivity-decay tasks call. Pure orchestrator over `price_engine` + repositories.

**Branch name:** `feat/phase-8b-price-tick`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/price_tick_service.py` — `PriceTickService`; methods `activity_price_tick()`, `inactivity_decay_tick()`, `vc_boost_tick()`. The `reset_24h_high_low()` method is **omitted** per Phase 3a correction 4 (high_24h/low_24h are computed dynamically from history, not stored).
- `/home/alex/Friendex/tests/application/test_price_tick_service.py` — covers: activity tick raises price for active user, lowers price for under-engaged user; inactivity decay applies only after threshold; vc_boost_tick only boosts users still in voice; min-price floor is enforced through every path.
- `/home/alex/Friendex/tests/application/fakes/__init__.py`
- `/home/alex/Friendex/tests/application/fakes/fake_repos.py` — in-memory implementations of every `I*Repo`, used as test doubles by Phase 8a–8f tests. Each fake supports the same surface as the SQLAlchemy implementations.

**Files modified:**
- `/home/alex/Friendex/tests/application/conftest.py` — add fixtures `fake_user_repo`, `fake_price_repo`, `fake_fund_repo`, `fake_penalty_repo`, `fake_cooldown_repo`, `fake_system_state_repo`, `lock_manager`, `default_settings`.

**Verification gate:**
```bash
uv run ruff check src/friendex/application/price_tick_service.py tests/application/
uv run mypy src/friendex/application/price_tick_service.py
uv run pytest tests/application/test_price_tick_service.py -v
```

**Commit boundary guidance:** Three commits — (1) `test(application): in-memory fake repos`, (2) `feat(application): price tick service`, (3) `test(application): price tick coverage`.

**References:** `Refs #<master-id>`.

---

## Phase 8c — Trading Service

**Goal:** `/buy`, `/sell`, `/short`, `/cover` use cases. The single most complex service. Acquires locks for both author and target; enforces market hours, opt-in, collateral, cooldown, freeze.

**Branch name:** `feat/phase-8c-trading`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/trading_service.py` — `TradingService`; constructor takes `user_repo`, `price_repo`, `fund_repo`, `cooldown_repo`, `lock_manager`, `settings`. Methods: `buy(buyer_id, target_id, shares)`, `sell(seller_id, target_id, shares)`, `short(shorter_id, target_id, shares)`, `cover(coverer_id, target_id, shares)`, `update_frozen_shorts()`. Each returns a typed result dataclass (`BuyResult`, `SellResult`, `ShortResult`, `CoverResult`) consumed by the embed builder.
- `/home/alex/Friendex/src/friendex/application/trade_results.py` — result dataclasses (frozen=True).
- `/home/alex/Friendex/tests/application/test_trading_service.py` — exhaustive: happy paths for all four ops; `InsufficientFunds` raised when cash short; `OptedOut` when target.opt_in=False; `MarketClosed` outside hours (with Sunday-buy exception verified); `SelfTrade` blocked; `OnCooldown` for short/cover within `trade_cooldown_seconds`; `PositionFrozen` blocks manual cover but is bypassed by `LiquidationService` (covered in 8f); collateral correctly split between cash and fund; weighted average entry recalculated on add; position deleted when shares hit zero.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/application/trading_service.py src/friendex/application/trade_results.py tests/application/test_trading_service.py
uv run mypy src/friendex/application/trading_service.py
uv run pytest tests/application/test_trading_service.py -v --cov=src/friendex/application/trading_service --cov-fail-under=90
```

**Commit boundary guidance:** Four commits — (1) `feat(application): trade result dataclasses`, (2) `feat(application): trading service buy + sell`, (3) `feat(application): trading service short + cover + freeze`, (4) `test(application): trading service`.

**References:** `Refs #<master-id>`.

---

## Phase 8d — Portfolio & Stats Services

**Goal:** Read-only services for `/portfolio`, `/balance`, `/trending`, `/mystats`, `/price`. No mutations, no locks.

**Branch name:** `feat/phase-8d-portfolio`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/portfolio_service.py` — `PortfolioService`; methods `calculate_net_worth(user_id)`, `portfolio_snapshot(user_id)`, `capture_month_start_net_worth()`. All purely-functional over `fund_math.compute_net_worth`.
- `/home/alex/Friendex/src/friendex/application/stats_service.py` — `StatsService`; methods `trending_snapshot(limit=15)`, `user_stats(user_id)`, `get_price_stats(user_id)` (computes 24h high/low dynamically from history per §Open-Q9 decision).
- `/home/alex/Friendex/src/friendex/application/snapshot_models.py` — read-model dataclasses (`PortfolioSnapshot`, `TrendingEntry`, `PriceStats`, `UserStats`). Distinct from domain models — these are tailored to embed builders.
- `/home/alex/Friendex/tests/application/test_portfolio_service.py` — net worth with long-only, short-only, mixed, frozen-only; month-start rollover captures correctly.
- `/home/alex/Friendex/tests/application/test_stats_service.py` — trending sorts descending, filters zero scores, slices to 15; 24h high/low computed from history window; engagement tier coverage.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/application/portfolio_service.py src/friendex/application/stats_service.py src/friendex/application/snapshot_models.py tests/application/
uv run mypy src/friendex/application/portfolio_service.py src/friendex/application/stats_service.py
uv run pytest tests/application/test_portfolio_service.py tests/application/test_stats_service.py -v
```

**Commit boundary guidance:** Three commits — (1) `feat(application): snapshot read models`, (2) `feat(application): portfolio + stats services`, (3) `test(application): portfolio + stats`.

**References:** `Refs #<master-id>`.

---

## Phase 8e — Fund & Daily Services

**Goal:** Hedge fund (create/info/withdraw/send_events) and daily reward.

**Branch name:** `feat/phase-8e-fund-daily`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/fund_service.py` — `FundService`; methods `create_or_rename(user_id, name=None)`, `withdraw(user_id, amount, now)`, `send_to_events(user_id, amount)`, `fund_info(user_id)`, `accrue_apy(now)` (called by monthly rollover), and a scaffolded `invest(investor_id, fund_id, amount)` that `raise NotImplementedError` per §Open-Q5.
- `/home/alex/Friendex/src/friendex/application/daily_service.py` — `DailyService`; method `claim_daily(user_id, now)` returning a typed `DailyClaimResult` (with `streak`, `reward`, `is_streak_bonus`).
- `/home/alex/Friendex/src/friendex/application/daily_result.py` — result dataclass.
- `/home/alex/Friendex/tests/application/test_fund_service.py` — create, rename, withdraw with day-1-no-penalty branch, withdraw mid-month applies penalty, send_to_events skips penalty, accrue_apy increases balance by monthly amount, `invest` raises `NotImplementedError`.
- `/home/alex/Friendex/tests/application/test_daily_service.py` — first claim, second claim same day rejected, next day continues streak, day 7 grants `streak_bonus` and resets streak.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/application/fund_service.py src/friendex/application/daily_service.py src/friendex/application/daily_result.py tests/application/
uv run mypy src/friendex/application/fund_service.py src/friendex/application/daily_service.py
uv run pytest tests/application/test_fund_service.py tests/application/test_daily_service.py -v
```

**Commit boundary guidance:** Three commits — (1) `feat(application): fund service`, (2) `feat(application): daily service`, (3) `test(application): fund + daily`.

**References:** `Refs #<master-id>`.

---

## Phase 8f — Liquidation & Discipline Services

**Goal:** Implement what the spec left as stubs. Liquidation auto-covers shorts at 150% of entry. Discipline applies 17% price drop on timeout or ban.

**Branch name:** `feat/phase-8f-liq-disc`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/liquidation_service.py` — `LiquidationService`; method `check_and_liquidate_shorts(now)` iterates all shorts, locks holder+target, executes cover via `TradingService.cover()` with an internal `force=True` flag bypassing the `PositionFrozen` check, returns a list of `LiquidationEvent` for notification.
- `/home/alex/Friendex/src/friendex/application/discipline_service.py` — `DisciplineService`; method `apply_discipline_penalty(user_id, reason: Literal["timeout", "ban"])` drops the user's stock by `settings.discipline_penalty`.
- `/home/alex/Friendex/src/friendex/application/liquidation_events.py` — `LiquidationEvent` dataclass.
- `/home/alex/Friendex/tests/application/test_liquidation_service.py` — short at 149% not liquidated; short at 150% liquidated; frozen short still liquidated; liquidation event payload correctness.
- `/home/alex/Friendex/tests/application/test_discipline_service.py` — timeout drops price by 17%, ban drops price by 17%, floor enforced at min_price, opt-out users still affected (their stock still trades).

**Files modified:**
- `/home/alex/Friendex/src/friendex/application/trading_service.py` — add private `_cover_internal(force: bool)` so `LiquidationService` can bypass frozen check without exposing `force` in the public API.

**Verification gate:**
```bash
uv run ruff check src/friendex/application/liquidation_service.py src/friendex/application/discipline_service.py src/friendex/application/liquidation_events.py src/friendex/application/trading_service.py tests/application/
uv run mypy src/friendex/application/
uv run pytest tests/application/ -v --cov=src/friendex/application --cov-fail-under=85
```
Last pytest invocation runs ALL application tests to confirm 8f's changes to `trading_service` did not regress 8c.

**Commit boundary guidance:** Four commits — (1) `feat(application): expose internal cover for liquidation`, (2) `feat(application): liquidation service`, (3) `feat(application): discipline service`, (4) `test(application): liquidation + discipline`.

**References:** `Refs #<master-id>`.

---

## Phase 9 — Background Tasks

**Goal:** All eight task classes in `adapters/tasks/`. Each is a thin wrapper around a service method that logs errors and never propagates them to the event loop. Implements the two missing reset tasks and fully completes the liquidation and monthly-rollover tasks.

**Branch name:** `feat/phase-9-tasks`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/tasks/base_task.py` — `BackgroundTask` abstract base with `start()`, `stop()`, and an error-wrapping `_safe_run()` helper.
- `/home/alex/Friendex/src/friendex/adapters/tasks/activity_tick_task.py` — 15-min, calls `PriceTickService.activity_price_tick`.
- `/home/alex/Friendex/src/friendex/adapters/tasks/inactivity_decay_task.py` — 5-min, calls `PriceTickService.inactivity_decay_tick`.
- `/home/alex/Friendex/src/friendex/adapters/tasks/liquidation_task.py` — 5-min, calls `LiquidationService.check_and_liquidate_shorts`; emits a Discord notification per event (callback injected by container so the task itself doesn't import `discord`).
- `/home/alex/Friendex/src/friendex/adapters/tasks/freeze_check_task.py` — 5-min, calls `TradingService.update_frozen_shorts`.
- `/home/alex/Friendex/src/friendex/adapters/tasks/vc_boost_task.py` — 15-min, calls `PriceTickService.vc_boost_tick`.
- `/home/alex/Friendex/src/friendex/adapters/tasks/daily_reset_task.py` — 1-min cadence; reads `SystemStateRepository.last_daily_reset`; if `utcnow().date() > last_daily_reset`, calls `ActivityService.reset_today_buckets` and updates state.
- `/home/alex/Friendex/src/friendex/adapters/tasks/weekly_reset_task.py` — 1-min cadence; same pattern keyed on `utcnow().weekday() == 0` and `last_weekly_reset`.
- `/home/alex/Friendex/src/friendex/adapters/tasks/monthly_rollover_task.py` — 1-hour cadence; on 1st of month at hour 0, calls `PortfolioService.capture_month_start_net_worth` and `FundService.accrue_apy(now)`.
- `/home/alex/Friendex/tests/adapters/tasks/__init__.py`
- `/home/alex/Friendex/tests/adapters/tasks/test_activity_tick_task.py` — task swallows service exceptions; service is called at the right cadence (use `discord.ext.tasks` test helpers or a manual `_safe_run` call).
- `/home/alex/Friendex/tests/adapters/tasks/test_inactivity_decay_task.py`
- `/home/alex/Friendex/tests/adapters/tasks/test_liquidation_task.py` — notification callback receives every `LiquidationEvent`.
- `/home/alex/Friendex/tests/adapters/tasks/test_freeze_check_task.py`
- `/home/alex/Friendex/tests/adapters/tasks/test_vc_boost_task.py`
- `/home/alex/Friendex/tests/adapters/tasks/test_daily_reset_task.py` — task fires exactly once per UTC date; `freezegun` advances time across the boundary.
- `/home/alex/Friendex/tests/adapters/tasks/test_weekly_reset_task.py` — task fires exactly once per ISO week.
- `/home/alex/Friendex/tests/adapters/tasks/test_monthly_rollover_task.py` — task fires on day 1 at hour 0 only; APY accrual called.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/tasks/ tests/adapters/tasks/
uv run mypy src/friendex/adapters/tasks/
uv run pytest tests/adapters/tasks/ -v --cov=src/friendex/adapters/tasks --cov-fail-under=80
```

**Commit boundary guidance:** Eight commits, one per task file with its test, plus a base-class commit first.

**References:** `Refs #<master-id>`.

---

## Phase 10 — Discord Embed Builders

**Goal:** Pure functions that take service result dataclasses and return `discord.Embed`. No bot state, no I/O — trivially testable.

**Branch name:** `feat/phase-10-embeds`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/discord_bot/embeds.py` — `build_balance_embed`, `build_daily_embed`, `build_price_embed`, `build_buy_confirmation_embed`, `build_sell_confirmation_embed`, `build_short_confirmation_embed`, `build_cover_confirmation_embed`, `build_portfolio_embed`, `build_trending_embed`, `build_mystats_embed`, `build_fund_info_embed`, `build_intro_embed`, `build_help_embed`, `build_liquidation_notification_embed`, `build_error_embed(error: DomainError)`.
- `/home/alex/Friendex/tests/adapters/discord_bot/__init__.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/test_embeds.py` — for each builder: title is set, description has expected fields, color matches semantic (green=success, red=error, orange=warning), field count matches spec layout. Uses `discord.Embed.to_dict()` for structural assertions without needing a live bot.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/discord_bot/embeds.py tests/adapters/discord_bot/test_embeds.py
uv run mypy src/friendex/adapters/discord_bot/embeds.py
uv run pytest tests/adapters/discord_bot/test_embeds.py -v --cov=src/friendex/adapters/discord_bot/embeds --cov-fail-under=95
```

**Commit boundary guidance:** Two commits — (1) `feat(discord): embed builders`, (2) `test(discord): embed structure`.

**References:** `Refs #<master-id>`.

---

## Phase 11 — Discord Cogs

**Goal:** One commit per cog. Each cog calls services and embed builders only.

**Branch name:** `feat/phase-11-cogs`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/trading_cog.py` — `/buy`, `/sell`, `/short`, `/cover` (public replies).
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/portfolio_cog.py` — `/portfolio` (ephemeral).
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/fund_cog.py` — `/fund create/info/withdraw/send_events/invest` as an `app_commands.Group` (`invest` raises `NotImplementedError` → caught by error handler).
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/daily_cog.py` — `/daily` (public).
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/stats_cog.py` — `/trending` (public); `/mystats`, `/price`, `/mystock` (ephemeral). Aliases `$ticker`/`$my_stock` collapse into `/price` and `/mystock`.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/account_cog.py` — `/balance`, `/optin`, `/optout` (ephemeral).
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/admin_cog.py` — `/game_intro` (manage_guild check), `/help`.
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/__init__.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/test_trading_cog.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/test_portfolio_cog.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/test_fund_cog.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/test_daily_cog.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/test_stats_cog.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/test_account_cog.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/test_admin_cog.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/cogs/conftest.py` — fixtures providing a fake `discord.Interaction` (`AsyncMock`, with `response`/`followup` mocked) and mock application services from `tests/application/fakes/`. Slash-command tests invoke each cog's callback directly (e.g. `await TradingCog.buy.callback(cog, interaction, user=..., shares=...)`) and assert on `interaction.response`/`interaction.followup`. `dpytest` is **not** used for cogs because it simulates message events, not slash interactions.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/discord_bot/cogs/ tests/adapters/discord_bot/cogs/
uv run mypy src/friendex/adapters/discord_bot/cogs/
uv run pytest tests/adapters/discord_bot/cogs/ -v --cov=src/friendex/adapters/discord_bot/cogs --cov-fail-under=80
```

**Commit boundary guidance:** Seven commits, one per cog + its test file. Each cog/test pair is independently functional, so this phase is the most parallelisable across commits.

**References:** `Refs #<master-id>`.

---

## Phase 12 — Discord Listeners

**Goal:** Four listeners (`on_message`, `on_voice_state_update`, `on_reaction_add`, `on_member_update`/`on_member_ban`). Each delegates to a service.

**Branch name:** `feat/phase-12-listeners`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/discord_bot/listeners/message_listener.py` — `MessageListener(commands.Cog)`; `on_message` calls `ActivityService.record_message` and `VoicePingService.register_ping_message` if the message is a VC ping. No `bot.process_commands` call — commands are slash commands dispatched by the command tree, so `on_message` only records activity.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/listeners/voice_listener.py` — `VoiceListener`; `on_voice_state_update` calls `ActivityService.handle_voice_join` / `handle_voice_leave` and `VoicePingService.reward_voice_ping_response`.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/listeners/reaction_listener.py` — `ReactionListener`; `on_reaction_add`.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/listeners/member_listener.py` — `MemberListener`; `on_member_update` (timeout detection), `on_member_ban`.
- `/home/alex/Friendex/tests/adapters/discord_bot/listeners/__init__.py`
- `/home/alex/Friendex/tests/adapters/discord_bot/listeners/test_message_listener.py` — text message routes correctly; bot messages ignored; VC ping detection routes to `VoicePingService`; reply detection.
- `/home/alex/Friendex/tests/adapters/discord_bot/listeners/test_voice_listener.py` — join/leave/switch routing; finalize-old-then-create-new on channel switch.
- `/home/alex/Friendex/tests/adapters/discord_bot/listeners/test_reaction_listener.py` — reaction routes; self-reaction ignored.
- `/home/alex/Friendex/tests/adapters/discord_bot/listeners/test_member_listener.py` — `timed_out_until` newly-set triggers discipline; un-timeout doesn't fire; ban fires.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/discord_bot/listeners/ tests/adapters/discord_bot/listeners/
uv run mypy src/friendex/adapters/discord_bot/listeners/
uv run pytest tests/adapters/discord_bot/listeners/ -v --cov=src/friendex/adapters/discord_bot/listeners --cov-fail-under=80
```

**Commit boundary guidance:** Four commits, one per listener + test.

**References:** `Refs #<master-id>`.

---

## Phase 13 — Error Handler & Container Wiring

**Goal:** Top-level `on_command_error` handler and the dependency container that constructs every repo, service, listener, cog, and task in the correct order.

**Branch name:** `feat/phase-13-container`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/discord_bot/error_handler.py` — `register_error_handler(bot, settings)` registers `on_command_error`. Maps `DomainError` → embed with `user_facing_message`; `PersistenceError` → log + generic message; `MissingRequiredArgument` → usage hint; `MemberNotFound` → "User not found"; fallthrough `Exception` → log CRITICAL + generic message.
- `/home/alex/Friendex/src/friendex/adapters/container.py` — `Container` class; constructor takes `Settings` and an `async_sessionmaker`. Builds repos, then `LockManager`, then every service in dependency order, then registers cogs, listeners, tasks, and the error handler with a passed-in `bot`.
- `/home/alex/Friendex/src/friendex/main.py` — `async def amain()` entry that loads settings, configures logging, creates engine + sessionmaker, builds container, builds bot, runs `bot.start(settings.discord_token)`. CLI: `python -m friendex`.
- `/home/alex/Friendex/tests/adapters/discord_bot/test_error_handler.py` — `DomainError` → user-friendly embed; `PersistenceError` → generic message + log line; unknown exception → log line + generic message.
- `/home/alex/Friendex/tests/adapters/test_container.py` — container wires the full graph without raising; every cog and listener is registered; every task is created (but not started, since starting requires a live event loop).

**Files modified:**
- `/home/alex/Friendex/src/friendex/__init__.py` — re-export `main` for `python -m friendex`.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/container.py src/friendex/adapters/discord_bot/error_handler.py src/friendex/main.py tests/
uv run mypy src/friendex/adapters/container.py src/friendex/adapters/discord_bot/error_handler.py src/friendex/main.py
uv run pytest tests/adapters/discord_bot/test_error_handler.py tests/adapters/test_container.py -v
```

**Commit boundary guidance:** Three commits — (1) `feat(discord): error handler`, (2) `feat(adapters): dependency container`, (3) `feat: main entry point`.

**References:** `Refs #<master-id>`.

---

## Phase 14 — Bot Factory & Entry Point

**Goal:** The `discord.Bot` instance with intents and a `setup_hook` that starts all background tasks **and syncs the slash-command tree to the home guild**. Smoke test the full bot launches against `dpytest`. (Per Phase 3a correction 1, tasks start in `setup_hook`, not `on_ready`.)

**Branch name:** `feat/phase-14-bot-factory`

**Files created:**

- `/home/alex/Friendex/src/friendex/adapters/discord_bot/bot.py` — `build_bot(settings, container)` constructs `commands.Bot(command_prefix=commands.when_mentioned, intents=discord.Intents.all())` (no prefix commands — `command_prefix` is required by the API but inert). Registers a `setup_hook` that starts every task class on the container, then syncs the slash-command tree to the home guild for instant availability (`bot.tree.copy_global_to(guild=discord.Object(settings.guild_id))` followed by `await bot.tree.sync(guild=discord.Object(settings.guild_id))`).
- `/home/alex/Friendex/tests/adapters/discord_bot/test_bot_factory.py` — `dpytest`-based smoke test: build bot, run `setup_hook`, assert every task's `is_running()` is `True`, assert every cog is in `bot.cogs`, assert every listener is registered, then `await bot.close()`.
- `/home/alex/Friendex/tests/integration/__init__.py`
- `/home/alex/Friendex/tests/integration/test_full_command_flow.py` — end-to-end: build the bot against an in-memory SQLite + fake Discord environment; invoke `/daily` and observe the embed; invoke `/buy` with `user=target, shares=1` and observe the embed; invoke `/portfolio` and confirm the position appears.

**Files modified:** none.

**Verification gate:**
```bash
uv run ruff check src/friendex/adapters/discord_bot/bot.py tests/adapters/discord_bot/test_bot_factory.py tests/integration/
uv run mypy src/friendex/adapters/discord_bot/bot.py
uv run pytest tests/adapters/discord_bot/test_bot_factory.py tests/integration/ -v
```

**Commit boundary guidance:** Two commits — (1) `feat(discord): bot factory and setup_hook`, (2) `test(integration): full command flow smoke test`.

**References:** `Refs #<master-id>`.

---

## Phase 15 — JSON-to-SQLite Migration Verification

**Goal:** Since there is no real production JSON data (greenfield), verify the migrator with synthetic fixtures simulating a "live" data set and prove idempotency. This is the gate that gives confidence to ever point the bot at real data.

**Branch name:** `feat/phase-15-migrate-verify`

**Files created:**

- `/home/alex/Friendex/tests/fixtures/json/realistic/users.json` — 50 users with mixed long/short positions, both activity buckets populated, daily streaks at various stages.
- `/home/alex/Friendex/tests/fixtures/json/realistic/prices.json` — 50 stocks with 24-hour history.
- `/home/alex/Friendex/tests/fixtures/json/realistic/funds.json` — 30 funds plus the `events_wallet` pseudo-fund.
- `/home/alex/Friendex/tests/fixtures/json/realistic/fund_penalties.json` — 10 active penalties at various stages of expiry.
- `/home/alex/Friendex/tests/integration/test_migration_realistic.py` — runs the migrator against the realistic fixtures, then runs every read-side service method and asserts results match expectations derived from the source JSON. Re-runs the migrator a second time and asserts row counts are unchanged (idempotency).

**Files modified:**
- `/home/alex/Friendex/src/friendex/adapters/persistence/migrate_json_to_sqlite.py` — add a `--dry-run` flag and a `--report` flag that prints row counts per table. Add a post-migration consistency check: every `LongPosition.target_user_id` must exist as a `UserAccount`; warn (don't fail) on orphans.

**Verification gate:**
```bash
uv run ruff check tests/integration/test_migration_realistic.py src/friendex/adapters/persistence/migrate_json_to_sqlite.py
uv run mypy src/friendex/adapters/persistence/migrate_json_to_sqlite.py
uv run pytest tests/integration/test_migration_realistic.py -v
# manual: dry-run against fixtures, print should match expected counts
uv run python -m friendex.adapters.persistence.migrate_json_to_sqlite \
  --source tests/fixtures/json/realistic/ \
  --target sqlite+aiosqlite:///:memory: \
  --dry-run --report
```

**Commit boundary guidance:** Two commits — (1) `test: realistic JSON migration fixtures`, (2) `feat(persistence): migrator dry-run and reporting`.

**References:** `Refs #<master-id>`.

---

## Phase 16 — Production Smoke Test (Cutover)

**Goal:** Since there is no existing `bot.py` to delete and no production JSON data, this phase becomes "verify the bot launches against a real Discord guild in a staging server and runs each command end-to-end." This is a manual checklist enforced by an automation runbook.

**Branch name:** `feat/phase-16-cutover`

**Files created:**

- `/home/alex/Friendex/docs/runbook-smoke-test.md` — step-by-step manual smoke test against a staging Discord guild. Each command from the spec is invoked and its expected embed is documented.
- `/home/alex/Friendex/scripts/smoke_test_commands.py` — script that prints the list of commands an operator must execute, in order, with expected outcomes — drives the checklist.

**Files modified:** none. (The hypothetical `bot.py` to delete never existed.)

**Verification gate:**
```bash
uv run ruff check scripts/
# manual: operator follows runbook against a staging guild; every checklist item must pass
# automated re-run of all earlier gates to confirm no regressions:
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest -v
```
All earlier gates must continue to pass. Operator signs off on the runbook checklist in the PR description.

**Commit boundary guidance:** Two commits — (1) `docs: smoke test runbook`, (2) `chore: smoke test driver script`.

**References:** `Refs #<master-id>`.

---

## Phase 17 — Hardening & Deferred Items

**Goal:** Implement everything the target architecture deferred: `/fund invest` (Open-Q5), full APY accrual to investors (Open-Q5/Q8), Sunday-buy confirmation (Open-Q2), hedge fund APY period confirmation (Open-Q8), intro distribution mechanism (Open-Q10), and any post-cutover bugs surfaced in Phase 16.

**Branch name:** `feat/phase-17-hardening`

**Files created:**

- `/home/alex/Friendex/src/friendex/application/invest_service.py` — extracted from `FundService` if `invest` logic grows large; otherwise `FundService.invest` is filled in directly.
- `/home/alex/Friendex/tests/application/test_fund_invest.py` — invest happy path, partial withdraw of investor stake, multiple investors split APY proportionally to invested_amount, manager cannot invest in own fund or rule TBD by product.

**Files modified:**

- `/home/alex/Friendex/src/friendex/application/fund_service.py` — fill in `invest()`; extend `accrue_apy()` to distribute to investors in proportion to `invested_amount`; honor `settings.hedge_fund_base_apy_period`.
- `/home/alex/Friendex/src/friendex/adapters/discord_bot/cogs/fund_cog.py` — `/fund invest manager:<member> amount:<float>` now functional.
- `/home/alex/Friendex/src/friendex/application/trading_service.py` — pass `sunday_buy_allowed=settings.sunday_buy_allowed` to `market_hours.is_market_open` only for `/buy`; confirm rule with product owner before merge.
- `/home/alex/Friendex/src/friendex/adapters/config.py` — add `sunday_buy_allowed: bool = True`, `hedge_fund_base_apy_period: Literal["monthly", "annual"] = "monthly"`, `opt_out_blocks_trading: bool = True` (already covered in Phase 2 if foresight applied).
- `/home/alex/Friendex/.env.example` — document the three new toggles.
- `/home/alex/Friendex/docs/runbook-smoke-test.md` — append the new invest test cases.

**Verification gate:**
```bash
uv run ruff check src/ tests/
uv run mypy src/
uv run pytest -v --cov=src --cov-fail-under=80
# manual: operator validates new /fund invest flow in staging
```

**Commit boundary guidance:** Five commits — (1) `feat(application): /fund invest`, (2) `feat(application): APY accrual to investors`, (3) `feat(config): sunday buy + APY period toggles`, (4) `docs: smoke runbook updates for invest`, (5) `test: invest coverage`.

**Closing keyword:** PR body uses `Closes #<master-id>` so merge auto-closes the master issue.

**References:** `Closes #<master-id>`.

---

## Verification Gate Matrix

Every phase PR must pass these commands locally and in CI before merge.

| Phase | `ruff check` | `mypy` | `pytest` | Other |
|------:|---|---|---|---|
| 0  | n/a | n/a | n/a | `gh issue view <id> --state OPEN` |
| 1  | `src/ tests/` | `src/` | `tests/test_scaffold.py` | `pre-commit run --all-files`; CI green |
| 2  | `src/friendex/adapters/config.py tests/adapters/` | `src/friendex/adapters/config.py` | `tests/adapters/test_config.py --cov-fail-under=90` | — |
| 3  | `src/friendex/domain/ tests/domain/` | `src/friendex/domain/` | `tests/domain/ --cov-fail-under=95` | — |
| 4  | `src/friendex/domain/ tests/domain/` | `src/friendex/domain/` | `tests/domain/ --cov-fail-under=95` | — |
| 5  | `src/friendex/adapters/persistence/ alembic/ tests/adapters/persistence/` | `src/friendex/adapters/persistence/` | `tests/adapters/persistence/test_orm.py` | `alembic upgrade head && alembic downgrade base` |
| 6  | `src/friendex/adapters/persistence/ src/friendex/application/interfaces.py tests/adapters/persistence/` | `src/friendex/application/interfaces.py src/friendex/adapters/persistence/` | `tests/adapters/persistence/ --cov-fail-under=85` | — |
| 7  | `src/friendex/application/lock_manager.py tests/application/test_lock_manager.py` | `src/friendex/application/lock_manager.py` | `tests/application/test_lock_manager.py --cov-fail-under=95` | — |
| 8a | `src/friendex/application/{activity,voice_ping}_service.py tests/application/` | `src/friendex/application/{activity,voice_ping}_service.py` | `tests/application/test_{activity,voice_ping}_service.py --cov-fail-under=85` | — |
| 8b | scoped | scoped | `tests/application/test_price_tick_service.py` | — |
| 8c | scoped | scoped | `tests/application/test_trading_service.py --cov-fail-under=90` | — |
| 8d | scoped | scoped | `tests/application/test_{portfolio,stats}_service.py` | — |
| 8e | scoped | scoped | `tests/application/test_{fund,daily}_service.py` | — |
| 8f | `src/friendex/application/ tests/application/` | `src/friendex/application/` | `tests/application/ --cov-fail-under=85` | — |
| 9  | `src/friendex/adapters/tasks/ tests/adapters/tasks/` | `src/friendex/adapters/tasks/` | `tests/adapters/tasks/ --cov-fail-under=80` | — |
| 10 | `src/friendex/adapters/discord_bot/embeds.py tests/adapters/discord_bot/test_embeds.py` | `src/friendex/adapters/discord_bot/embeds.py` | `tests/adapters/discord_bot/test_embeds.py --cov-fail-under=95` | — |
| 11 | `src/friendex/adapters/discord_bot/cogs/ tests/adapters/discord_bot/cogs/` | `src/friendex/adapters/discord_bot/cogs/` | `tests/adapters/discord_bot/cogs/ --cov-fail-under=80` | — |
| 12 | `src/friendex/adapters/discord_bot/listeners/ tests/adapters/discord_bot/listeners/` | `src/friendex/adapters/discord_bot/listeners/` | `tests/adapters/discord_bot/listeners/ --cov-fail-under=80` | — |
| 13 | scoped | scoped | `tests/adapters/discord_bot/test_error_handler.py tests/adapters/test_container.py` | — |
| 14 | scoped | scoped | `tests/adapters/discord_bot/test_bot_factory.py tests/integration/` | — |
| 15 | scoped | scoped | `tests/integration/test_migration_realistic.py` | dry-run migrator against fixtures |
| 16 | `src/ tests/` | `src/` | full suite | Operator-signed runbook checklist in PR description |
| 17 | `src/ tests/` | `src/` | full suite `--cov-fail-under=80` | Operator-signed runbook checklist |

"scoped" = lint/typecheck the files touched by the phase; the full-tree command is enforced by CI on every PR regardless.

---

## Risk & Rollback

**Independent revertibility.** Every phase is a single squash-merged PR onto `main`. Reverting any one phase is `gh pr revert <pr-number>` or `git revert -m 1 <merge-sha>` followed by a new revert PR. Phases are ordered so that reverting phase N invalidates only phases N+1 and later — the codebase remains importable and the earlier phases continue to pass their gates.

**Why the order matters for safe rollback:**
- Phases 1–7 introduce no game behaviour: a rollback is purely structural and leaves no broken commands.
- Phases 8a–8f deliver services with their own tests but no Discord surface: a rollback removes capability but doesn't break the live bot because nothing calls them yet (the bot isn't running on `main` until Phase 14).
- Phases 9–13 build incremental Discord surface; a rollback removes that surface but the bot will still launch with an empty cog set.
- Phase 14 is the first phase that produces a runnable bot. Reverting Phase 14 returns the repository to a "library, not a bot" state — safe.
- Phases 15–17 are post-launch; reverts are equivalent to disabling a feature flag.

**Deployment posture.**
- **No deployment against any real Discord guild until Phase 14 passes its integration smoke test.**
- **No deployment against any production data until Phase 15 verification passes** — the migrator must be proven idempotent and the realistic-fixture round-trip must succeed.
- Phase 16 is the controlled cutover against a staging guild; production deploy comes only after the operator's runbook checklist is signed in the PR.

**Regression containment.** Every phase PR runs the full `pytest` suite in CI (not just the scoped tests in its verification gate). A test that passes in phase N but fails in phase N+1 because of a refactor is caught at the PR merge gate before reaching `main`.

**Worktree-per-phase.** Per project rule, each phase is developed in its own `git worktree` under `.worktrees/<phase-slug>`. This isolates work-in-progress so an in-flight Phase 9 cannot accidentally bleed into a Phase 10 PR. After merge, the worktree is removed and the branch deleted with `git worktree remove` then `git branch -D`.

---

## Estimated Calendar

| Phase | Complexity | Est. days (single engineer) |
|------:|:----------:|:---:|
| 0  | S | 0.25 |
| 1  | M | 2 |
| 2  | S | 1 |
| 3  | M | 2 |
| 4  | M | 3 |
| 5  | M | 2.5 |
| 6  | L | 5 |
| 7  | S | 1 |
| 8a | M | 2.5 |
| 8b | M | 2 |
| 8c | L | 5 |
| 8d | M | 2.5 |
| 8e | M | 2.5 |
| 8f | M | 2.5 |
| 9  | L | 5 |
| 10 | M | 2 |
| 11 | L | 5 |
| 12 | M | 2.5 |
| 13 | M | 2 |
| 14 | S | 1 |
| 15 | M | 2 |
| 16 | S | 1 |
| 17 | M | 3 |
| **Total** |  | **~56 working days (~11 calendar weeks)** |

**Parallelisation opportunities (cuts total to ~5-7 weeks):**
- Phases 5 and 7 are independent of one another after Phase 3 and can be done in parallel.
- Phases 8a–8f are largely sequential by dependency but 8d (read-only) and 8e (fund/daily) can be done in parallel after 8c lands.
- Phase 10 (embeds) can begin as soon as Phase 8c lands; it doesn't need 8d–8f to complete.
- Within Phase 11, each cog file can be a separate engineer-day because they share only the embed builder layer (already shipped in Phase 10).

**Critical path:** 0 → 1 → 2 → 3 → 4 → 6 → 7 → 8a → 8b → 8c → 8f → 9 → 11 → 12 → 13 → 14 → 15 → 16 → 17. Everything off this path can move earlier given an extra engineer.
