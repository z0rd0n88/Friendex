# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Resuming Work

If you are joining an in-flight effort, **start by reading [`pass-baton/INDEX.md`](./pass-baton/INDEX.md)** — it lists the most recent pass-baton per feature/epic so you can pick up without re-deriving context. Every write to `pass-baton/` is mediated by the project-scoped `pass-baton` skill (`.claude/skills/pass-baton/SKILL.md`); see `pass-baton/CLAUDE.md` for the directory's hard rules.

## Python Code Style — Mandatory Skill

**Always invoke the `ecc-python-patterns` skill before writing or modifying any Python code in this repository.** Run `/ecc-python-patterns` at the start of any task that touches `.py` files — including bug fixes, refactors, new features, and tests. The skill provides the canonical guidance for PEP 8 compliance, type hints, Pythonic idioms, and project-standard patterns. Do not skip this step even for "small" edits; consistency across the codebase depends on every contribution being checked against the same playbook.

## Project

Friendex is a Discord bot that simulates a stock exchange game. Each server member has their own "stock" that others can buy, sell, or short. Prices rise/fall based on real Discord activity (messages, voice time, reactions) tracked by the bot.

## Development

This is a [`uv`](https://docs.astral.sh/uv/)-managed Python ≥3.11 package. It is a **greenfield rebuild in progress and not yet a runnable bot** — the `friendex` entry point (`friendex.main:main`) is built in Phase 14. Until then it is a library under construction; the loop you exercise is tests, not a live bot.

```bash
uv sync                                          # install deps + dev group
uv run pytest                                     # run tests (coverage-gated)
uv run ruff check . && uv run ruff format --check .
uv run mypy src/friendex
uv run friendex                                   # run the bot — only works once Phase 14 lands
```

A `.env` with `DISCORD_TOKEN` is required (see `.env.example`). Slash commands sync **globally**, so the bot works in any server it is added to — there is no command prefix and no required home guild. `DEV_GUILD_ID` is optional: when set it also syncs commands instantly to that one guild for development. Each server is an isolated economy keyed by `(guild_id, user_id)` — see [ADR-0001](./docs/adr/0001-per-guild-markets.md).

## Repo workflow & PRs

- **`.claude/` is git-tracked here** (skills in `.claude/skills/`, agents in `.claude/agents/`) — edits to them go through a worktree + PR like any code; the global `~/.claude/` commit-to-`main` carve-out does NOT apply. Create worktrees under `.claude/worktrees/<name>` (repo convention; not gitignored — relies on git auto-excluding registered worktrees).
- **Phase status lives in GitHub issue #2, never in-repo.** PRs follow `.github/pull_request_template.md` and reference it (`Refs #2`). For docs/tooling PRs with no Python change, mark the Verification gates **N/A** and note "not a phase PR" in Tracking.
- **Merges auto-delete the head branch** (`deleteBranchOnMerge`), so `git push origin --delete <branch>` errors harmlessly — clean up with `git worktree remove` → `git branch -D` → `git fetch --prune`.
- **Multi-phase builds:** the user-invoked `baton-runner` skill orchestrates implement→review→fix subagent units (see `.claude/skills/baton-runner/`).

## Architecture

Friendex is a **greenfield rebuild** of an original single-file `bot.py` into a **hexagonal (ports-and-adapters)** package under `src/friendex/`. The original monolith no longer exists in the tree — it survives only as the spec at [`docs/spec/original-skeleton.md`](./docs/spec/original-skeleton.md).

> **Authoritative sources — do not re-snapshot them here (that is what rots):**
> - Target architecture → [`docs/02-target-architecture.md`](./docs/02-target-architecture.md)
> - Phased build plan → [`docs/04-migration-plan.md`](./docs/04-migration-plan.md)
> - Testing strategy → [`docs/05-testing-strategy.md`](./docs/05-testing-strategy.md)
> - **Live phase status → GitHub issue #2** (its checklist + merged PRs — never a status line in this repo)

### Layers (`src/friendex/`)

Dependencies point inward only — `adapters → application → domain`. The domain layer imports nothing; adapters (Discord, DB, config) never reach past the application services.

| Layer | Package | Holds |
|-------|---------|-------|
| Domain | `domain/` | Pure dataclass models + invariants (`models.py`), error taxonomy (`errors.py`), and pure functions (price engine, activity, market hours, fund math) |
| Application | `application/` | Use-case services (trading, portfolio, fund, daily, stats, activity, liquidation, …) orchestrating domain logic + repositories |
| Adapters | `adapters/` | `config.py` (`Settings`); `persistence/` (SQLAlchemy + Alembic); `discord_bot/` (`cogs/`, `listeners/`, embed builders, bot factory); `tasks/` (background loops) |

### Current state

Implemented: `Settings` + structured logging (Phase 2); domain models + error taxonomy (Phase 3) — **money fields are `Decimal` and datetimes are UTC-aware** (Phase 3.1 invariant; preserve it in new code). Everything else — domain pure functions, persistence (ORM + migrator), application services, Discord cogs/listeners, background tasks, and the bot entry point — is scaffolded (`__init__.py` only) and built phase-by-phase. **Check issue #2 for what is actually done.**

### Persistence

Domain state is stored in **SQLite via async SQLAlchemy 2.0 + Alembic** (`adapters/persistence/`, behind repository interfaces; `database_url` defaults to `sqlite+aiosqlite:///data/friendex.db`). This replaces the original bot's JSON files (`users.json`, `funds.json`, `prices.json`, `fund_penalties.json`); a one-time JSON→SQLite migrator is part of the cutover. Built in Phases 5–6.

### Price & game rules

Durable game-design facts. Tunables live in `Settings` (`adapters/config.py`), not as module-level constants; the engine itself lands in Phase 4 (domain) and Phase 9 (background loops).

- **Activity ticks** (15-min loop): accumulate text posts, media, reactions, replies, and voice time; apply `ΔP = K · ln(1 + activity)`.
- **Trade impact**: buy/sell/short/cover shift the price immediately via `price_impact_k` (0.5).
- **Inactivity decay**: 4% drop after ~4h idle. `min_price` floor is $70; initial price $100.
- **Background loops** (Phase 9): activity tick, short liquidation (auto-cover at `liquidation_threshold` × entry = 1.5×), hedge-fund APY accrual, early-withdrawal penalty decay.
- Other key tunables: `initial_cash` $10,000, `trade_cooldown_seconds` 900 (short/cover only), `hedge_fund_base_apy` 0.15, `early_withdraw_penalty` 0.05; market hours 06:30–04:30 next day, Mon–Sat (Sunday closed).

### Discord interface

Events are handled by listeners in `adapters/discord_bot/listeners/` (Phase 12): `on_message` (text/media activity, reaction & reply credit, opt-in), `on_reaction_add`, `on_voice_state_update` (VC join/leave + ping-response timing), `on_member_update` (timeout/ban discipline penalty, 17% drop). Commands are cogs in `adapters/discord_bot/cogs/` (Phase 11).

### Bot Commands (slash `/`)

Commands are Discord **slash commands** (`discord.app_commands`), registered with Discord and synced **globally** (available in every server the bot is in; `DEV_GUILD_ID` adds an instant sync to one guild for development). Slash commands have no aliases, so the original `$mb` / `$pf` / `$mp` / `$ticker` aliases are dropped in favour of canonical names plus Discord's built-in autocomplete. Reply visibility replaces the old `delete_after=15` cleanup: personal/read commands reply **ephemerally** (only the invoker sees them); action commands reply **publicly** so trades stay visible in-channel.

| Command | Visibility | Purpose |
|---------|------------|---------|
| `/balance` | ephemeral | Cash + portfolio summary |
| `/daily` | public | Claim daily $500 reward (streak bonus on day 7) |
| `/price [user]` | ephemeral | Look up a stock price |
| `/mystock` | ephemeral | View your own stock stats |
| `/buy <user> <shares>` | public | Long position |
| `/sell <user> <shares>` | public | Close long position |
| `/short <user> <shares>` | public | Open short (15-min cooldown, 30-min freeze) |
| `/cover <user> <shares>` | public | Close short |
| `/portfolio [user]` | ephemeral | Full portfolio view |
| `/fund <subcommand>` | info ephemeral, mutations public | Hedge fund management (create/invest/withdraw/info) |
| `/trending` | public | Top movers leaderboard |
| `/mystats` | ephemeral | Personal activity stats |
| `/optin` · `/optout` | ephemeral | Consent to be a tradeable stock |
