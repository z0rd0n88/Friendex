# Friendex

Discord bot that simulates a per-guild stock exchange game. Each server member is a
tradeable "stock" whose price moves based on real Discord activity (messages, voice
time, reactions) and direct trade pressure from buy/sell/short/cover commands.

The bot is complete and deployable. See [docs/deployment-guide.md](./docs/deployment-guide.md)
for production setup instructions.

## Commands

| Command | Visibility | Purpose |
|---------|-----------|---------|
| `/balance` | ephemeral | Cash balance, net worth, and fund summary |
| `/daily` | public | Claim daily $500 reward (streak bonus on day 7) |
| `/price [user]` | ephemeral | Look up a user's current stock price |
| `/mystock` | ephemeral | View your own stock stats |
| `/buy <user> <shares>` | public | Open a long position |
| `/sell <user> <shares>` | public | Close a long position |
| `/short <user> <shares>` | public | Open a short (15-min cooldown, 30-min freeze) |
| `/cover <user> <shares>` | public | Close a short position |
| `/portfolio [user]` | ephemeral | Full portfolio view with P&L |
| `/fund <subcommand>` | info ephemeral; mutations public | Hedge fund management (create/rename/info/invest/withdraw/send_events) |
| `/trending` | public | Top movers leaderboard |
| `/mystats` | ephemeral | Personal activity stats |
| `/optin` | ephemeral | Consent to be a tradeable stock |
| `/optout` | ephemeral | Remove yourself from active trading |
| `/help` | ephemeral | List every slash command |
| `/game_intro` | public, `manage_guild`-gated | Post the intro embed (moderator onboarding) |

Slash commands sync globally — the bot works in every server it joins. Set `DEV_GUILD_ID`
for instant command sync to one guild during development. Full parameter reference:
[docs/command-reference.md](./docs/command-reference.md).

## Quick start — development

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/z0rd0n88/Friendex.git
cd Friendex
uv sync
cp .env.example .env        # edit DISCORD_TOKEN at minimum
uv run alembic upgrade head # create the SQLite database
uv run pytest               # run test suite
uv run friendex             # start the bot
```

## Quick start — production

See [docs/deployment-guide.md](./docs/deployment-guide.md) for the full guide, including
environment variables, database setup, the JSON-to-SQLite migrator for existing data, and
a sample systemd unit file.

## Architecture

Friendex is a **hexagonal (ports-and-adapters)** Python package. Dependencies point
strictly inward — adapters can import anything, application services import only domain
and repository interfaces, and the domain layer imports nothing outside the standard
library.

| Layer | Package | Contents |
|-------|---------|---------|
| Domain | `src/friendex/domain/` | Pure dataclasses and functions (price engine, activity scoring, market hours, fund math) |
| Application | `src/friendex/application/` | Use-case services (trading, portfolio, fund, daily, activity, liquidation, discipline) |
| Adapters | `src/friendex/adapters/` | `config.py` (Settings), `persistence/` (SQLAlchemy + Alembic), `discord_bot/` (cogs, listeners, embeds), `tasks/` (background loops) |

Persistence is **SQLite via async SQLAlchemy 2.0 + Alembic**. Each Discord server is an
isolated economy keyed by `(guild_id, user_id)`.

## Docs

- [docs/deployment-guide.md](./docs/deployment-guide.md) — production deployment
- [docs/command-reference.md](./docs/command-reference.md) — full slash command parameter reference
- [docs/runbook-smoke-test.md](./docs/runbook-smoke-test.md) — post-deploy smoke test checklist
- [docs/02-target-architecture.md](./docs/02-target-architecture.md) — architecture detail
- [docs/05-testing-strategy.md](./docs/05-testing-strategy.md) — test pyramid and toolchain
- [docs/adr/](./docs/adr/) — architecture decision records
- [ARCH.md](./ARCH.md) — auto-maintained file-tree map
