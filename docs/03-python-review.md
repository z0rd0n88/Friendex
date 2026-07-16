# Friendex — Python Code Review: Phase 2 Target Architecture

> **Historical document.** This is a pre-implementation review of the Phase 2 design
> (`docs/02-target-architecture.md`), written before construction began. The build is
> complete as of 2026-05-28, and the three risks flagged below were addressed during
> implementation: `LockManager` no longer exposes the TOCTOU-prone `locked()` pattern
> (see `application/lock_manager.py`), no `datetime.utcnow()` calls remain in
> production code, and `domain/models.py` uses explicit `raise ValueError(...)` guards
> instead of bare `assert`. Preserved for historical context; does not describe current
> code.

## Executive Summary

The Phase 2 architecture is fundamentally sound. The layered dependency model, typed domain dataclasses, SQLite persistence with Alembic, and per-user `asyncio.Lock` serialization are all correct choices for a single-process Discord bot at this scale. Three things the implementation team must get right to avoid subtle production failures: (1) the `LockManager.locked()` method contains a time-of-check/time-of-use gap between `acquire()` and `lock.acquire()` that must be closed before any concurrency-sensitive code ships; (2) every `datetime` construction in the codebase must use `datetime.now(tz=timezone.utc)` — `datetime.utcnow()` is deprecated in Python 3.12 and the Phase 2 dataclass examples use it; (3) all `assert` statements in `__post_init__` are silently stripped under `python -O` and must be replaced with explicit `raise ValueError(...)` guards before the domain layer can be considered correct.

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Python Version and Typing](#python-version-and-typing)
3. [PEP 8 and Style](#pep-8-and-style)
4. [Async Correctness for discord.py](#async-correctness-for-discordpy)
5. [Domain Layer Review](#domain-layer-review)
6. [Application Layer Review](#application-layer-review)
7. [Persistence Layer Review](#persistence-layer-review)
8. [Configuration, Secrets, Logging](#configuration-secrets-logging)
9. [Packaging and Toolchain](#packaging-and-toolchain)
10. [Testing Libraries and Patterns](#testing-libraries-and-patterns)
11. [Specific Corrections and Refinements to Phase 2](#specific-corrections-and-refinements-to-phase-2)
12. [Open Issues Escalated to Implementation Phase](#open-issues-escalated-to-implementation-phase)

---

## Python Version and Typing

### Version Floor: Python 3.11

Require `python >= "3.11"` in `pyproject.toml`. Do not drop to 3.10.

Rationale:
- `tomllib` is stdlib in 3.11, removing a dev-only dependency.
- `ExceptionGroup` and `except*` are available in 3.11, relevant if task fan-out ever uses `asyncio.TaskGroup`.
- `asyncio.TaskGroup` (3.11) is the safe replacement for `asyncio.gather` with exception propagation — use it in `LiquidationTask` when processing all shorts in parallel.
- `typing.Self` is 3.11, useful for builder-style domain factory methods.
- CPython 3.11 is ~25% faster than 3.10 on the asyncio scheduler path, which matters for a bot receiving continuous Discord events.

Do not require 3.12 as the floor. Python 3.12 is not universally available in the most common hosting environments (Railway, Fly.io, DigitalOcean App Platform) as of mid-2026, and the 3.11→3.12 gains are marginal for this workload. Target 3.12 in CI as a test matrix entry but not as the minimum.

### Type-hint Policy

Add `from __future__ import annotations` at the top of every module. This enables PEP 563 deferred evaluation, which avoids `NameError` on forward references in `models.py` (e.g., `VoiceSession` referencing `set[int]` before `int` is in scope) and makes the annotations cheaper at runtime.

**Input parameters to functions:** use `Mapping[str, X]` not `dict[str, X]` for read-only dict arguments; use `Sequence[X]` not `list[X]` for read-only sequence arguments. This keeps the domain pure-function signatures honest about what they require and makes callers that pass tuples or `dict_values` work without coercion.

**Return types from functions:** use the concrete type (`list[X]`, `dict[str, X]`) since the caller needs to know what they receive.

**`TypeAlias` usage:** define aliases at the module top for any type that appears more than twice:

```python
from __future__ import annotations
from typing import TypeAlias

UserId: TypeAlias = str
FundId: TypeAlias = str
Price: TypeAlias = float
```

This prevents `dict[str, LongPosition]` sprawl and makes intent explicit.

**Avoid `Any`.** If a function would otherwise need `Any`, it is a signal the abstraction boundary is wrong. The only legitimate use of `Any` in this codebase is in repository serialization adapters when round-tripping raw JSON before mapping to domain types — and even there, `object` is preferable.

### mypy Strict-mode Plan

Run mypy with:

```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_ignores = true
disallow_untyped_defs = true
disallow_any_generics = true
no_implicit_reexport = true
```

Add per-module overrides only for the adapters that interface directly with `discord.py` objects (discord.py has incomplete stubs in some versions):

```toml
[[tool.mypy.overrides]]
module = ["discord.*", "discord.ext.*"]
ignore_missing_imports = true
```

Do not add blanket `ignore_missing_imports = true` at the project level. Install `discord.py` type stubs when they become stable; until then, contain the suppression to the discord modules only.

Run mypy as part of the pre-commit hook and CI gate. Zero mypy errors is the standard for merge. `type: ignore` comments require a trailing reason comment: `# type: ignore[assignment]  # discord.py stub gap`.

---

## PEP 8 and Style

### Formatter: `ruff format`

Use `ruff format` rather than `black`. As of ruff 0.4+, `ruff format` is output-identical to black for all practical formatting decisions, but it is 10–100x faster and eliminates a separate tool. Running both is redundant and creates CI friction when they disagree on edge cases. Pick `ruff format`, configure it in `pyproject.toml`, and remove any `black` configuration.

### Line Length

88 characters. This is the `ruff format` default and matches black. Do not raise it to 100+ — the gain is marginal and it creates diffs that wrap in side-by-side review.

### `ruff` Rule Selection

```toml
[tool.ruff.lint]
select = [
    "E",      # pycodestyle errors — basic PEP 8
    "F",      # pyflakes — undefined names, unused imports
    "I",      # isort — import order
    "B",      # flake8-bugbear — common bug patterns (B006: mutable default args, B007: unused loop var, B904: raise-from in except)
    "UP",     # pyupgrade — modernize syntax (UP007: X | Y unions, UP035: deprecated typing imports)
    "RUF",    # ruff-native rules — RUF006: asyncio dangling tasks, RUF010: f-string conversion, RUF100: unused noqa
    "ASYNC",  # flake8-async — ASYNC100: blocking calls in async context (critical for aiosqlite + asyncio)
    "C4",     # flake8-comprehensions — prefer list/dict/set comprehensions over calls
    "SIM",    # flake8-simplify — simplify boolean expressions, context managers
    "TCH",    # flake8-type-checking — move type-only imports into TYPE_CHECKING blocks
    "PTH",    # flake8-use-pathlib — replace os.path with pathlib.Path
    "ERA",    # eradicate — no commented-out code
    "PL",     # pylint — select PLR0912 (too many branches), PLR0913 (too many arguments), PLR2004 (magic values)
]
ignore = [
    "E501",   # line too long — handled by ruff format
    "B008",   # function call in default argument — needed for pydantic Field(default_factory=...)
    "PLR2004", # magic value comparisons — disable for domain constants (prices, percentages)
]
```

ASYNC rules are the highest-value selection for this codebase. `ASYNC100` and `ASYNC110` will flag any blocking `open()`, `time.sleep()`, or synchronous DB call inside an `async def` — exactly the failure mode that will stall the Discord event loop.

### Import Sorting

Configure `ruff` isort with:

```toml
[tool.ruff.lint.isort]
known-first-party = ["friendex"]
split-on-trailing-comma = true
```

Import order: stdlib, third-party (`discord`, `sqlalchemy`, `pydantic_settings`, `structlog`), first-party (`friendex.*`). No relative imports except within the same module subpackage (e.g., `from .models import UserAccount` within `domain/`).

### Naming Conventions

Follow PEP 8 strictly:
- Classes: `PascalCase`
- Functions, methods, variables: `snake_case`
- Constants (module-level, immutable): `UPPER_SNAKE_CASE`
- Private attributes: single leading underscore (`_locks`, `_meta_lock`)
- Type aliases: `PascalCase` (treated as types)
- Abstract base classes: prefix with `I` per Phase 2 convention (`IUserRepo`) or use the `Protocol` pattern — see Application Layer section for the recommendation

---

## Async Correctness for discord.py

### Cog Lifecycle: `setup_hook` Not `on_ready`

Phase 2's Background Tasks section states tasks are "started from `on_ready` in `bot.py`." This is incorrect for a production bot.

Use `setup_hook` on the `Bot` subclass instead:

```python
class FriendexBot(commands.Bot):
    async def setup_hook(self) -> None:
        await self.add_cog(TradingCog(self._container.trading_service, self._settings))
        # ... add all cogs and listeners
        self._container.start_tasks()  # tasks started here, not in on_ready
```

`setup_hook` runs once before the bot connects to Discord and is guaranteed to complete before `on_ready` fires. Starting tasks in `on_ready` creates a race: `on_ready` can fire multiple times (on reconnect), causing duplicate task loops. Starting tasks in `setup_hook` and guarding with `if not task.loop.is_running()` or using `tasks.loop.start()` idempotently avoids duplicate loops on reconnect.

### `discord.ext.tasks.loop` Patterns

Every task loop must have a `before_loop` hook that waits for the bot to be ready before the first iteration:

```python
@_loop.before_loop
async def _before_loop(self) -> None:
    await self.bot.wait_until_ready()
```

Without this, a task started in `setup_hook` may fire before the Discord session is established and before repository connections are open.

Every task must explicitly handle its own exceptions. The Phase 2 `LiquidationTask` example does this correctly with the `try/except Exception` wrapper. The rule is: tasks must never let an exception propagate out of `_loop()`. A propagated exception silently kills the task loop without any restart — `discord.ext.tasks` does not restart a loop that exits via unhandled exception unless `reconnect=True` is passed to `@tasks.loop`. Pass `reconnect=True` on every loop decorator as a defense-in-depth measure, and still keep the `try/except` wrapper.

On shutdown, call `loop.cancel()` explicitly in the `cog_unload` method of any cog that owns a task:

```python
def cog_unload(self) -> None:
    self._loop.cancel()
```

### `asyncio.Lock` and `LockManager` — Deadlock and TOCTOU Analysis

The `LockManager.locked()` method in Phase 2 has a subtle time-of-check/time-of-use gap. Here is the relevant sequence:

```python
# Phase 2 code
locks = [await self.acquire(uid) for uid in ids]  # acquires meta_lock, then releases it, per uid
for lock in locks:
    await lock.acquire()                            # acquires each per-user lock
```

`acquire()` uses `async with self._meta_lock` to safely create the per-user lock, then returns it. The returned lock object is real and stable — `asyncio.Lock` objects are not garbage collected while referenced. The gap is between the two phases: after `acquire()` returns a lock object and before `lock.acquire()` is called, another coroutine can call `acquire()` for the same `uid`, receive the same `Lock` object, and call `lock.acquire()` first. This is not a correctness bug — both callers are getting the same lock, and the second one will wait as intended. The TOCTOU analysis is benign here: the meta lock only guards creation of the per-user lock entry, not ownership of that lock. Once created, the `asyncio.Lock` itself serializes concurrent acquirers correctly.

However, there is a genuine issue: the list comprehension `[await self.acquire(uid) for uid in ids]` calls `acquire()` sequentially with `await`, meaning the meta lock is released and re-acquired between each uid. This is correct and necessary — holding the meta lock across multiple awaits would cause a deadlock if another coroutine needed to create a different user's lock while we held the meta lock.

The proposed fix: document the design explicitly and confirm the following invariant in the implementation:

```python
@asynccontextmanager
async def locked(self, *user_ids: str) -> AsyncIterator[None]:
    ids = sorted(set(user_ids))  # sorted order prevents deadlock across concurrent callers
    locks: list[asyncio.Lock] = []
    for uid in ids:
        async with self._meta_lock:
            if uid not in self._locks:
                self._locks[uid] = asyncio.Lock()
        locks.append(self._locks[uid])
    # All lock objects are now collected. Acquire them in sorted order.
    for lock in locks:
        await lock.acquire()
    try:
        yield
    finally:
        for lock in reversed(locks):
            lock.release()
```

This inlines `acquire()` into `locked()` to make the two phases explicit and removes the dangling async method that returns an unacquired lock (a footgun for callers who might call `acquire()` directly and forget to call `lock.acquire()`). The async method `acquire()` should be removed from the public API of `LockManager` to prevent misuse.

### `asyncio.gather` vs Sequential Awaits

Use `asyncio.gather` only for genuinely independent coroutines with no shared mutable state. In this codebase:

- `$portfolio` rendering iterates positions and resolves member objects from the Discord cache. Member resolution (`guild.get_member`) is synchronous (cache lookup), so there is nothing to gather. The value computations are pure. Sequential iteration is fine.
- Background `LiquidationTask` processing many users: use `asyncio.TaskGroup` (3.11+) to process shorts in parallel, with each sub-task acquiring its own locks. This is the correct use case.
- `$trending` resolves member names and reads prices for up to 15 users: these reads are independent. Use `asyncio.gather(*[repo.get_price(uid) for uid in top_ids])` here.

The rule: if the coroutines touch different user IDs and hold no shared locks, `gather` is safe and faster. If they could contend on the same lock, sequential is correct and gather would produce a deadlock.

### `asyncio.sleep` vs `discord.utils.sleep_until`

For the `DailyResetTask` and `WeeklyResetTask` that run every 1 minute and check wall-clock time, use `discord.utils.sleep_until` for the body sleep:

```python
import discord.utils
from datetime import datetime, timezone

async def _loop(self) -> None:
    now = datetime.now(tz=timezone.utc)
    # ... check if reset needed
    # sleep until next minute boundary
    next_minute = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    await discord.utils.sleep_until(next_minute)
```

`discord.utils.sleep_until` accepts a timezone-aware `datetime` and handles the case where the target time has already passed (returns immediately rather than sleeping a full cycle). `asyncio.sleep(60)` drifts because each iteration starts after the previous one's processing time, not at a fixed boundary.

### Blocking I/O in Async Methods

The SQLite migration script in Phase 2 uses a synchronous `Session(engine)` and `Base.metadata.create_all(engine)`. These are blocking calls. The migration script is run as a standalone CLI script (not inside the bot's event loop), so this is acceptable. Do not call synchronous SQLAlchemy methods inside any `async def`. The ruff `ASYNC100` rule will catch most violations; verify the pattern in `env.py` as well (see Persistence Layer section).

---

## Domain Layer Review

### `dataclass` vs `pydantic.BaseModel`

Use `@dataclass` throughout the domain layer. The domain layer must have zero third-party imports — this is stated in Phase 2's design principles and is correct. Pydantic is a validation and serialization library, not a domain modeling tool. Its runtime cost (field validation on every instantiation), its import footprint, and its tight coupling to JSON serialization are all wrong for a layer that should be pure, fast, and dependency-free.

Pydantic belongs in the adapters layer: `pydantic-settings` for config, and optionally Pydantic models for request/response validation at the Discord command boundary if the team wants coercion of Discord argument types. It has no role in `domain/models.py`.

### Invariant Enforcement: `__post_init__` with `raise ValueError`

Replace every `assert` in `__post_init__` with an explicit `raise ValueError(...)`. Python strips assert statements under `python -O` (optimized mode), which is commonly used in production deployments. A stripped invariant check means corrupted domain objects propagate silently.

Replace:
```python
def __post_init__(self):
    assert self.streak >= 0, "streak must be non-negative"
```

With:
```python
def __post_init__(self) -> None:
    if self.streak < 0:
        raise ValueError(f"streak must be non-negative, got {self.streak!r}")
```

The `f"{value!r}"` form in error messages gives exact repr of the bad value, which is critical for debugging.

### `frozen=True` for Value Objects

Apply `frozen=True` to `PricePoint` — it is a pure value object (price + timestamp) with no mutable identity. Frozen dataclasses are hashable, can be stored in sets, and communicate immutable intent to readers.

```python
@dataclass(frozen=True)
class PricePoint:
    price: float
    timestamp: datetime
```

All other domain models (`UserAccount`, `Stock`, `HedgeFund`, etc.) are aggregates with mutable state and should remain `frozen=False`.

`VoiceSession` and `VoicePingSession` are in-memory session state (not persisted domain aggregates). Leave them mutable.

### `datetime` Handling

Every `datetime.utcnow()` call in the codebase must be replaced before implementation begins. `datetime.utcnow()` returns a naive (timezone-unaware) datetime, is deprecated since Python 3.12, and will be removed in a future version.

The correct replacement throughout:

```python
from datetime import datetime, timezone

# WRONG
datetime.utcnow()

# CORRECT
datetime.now(tz=timezone.utc)
```

The Phase 2 `ActivityBucket` dataclass uses `field(default_factory=datetime.utcnow)` as the default for `bucket_start`. Replace with:

```python
from datetime import datetime, timezone
from dataclasses import field

bucket_start: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
```

All datetime comparisons must be between two aware datetimes. If a datetime is loaded from the database as a naive datetime (SQLite stores TEXT; aiosqlite returns strings), convert it explicitly at the repository boundary before it enters the domain:

```python
def _parse_dt(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
```

Never mix naive and aware datetimes — Python will raise `TypeError` at comparison time, but only at runtime, not during static analysis.

### `decimal.Decimal` vs `float` for Money

Use `float` for this codebase. The recommendation is intentional despite the general advice to use `Decimal` for financial calculations.

Rationale: SQLite stores `REAL` as IEEE 754 double-precision float. Converting between `Decimal` and `float` at every repository boundary creates more error surface than it eliminates. The game operates in a range where float precision is adequate: prices are in the $70–$10,000 range, cash balances in the $0–$1,000,000 range. A float64 is precise to ~15 significant digits, which means cash balances are accurate to the nearest cent at values below $10 trillion. For a Discord game bot this is more than sufficient.

The required discipline with `float`: define a single rounding function and use it at every point where a float result is displayed or persisted:

```python
# domain/models.py or domain/money.py
def round_money(value: float) -> float:
    return round(value, 2)
```

Apply `round_money()` in the service layer before writing to the repository and before constructing embed strings. Do not round inside the domain pure functions — let them operate at full float precision and round at the output boundary.

---

## Application Layer Review

### Service Classes vs Module-level Functions

Use service classes with constructor injection. Phase 2 already makes this choice and it is correct.

The alternative — module-level functions with parameters for every dependency — produces function signatures with 5–7 parameters for any non-trivial operation and makes testing harder (no object to mock at the boundary). A `TradingService(user_repo, price_repo, fund_repo, lock_manager, settings)` constructor makes dependencies explicit and testable. The class provides a coherent namespace for the buy/sell/short/cover operations that share the same dependency set.

### Dependency Injection Style

Constructor injection throughout, as Phase 2 specifies. No global singletons except `LockManager`.

One addition: `LockManager` itself should not be a module-level singleton. It should be constructed in `container.py` and injected like any other dependency. The constraint that makes it feel singleton-like is that all services must share the same `LockManager` instance — but that is a lifetime concern (construct once, inject everywhere), not a reason to use a module-level global. Enforce this by never importing `LockManager` directly in service modules; receive it via constructor.

### Error Propagation

Services raise `DomainError` subclasses. Services never return `None` to indicate failure. Services never return raw dicts.

The rule: if a service method can fail, it raises. If it succeeds, it returns a typed result object. The caller (cog) handles exceptions via the top-level error handler. This eliminates the pattern of `if result is None: await ctx.send("error")` scattered across cog methods.

### Result Types

Define typed result dataclasses in `application/results.py`:

```python
@dataclass(frozen=True)
class BuyResult:
    buyer_id: str
    target_id: str
    shares: int
    price_per_share: float
    total_cost: float
    new_cash_balance: float
    new_price: float

@dataclass(frozen=True)
class ShortResult:
    seller_id: str
    target_id: str
    shares: int
    entry_price: float
    locked_cash: float
    locked_fund: float
    new_price: float

@dataclass(frozen=True)
class CoverResult:
    holder_id: str
    target_id: str
    shares: int
    entry_price: float
    cover_price: float
    pnl: float
    returned_cash: float
    returned_fund: float
```

`frozen=True` on result objects: they are created once, returned, and passed to embed builders. Mutability serves no purpose and frozen dataclasses are slightly faster to instantiate due to `__hash__` precomputation.

### Repository Interface Design

Phase 2 uses abstract base classes in `application/interfaces.py`. Prefer `typing.Protocol` over `abc.ABC` for repository interfaces:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class IUserRepo(Protocol):
    async def get(self, user_id: str) -> UserAccount | None: ...
    async def save(self, account: UserAccount) -> None: ...
    async def get_all_ids(self) -> list[str]: ...
```

`Protocol` does not require `implements` declarations — the concrete `SqliteUserRepo` satisfies `IUserRepo` structurally, without inheriting from it. This keeps the persistence layer decoupled from the application layer's interface definition: `SqliteUserRepo` does not need to import from `application/interfaces.py`, preserving the strict inward dependency direction.

For batch operations: add `async def save_many(self, accounts: Sequence[UserAccount]) -> None` to the interface. Background tasks that update every user's price in a tick should use `save_many` with a single transaction, not `N` individual `save()` calls.

---

## Persistence Layer Review

### SQLAlchemy 2.0 Async Session Pattern

The correct pattern for all database access:

```python
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

engine = create_async_engine(
    settings.database_url,
    connect_args={"check_same_thread": False},
    echo=settings.log_level == "DEBUG",
)

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,  # critical: see below
)
```

Use `expire_on_commit=False`. By default SQLAlchemy expires all ORM attribute access after `commit()`. In an async context, accessing an expired attribute triggers a lazy SQL query on the next attribute access, which will raise `MissingGreenlet` (no greenlet) because async SQLAlchemy does not support implicit lazy loading. With `expire_on_commit=False`, committed objects retain their in-memory values. Since repositories map ORM rows to domain dataclasses before returning, the ORM objects are discarded after each session anyway — `expire_on_commit=False` is the correct setting.

Repository methods should use `async with SessionLocal() as session:` and `async with session.begin():` to ensure transactions:

```python
async def save(self, account: UserAccount) -> None:
    async with SessionLocal() as session:
        async with session.begin():
            orm_obj = _to_orm(account)
            await session.merge(orm_obj)
```

### WAL Mode Setup

Configure WAL mode at engine creation time, not in Alembic migrations:

```python
from sqlalchemy import event

@event.listens_for(engine.sync_engine, "connect")
def set_wal_mode(dbapi_conn, connection_record):
    dbapi_conn.execute("PRAGMA journal_mode=WAL")
    dbapi_conn.execute("PRAGMA synchronous=NORMAL")  # safe with WAL
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

`PRAGMA synchronous=NORMAL` with WAL gives durable writes that survive OS crashes (not power failures) at roughly 3x the throughput of `FULL` synchronous mode. For a Discord bot on a VPS, this is the correct tradeoff. `foreign_keys=ON` must be set per connection in SQLite because it defaults to off.

### ORM Model Pattern

Use SQLAlchemy 2.0 `Mapped[T]` + `mapped_column()`:

```python
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, Boolean

class Base(DeclarativeBase):
    pass

class UserORM(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    cash_balance: Mapped[float] = mapped_column(Float, nullable=False)
    net_worth: Mapped[float] = mapped_column(Float, nullable=False)
    month_start_net_worth: Mapped[float] = mapped_column(Float, nullable=False)
    last_activity: Mapped[str] = mapped_column(String, nullable=False)  # ISO datetime
    opt_in: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    intro_shown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

The `Mapped[T]` syntax makes the type checker aware of column types without stubs. Never use the old `Column(String, ...)` pattern with SQLAlchemy 2.0; it bypasses the typed layer.

### Repository Boundary: ORM ↔ Domain

Map at the boundary. ORM objects never leave the repository method. Domain objects never enter the ORM directly.

```python
# In SqliteUserRepo
async def get(self, user_id: str) -> UserAccount | None:
    async with SessionLocal() as session:
        orm_obj = await session.get(UserORM, user_id)
        if orm_obj is None:
            return None
        return _orm_to_domain(orm_obj)

def _orm_to_domain(orm: UserORM) -> UserAccount:
    return UserAccount(
        user_id=orm.user_id,
        cash_balance=orm.cash_balance,
        # ...
    )
```

The `_orm_to_domain` and `_domain_to_orm` functions are private to each repository module. They are the only place in the codebase where the ORM schema and the domain schema meet. Keeping them small, explicit, and tested is critical.

### Lazy Loading Prohibition

Never access ORM relationship attributes outside the session context. The Phase 2 schema has relationships (e.g., `UserORM` → `LongPositionORM` 1:N). When loading a `UserORM`, eagerly load its positions:

```python
from sqlalchemy.orm import selectinload

result = await session.execute(
    select(UserORM)
    .options(
        selectinload(UserORM.long_positions),
        selectinload(UserORM.short_positions),
        selectinload(UserORM.activity_buckets),
    )
    .where(UserORM.user_id == user_id)
)
orm_obj = result.scalar_one_or_none()
```

This is not optional — without it, accessing `orm_obj.long_positions` after the session closes raises `sqlalchemy.orm.exc.DetachedInstanceError`.

### Alembic for Async

`env.py` must use the async connection pattern:

```python
# alembic/env.py
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine

def run_migrations_online() -> None:
    connectable = create_async_engine(config.get_main_option("sqlalchemy.url"))

    async def run_async_migrations() -> None:
        async with connectable.connect() as connection:
            await connection.run_sync(do_run_migrations)
        await connectable.dispose()

    asyncio.run(run_async_migrations())
```

The synchronous migration path (`run_migrations_offline`) is fine as-is for generating SQL scripts. Only the online path needs the async adaptation.

Set `render_as_batch=True` in `env.py` for SQLite compatibility:

```python
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=True,  # required for ALTER TABLE in SQLite
)
```

Without `render_as_batch=True`, Alembic cannot add or drop columns in SQLite (SQLite has no `ALTER TABLE ... DROP COLUMN` before 3.35). This will block every schema evolution migration.

---

## Configuration, Secrets, Logging

### pydantic-settings v2 Patterns

The Phase 2 `Settings` class is structurally correct. Additions:

**Comma-separated list parsing for `vc_ping_role_ids` and `photo_bonus_channel_ids`:**

pydantic-settings v2 does not automatically split comma-separated environment variable strings into lists. The `.env.example` shows `VC_PING_ROLE_IDS=1331261849488068628,...` but the `Settings` field is `list[int]`. Add a validator:

```python
from pydantic import field_validator

class Settings(BaseSettings):
    vc_ping_role_ids: list[int] = Field(default_factory=list)

    @field_validator("vc_ping_role_ids", "photo_bonus_channel_ids", mode="before")
    @classmethod
    def parse_int_list(cls, v: object) -> list[int]:
        if isinstance(v, str):
            return [int(x.strip()) for x in v.split(",") if x.strip()]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []
```

Without this validator, loading `VC_PING_ROLE_IDS=123,456` from `.env` will raise a `ValidationError` because pydantic-settings v2 treats the entire string as a single value for `list[int]`.

**Startup validation for required secrets:** Add a model-level validator that raises at boot if `discord_token` is the placeholder string:

```python
from pydantic import model_validator

@model_validator(mode="after")
def validate_secrets(self) -> "Settings":
    if self.discord_token in ("", "your_bot_token_here"):
        raise ValueError("DISCORD_TOKEN is not configured")
    return self
```

**Time field parsing:** `market_open` and `market_close` are `datetime.time` fields. pydantic-settings v2 handles ISO time strings (`"06:30"`) via its built-in `time` type coercion. This works correctly; no custom validator needed.

**Nested settings:** For readability, consider grouping settings into nested classes:

```python
class MarketSettings(BaseModel):
    open: time = time(6, 30)
    close: time = time(4, 30)
    timezone_offset_hours: int = 0
    sunday_buy_allowed: bool = True

class Settings(BaseSettings):
    market: MarketSettings = Field(default_factory=MarketSettings)
```

pydantic-settings v2 supports nested models with env var prefix via `model_config = SettingsConfigDict(env_nested_delimiter="__")`, so `MARKET__OPEN=06:30` maps to `settings.market.open`. This is optional but significantly improves readability for the 25-field settings class in Phase 2.

### `.env` Handling

`.env` in `.gitignore`. `.env.example` committed to the repository. The Phase 2 config section already states this; no change needed.

Do not commit any real Discord token, guild ID, or role ID to the repository even in example files. The `.env.example` in Phase 2 correctly uses placeholder values for `DISCORD_TOKEN` and `GUILD_ID`. The hardcoded role IDs in `VC_PING_ROLE_IDS` in the example are real values from the spec skeleton — replace them with placeholder values in the committed example.

### structlog Initialization

The Phase 2 `configure_logging` function is nearly correct. Three corrections:

**1. Wire stdlib logging through structlog:**

```python
import logging
import structlog

def configure_logging(settings: Settings) -> None:
    structlog.configure(...)  # as in Phase 2

    # Route stdlib logging (discord.py, sqlalchemy, etc.) through structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )
    # Replace stdlib handlers with structlog's foreign pre-chain
    for name in ("discord", "sqlalchemy.engine", "aiosqlite"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
```

Without this, `discord.py` internal log messages (connection events, heartbeat failures) use the default stdlib formatter and appear as unstructured noise alongside your JSON logs.

**2. Add `StackInfoRenderer` before `JSONRenderer`:**

```python
processors = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),   # add this
    structlog.processors.ExceptionRenderer(),   # add this
]
```

Without `ExceptionRenderer`, exceptions passed as `exc_info=True` are dropped from JSON output.

**3. `BoundLogger` injection:** Prefer injecting a bound logger into services over module-level `get_logger()`:

```python
# In service constructor
def __init__(self, ...) -> None:
    self._log = structlog.get_logger().bind(service="trading_service")
```

Module-level `log = structlog.get_logger()` is acceptable for small modules but creates implicit global state. Constructor binding produces a logger that always includes the service name in every record emitted by that service.

**Token redaction:** Never pass `settings.discord_token` to any logger. Add a structlog processor that redacts the token value from all log records:

```python
def redact_token(logger, method, event_dict):
    token = event_dict.get("token") or ""
    if token:
        event_dict["token"] = "REDACTED"
    return event_dict
```

Add `redact_token` as the first processor in the chain so it runs before any serialization.

---

## Packaging and Toolchain

### `pyproject.toml` Structure

```toml
[project]
name = "friendex"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "discord.py>=2.4",
    "sqlalchemy[asyncio]>=2.0.30",
    "aiosqlite>=0.20",
    "alembic>=1.13",
    "pydantic-settings>=2.2",
    "structlog>=24.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-cov>=5",
    "dpytest>=0.7",
    "freezegun>=1.4",
    "mypy>=1.10",
    "ruff>=0.4",
]

[project.scripts]
friendex = "friendex.main:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

The entry point `friendex.main:main` enables `python -m friendex` and `friendex` CLI invocation after install.

### Dependency Management: `uv`

Use `uv` for all dependency operations. The workflow:

```bash
# Install uv (one-time)
pip install uv

# Create virtual environment and install all dependencies
uv venv
uv pip install -e ".[dev]"

# Lock dependencies
uv pip compile pyproject.toml -o requirements.txt
uv pip compile pyproject.toml --extra dev -o requirements-dev.txt

# Sync to locked versions (CI, production)
uv pip sync requirements-dev.txt
```

`uv` resolves and installs 10–100x faster than pip. The `uv.lock` file produced by newer `uv` versions (0.2+) is the preferred lockfile format — it is cross-platform and binary-safe. Mark `uv.lock` as binary in `.gitattributes` to prevent line-ending corruption on Windows checkouts.

Comparison with Poetry: Poetry has an opinionated project management layer (version bumping, publishing, virtualenv management) that adds complexity not needed here. `uv` is a drop-in pip replacement that does exactly what is needed — fast, reproducible installs — without the abstraction overhead. For a single-application bot that will never be published to PyPI, `uv` + `pyproject.toml` is the correct choice.

### Pinned Versions

```
discord.py>=2.4        # app commands stable, message commands stable
sqlalchemy[asyncio]>=2.0.30  # 2.0.30 fixes an async session bug
aiosqlite>=0.20        # asyncio SQLite driver
alembic>=1.13          # render_as_batch stable
pydantic-settings>=2.2  # nested model env var support
structlog>=24.1        # async contextvars support stable
```

### Lint/Format/Type-check Run Order

In `pre-commit` and CI, run in this order:

1. `ruff format --check` (formatting)
2. `ruff check` (linting)
3. `mypy --strict src/` (type checking)
4. `pytest --cov=friendex --cov-fail-under=80` (tests with coverage gate)

Order matters: format check before lint avoids false positives in some ruff rules that parse indentation. mypy after lint because mypy is slower and lint failures are faster to fix. Tests last because they are the most expensive.

### `pre-commit` Configuration

`.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.4.7
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.10.0
    hooks:
      - id: mypy
        args: [--strict, src/]
        additional_dependencies:
          - pydantic-settings>=2.2
          - sqlalchemy[asyncio]>=2.0.30
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
```

### CI Sketch (GitHub Actions)

```yaml
name: CI
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v2
      - run: uv venv --python ${{ matrix.python-version }}
      - run: uv pip install -e ".[dev]"
      - run: uv run ruff format --check .
      - run: uv run ruff check .
      - run: uv run mypy --strict src/
      - run: uv run pytest --cov=friendex --cov-report=xml --cov-fail-under=80
      - uses: codecov/codecov-action@v4
        with:
          files: coverage.xml
```

---

## Testing Libraries and Patterns

Full strategy is deferred to Phase 3c (`docs/05-testing-strategy.md`). The following decisions are locked now:

**Test runner:** `pytest` with `pytest-asyncio`. Configure `asyncio_mode = "auto"` in `pyproject.toml` so that all `async def test_*` functions automatically use the asyncio event loop without requiring the `@pytest.mark.asyncio` decorator on each:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Discord integration testing:** Use `dpytest` for cog command integration tests. `dpytest` provides a mock Discord client, fake guilds/channels/members, and allows `await dpytest.message("$buy @user 10")` followed by assertions on the reply embed. For listener tests (`on_message`, `on_voice_state_update`), hand-rolled `unittest.mock.AsyncMock` of the relevant Discord objects is simpler than `dpytest` event dispatch and gives more control.

**Time-sensitive tests:** Use `freezegun`. Every test that touches `datetime.now(tz=timezone.utc)`, market hours checks, cooldown expiry, or daily reward eligibility must freeze time via `@freeze_time("2026-05-13 10:00:00+00:00")`. Without `freezegun`, these tests are non-deterministic.

**Repository testing:** Use an in-memory SQLite database (`sqlite+aiosqlite:///:memory:`) for all repository tests. Create a `pytest` fixture that creates the schema via `Base.metadata.create_all(engine)` at the start of each test session and truncates tables between tests. Do not mock the repository in repository tests — test the actual SQL.

**Coverage target:** 80% minimum per project rules. Prioritize domain and application layer coverage (should reach 90%+); adapter layer coverage (Discord cogs, embed builders) is harder to reach and can be 70%+.

---

## Specific Corrections and Refinements to Phase 2

### Correction 1: `datetime.utcnow()` in `ActivityBucket`

Phase 2 `models.py`:

```python
bucket_start: datetime = field(default_factory=datetime.utcnow)
```

Replace with:

```python
bucket_start: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
```

This applies to every `datetime.utcnow()` reference in the architecture document. The `DailyResetTask` body also uses `datetime.utcnow().date()` and `datetime.utcnow().weekday()` — replace with `datetime.now(tz=timezone.utc).date()` and `.weekday()` respectively.

### Correction 2: `assert` in `__post_init__` — Stripped Under `-O`

All `assert` statements in Phase 2 dataclasses are unsafe in production:

```python
# Phase 2 DailyProgress
def __post_init__(self):
    assert self.streak >= 0, "streak must be non-negative"

# Phase 2 LongPosition
def __post_init__(self):
    assert self.shares > 0, "shares must be positive"
    assert self.avg_entry > 0, "avg_entry must be positive"

# Phase 2 ShortPosition — three asserts
# Phase 2 UserAccount — one assert
# Phase 2 Stock — one assert
# Phase 2 HedgeFund — one assert
```

Every one of these must become `if ... raise ValueError(...)`. See Domain Layer Review section for the correct pattern.

### Correction 3: `LockManager.acquire()` as Public API is a Footgun

Phase 2 exposes `async def acquire(self, user_id: str) -> asyncio.Lock`. This method returns an unacquired lock. Any caller who stores the result and does not call `.acquire()` on it will corrupt the serialization guarantee. Remove this method from the public interface. Make it `_acquire()` (private) and call it only from `locked()`. The public API of `LockManager` should be exactly one method: `locked(*user_ids)`.

### Correction 4: `Stock` Model Contains `high_24h` and `low_24h`

Phase 2's open-questions resolution (item 9) explicitly removes `high_24h` and `low_24h` from the stored model and computes them dynamically from price history. However, the `Stock` dataclass in the Domain Model section still contains these fields:

```python
@dataclass
class Stock:
    user_id: str
    current: float
    history: list[PricePoint]
    high_24h: float      # REMOVE
    low_24h: float       # REMOVE
    all_time_high: float
```

And the SQLite schema still has `high_24h REAL NOT NULL` and `low_24h REAL NOT NULL` in the `stocks` table. Remove both fields from the `Stock` dataclass and from the `stocks` table schema. Remove `DailyResetTask`'s call to `PriceTickService.reset_24h_high_low()` (which no longer exists). `StatsService.get_price_stats()` computes these dynamically as stated in the resolution.

### Correction 5: `LockManager` Uses `dict` Not `defaultdict`

Phase 2 text says "holds a `defaultdict(asyncio.Lock)`" but the code shows `self._locks: dict[str, asyncio.Lock] = {}` with an explicit `if user_id not in self._locks` guard. This is not a bug — the explicit guard inside the meta lock is correct. However, the comment in the architecture document should not say `defaultdict` because that would imply lock creation is not protected by the meta lock. Update the prose description to remove the `defaultdict` reference to avoid confusing the implementation team.

### Correction 6: Migration Script Uses Synchronous `Session`

The JSON-to-SQLite migration script in Phase 2:

```python
with Session(engine) as session:
    ...
    session.commit()
```

Uses a synchronous `Session` and synchronous `engine`. Since the migration is a standalone script (not inside the bot's event loop), this is acceptable. However, `engine` in this context must be a synchronous engine (`create_engine`), not the async engine used by the bot. The migration script should construct its own synchronous engine:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

sync_engine = create_engine("sqlite:///data/friendex.db")
Base.metadata.create_all(sync_engine)

with Session(sync_engine) as session:
    ...
```

Using the bot's `create_async_engine` object in a synchronous `with Session(...)` block will raise `TypeError` at runtime.

### Correction 7: Task Structure — `@tasks.loop` on Instance Method

The Phase 2 `LiquidationTask` example:

```python
class LiquidationTask:
    def start(self):
        self._loop.start()

    @tasks.loop(minutes=5)
    async def _loop(self):
        ...
```

`@tasks.loop` is a class-level decorator on discord.py. When applied to an instance method, it creates a class-level loop object shared across all instances, not a per-instance loop. For a task class that is only ever instantiated once (as in this architecture), this works. But it is not the idiomatic pattern.

The idiomatic pattern is to use `loop.start(self)` by registering the loop in the constructor or to use a module-level decorated function that is then wrapped in a class for DI purposes. The simplest correct pattern:

```python
class LiquidationTask:
    def __init__(self, service: LiquidationService) -> None:
        self._service = service
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._loop.start()

    def stop(self) -> None:
        self._loop.cancel()

    @tasks.loop(minutes=5, reconnect=True)
    async def _loop(self) -> None:
        try:
            await self._service.check_and_liquidate_shorts()
        except Exception:
            log.error("liquidation_task.failed", exc_info=True)
```

Instantiate exactly once in `container.py`. The single-instantiation constraint ensures the `@tasks.loop` class-level object behaves correctly.

---

## Open Issues Escalated to Implementation Phase

The following items are not blockers for the architecture but must be resolved by the implementation team during Phase 3 (implementation planning) or Phase 5 (Discord interface) at the latest.

**1. `asyncio.Lock` objects are bound to the event loop at creation time.** If `LockManager` is constructed before `asyncio.run()` starts (e.g., in module-level code), the locks will reference a closed or nonexistent event loop. Enforce construction of `LockManager` inside `main()` after `asyncio.run()` is entered, or inside `setup_hook`. Add a construction-time assertion or defer lock creation to first use.

**2. `VoiceSession.from_ping_message_ids` is typed as `set[int]` in the domain model but the repository must serialize it as `list[int]`.** The bidirectional mapping is noted in Phase 2 (open question 11 resolution) but the concrete serialization code is not specified. The implementation team must add a `voice_unique_channels` table analog (or a JSON-encoded column) for this set. Recommendation: use a `voice_session_ping_messages` junction table with `(user_id, message_id)` primary key.

**3. `last_trade_time` is in-memory only in the spec and Phase 2 does not define a `trade_cooldowns` table.** Phase 2 mentions a `trade_cooldowns` table with `expires_at` as a justification for not using Redis, but this table is absent from the SQLite schema sketch. The implementation team must add this table and implement a `ICooldownRepo` interface or integrate cooldown checking into `IUserRepo`. Recommendation: add a `trade_cooldowns` table with `(user_id TEXT PK, expires_at TEXT NOT NULL)` and a 5-minute sweep task that deletes expired rows.

**4. `vc_extra_boosts` in-memory state.** Phase 2 does not define persistence for `VcExtraBoost` objects. The `VcBoostTask` will lose all active boosts on restart. The Phase 1 risk register flags this as medium severity. Add a `vc_extra_boosts` table to the schema. The `VcBoostTask` should load active boosts from the repository on startup.

**5. `voice_sessions` in-memory state.** Phase 2 inherits this gap from Phase 1. Voice sessions are not persisted. On restart, users in voice channels lose their in-progress session credit. The architecture supports fixing this (repositories are the persistence boundary) but no `IVoiceSessionRepo` interface or persistence table is defined. For Phase 0 (foundation), define the table and interface even if the `VoiceListener` only writes to it in Phase 5.

**6. Notification channel for auto-liquidations.** Phase 2 notes that `LiquidationTask` "emits a Discord notification to the guild's configured notification channel" but `Settings` has no `notification_channel_id` field. Add `notification_channel_id: int | None = None` to `Settings`. If `None`, liquidation proceeds silently (no Discord message). This must be wired before `LiquidationTask` can send notifications.

**7. `$portfolio` resolves member objects via `ctx.guild.get_member`.** This returns `None` for users who have left the server, causing silent position invisibility. Store `display_name: str` on `LongPosition` and `ShortPosition` at the time of purchase. Fall back to the stored name when `get_member` returns `None`. This is a Phase 5 (Discord interface) concern but the domain model change (`display_name` field on position dataclasses) must be made in Phase 1.

**8. `HedgeFund.investors` dict — key type.** Phase 2 types this as `dict[str, float]` where the key is `investor_user_id`. The `investors` field is present but `FundService.invest()` is stubbed. When the invest command is implemented, the APY accrual in `MonthlyRolloverTask` must distribute proportionally to investors, not just to the manager. The `accrue_apy` function in `fund_math.py` must be designed with this distribution in mind from the start, even if the multi-investor path raises `NotImplementedError` initially.

**9. `Settings.log_format` validation.** The `log_format` field is `str` with no validation. An invalid value like `"text"` will silently fall through to no renderer being appended to the processor chain and structlog will raise at log time, not at startup. Add a `Literal["json", "console"]` type or a `field_validator` that raises at settings load time.

**10. `DailyResetTask` and `WeeklyResetTask` wall-clock checks use `last_reset_date` stored in a `system_state` table.** This table is not defined in Phase 2's schema sketch. Add it: `CREATE TABLE system_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)`. Rows: `("daily_last_reset", ISO date string)`, `("weekly_last_reset", ISO date string)`, `("monthly_last_rollover", ISO date string)`. The implementation team must add this table in the Alembic baseline migration.
