# Friendex: Plan Refactor of Single-File Bot into Maintainable Python Application

Turn `bot.py` (a working single-file Discord bot simulating a stock-exchange game on real server activity) into a properly structured, testable Python application. **This task is planning only — no code refactoring yet.** Deliverables are documents that I will review before authorizing implementation in a follow-up session.

## Setup (do this first)

Create a worktree per project CLAUDE.md before any file writes:

```bash
git worktree add .worktrees/refactor-plan -b feat/refactor-plan main
cd .worktrees/refactor-plan
mkdir -p docs
```

All planning docs land in `docs/`. Do not modify `bot.py` or any runtime code.

## Phase 1 — Explore Current State

Use the `feature-dev:code-explorer` agent. Read `bot.py` in full plus `docs/spec/original-skeleton.md`.

Produce `docs/01-current-state.md`:

- Inventory: every function, class, global, background task, event handler, command (`$`-prefixed)
- Data flow: how `users_data` / `funds_data` / `prices_data` / `fund_penalty_history` are read, mutated, persisted
- Coupling hotspots: where Discord API calls, domain logic, persistence, and price math are intermingled
- Tech debt: race conditions on shared dicts, save-after-every-mutation cost, missing type hints, hardcoded constants, in-memory voice/ping session state lost on restart
- Risk register: what could break during refactor (background-task timing, JSON schema compatibility, opt-in state)
- Mermaid diagram of current module/event/task topology

## Phase 2 — Target Architecture

Use the `code-architect` (or `architect-reviewer`) agent. Input: Phase 1 report.

Produce `docs/02-target-architecture.md`:

- Proposed package layout (concrete tree: `friendex/{config,domain,persistence,discord_io,tasks,commands}` or your justified alternative)
- Module boundaries and dependency direction (domain depends on nothing Discord-specific; Discord layer depends on domain; persistence behind a repository interface)
- Persistence strategy: evaluate replacing JSON-dict-and-`save_data()` with SQLite + SQLAlchemy (or `sqlite3` + repositories). Recommend one with reasoning and a JSON→SQLite migration sketch. Also consider using redis or other lightweight containers.
- Config & secrets: move constants out of the `bot.py` header into `pydantic-settings` or `dataclass` config loaded from `.env`/`config.toml`
- Logging: structured logging (`structlog` or stdlib) replacing `print`
- Error handling and concurrency model for async dict mutations
- Mermaid diagram of target topology

## Phase 3 — Python Expert Review + Migration Plan

Use `python-reviewer` (and `backend-developer` if useful) against the Phase 2 design.

Produce two docs:

**`docs/03-python-review.md`** — Pythonic idioms, PEP 8, full type hints, async correctness for `discord.py` (cog pattern, task lifecycle, `asyncio.Lock` for shared state), packaging via `pyproject.toml`, dependency management with `uv`, lint/format toolchain (`ruff`, `mypy`).

**`docs/04-migration-plan.md`** — Phased refactor ordered by risk, each phase naming concrete files created/modified:

1. create a master issue for the migration that we can track all the separate commits under. 
2. Packaging & tooling scaffold (no behavior change)
3. Extract config and constants
4. Extract domain models (User, Stock, Fund, Position) as pure dataclasses
5. Repository layer behind interfaces (JSON impl first, SQLite impl second)
6. Split commands into cogs
7. Extract background tasks
8. Extract price engine as pure functions
9. Cutover and delete `bot.py`

**`docs/05-testing-strategy.md`** — `pytest` + `pytest-asyncio`, `dpytest` or hand-rolled mocks for Discord API, fixtures for repositories, unit tests for the price engine (deterministic, no Discord), integration tests for cogs, 80% coverage target per project rules. Extract functionality in as small of a unit as  possible. a unit is defined as the smallest testable piece of code. 

## Constraints

- Planning only. No edits to `bot.py`, no new runtime code.
- All docs in `docs/` on the `feat/refactor-plan` branch.
- Direct, technical tone. No apologetic hedging.
- Stop after Phase 3 and report deliverables for review.
