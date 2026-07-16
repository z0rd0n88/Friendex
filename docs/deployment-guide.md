# Friendex Deployment Guide

## Executive Summary

This guide covers everything needed to get Friendex running in production: creating the
Discord application, configuring environment variables, running the database migration,
and starting the bot. The bot is a single Python process; the only runtime dependency is
a writable filesystem for SQLite.

Two hosting options are documented:
- **Railway** (§8) — recommended for quick cloud deployment; handles builds, restarts, and persistent storage automatically.
- **Self-hosted via systemd** (§7) — for VPS or bare-metal installs.

---

## Prerequisites

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/) (Python package/project manager)
- A Discord account with permission to create bot applications
- A Linux host with write access to the working directory (for the SQLite database)

---

## 1. Create the Discord application and bot token

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications)
   and click **New Application**.
2. Name your application, then navigate to the **Bot** tab.
3. Click **Reset Token** to generate a bot token. Copy it — you will need it in step 4.
4. Under **Privileged Gateway Intents**, enable:
   - **Server Members Intent** (required for member join/ban events and opt-in enforcement)
   - **Message Content Intent** (required for activity tracking)
5. Navigate to **OAuth2 > URL Generator**. Under Scopes, select `bot` and
   `applications.commands`. Under Bot Permissions, select at minimum:
   `Send Messages`, `Embed Links`, `Read Message History`, `View Channels`.
   Copy the generated URL and use it to add the bot to your server(s).

Discord's official documentation: [https://discord.com/developers/docs/intro](https://discord.com/developers/docs/intro)

---

## 2. Clone and install

```bash
git clone https://github.com/z0rd0n88/Friendex.git
cd Friendex
uv sync
```

`uv sync` installs all runtime dependencies into a local `.venv/`. No system-wide
Python packages are modified.

---

## 3. Configure the environment

Copy the example file and edit it:

```bash
cp .env.example .env
```

Open `.env` in an editor and set values for your deployment.

### Required variable

| Variable        | Description                                         |
| --------------- | --------------------------------------------------- |
| `DISCORD_TOKEN` | The bot token from step 1. Never commit this value. |

### Important optional variables

| Variable                | Default                                | Description                                                                                                                                                                                    |
| ----------------------- | -------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`          | `sqlite+aiosqlite:///data/friendex.db` | SQLAlchemy async URL for the database. The default creates `data/friendex.db` relative to the working directory.                                                                               |
| `DEV_GUILD_ID`          | _(unset)_                              | Discord server ID. When set, slash commands are also synced instantly to this guild for faster iteration. Leave unset in production; global propagation takes up to 1 hour after first launch. |
| `MARKET_OPEN`           | `06:30`                                | Market open time (24h UTC).                                                                                                                                                                    |
| `MARKET_CLOSE`          | `04:30`                                | Market close time (24h UTC). The market spans overnight — open from 06:30 through 04:30 the following morning, Mon–Sat. Sunday is closed.                                                      |
| `TIMEZONE_OFFSET_HOURS` | `0`                                    | UTC offset applied to market hour calculations.                                                                                                                                                |
| `LOG_LEVEL`             | `INFO`                                 | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`.                                                                                                                                        |
| `LOG_FORMAT`            | `json`                                 | Log output format. Use `json` in production (structured, one record per line). Use `console` for human-readable development output.                                                            |

### Game tuning variables

These have sensible defaults and rarely need changing for a standard deployment.

| Variable                 | Default | Description                                                                              |
| ------------------------ | ------- | ---------------------------------------------------------------------------------------- |
| `INITIAL_CASH`           | `10000` | Starting cash balance for new users.                                                     |
| `INITIAL_PRICE`          | `100`   | Starting stock price for new users.                                                      |
| `MIN_PRICE`              | `70`    | Absolute price floor — no stock can drop below this.                                     |
| `PRICE_IMPACT_K`         | `0.5`   | Linear scaling factor for trade-driven price moves.                                      |
| `INACTIVITY_DECAY`       | `0.04`  | Price drop (4%) applied after ~4 hours of inactivity.                                    |
| `LIQUIDATION_THRESHOLD`  | `1.5`   | Short positions are auto-covered when the current price reaches 150% of the entry price. |
| `SHORT_FREEZE_MINUTES`   | `30`    | Minutes before a short position is frozen (prevents manual cover).                       |
| `TRADE_COOLDOWN_SECONDS` | `900`   | Cooldown between short/cover operations per user (15 minutes).                           |
| `DISCIPLINE_PENALTY`     | `0.17`  | Price drop (17%) applied when a user is timed out or banned.                             |

### Discord role and channel IDs

| Variable                  | Description                                                                                                                |
| ------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `VC_PING_ROLE_IDS`        | Comma-separated list of role IDs whose @mentions trigger VC ping bonuses. Example: `111111111111111111,222222222222222222` |
| `PHOTO_BONUS_CHANNEL_IDS` | Comma-separated list of channel IDs where media posts receive an extra activity bonus.                                     |

### Hedge fund variables

| Variable                     | Default   | Description                                                                                          |
| ---------------------------- | --------- | ---------------------------------------------------------------------------------------------------- |
| `HEDGE_FUND_BASE_APY`        | `0.15`    | Base annual percentage yield for hedge fund balances.                                                |
| `HEDGE_FUND_BASE_APY_PERIOD` | `monthly` | How APY is credited: `monthly` (balance × apy / 12 per cycle) or `annual` (full balance × apy once). |
| `EARLY_WITHDRAW_PENALTY`     | `0.05`    | APY reduction applied per early withdrawal.                                                          |
| `PENALTY_DURATION_DAYS`      | `14`      | Days a withdrawal penalty remains active.                                                            |

### Trading toggles

| Variable                 | Default | Description                                                                                      |
| ------------------------ | ------- | ------------------------------------------------------------------------------------------------ |
| `SUNDAY_BUY_ALLOWED`     | `true`  | When `true`, `/buy` is permitted on Sundays even though `/sell`, `/short`, and `/cover` are not. |
| `OPT_OUT_BLOCKS_TRADING` | `true`  | When `true`, a user who has run `/optout` cannot be bought, sold, or shorted.                    |

---

## 4. Database setup

Create the SQLite database and apply all migrations:

```bash
uv run alembic upgrade head
```

This creates the `data/` directory and `data/friendex.db` (or whatever path your
`DATABASE_URL` points to) with the complete schema. Run this command on every deploy
that updates the code — Alembic is idempotent when the schema is already up to date.

---

## 5. Run the bot

```bash
uv run friendex
```

The bot will:

1. Load settings from `.env`
2. Configure structured logging
3. Connect to the database
4. Register all slash commands globally with Discord (first launch may take up to 1 hour
   to propagate; set `DEV_GUILD_ID` for instant sync to one server)
5. Start all background tasks (activity tick, inactivity decay, short liquidation,
   daily/weekly resets, monthly rollover)
6. Begin accepting events

---

## 6. Migrating from old JSON data files

If you are migrating from an earlier version of Friendex that stored data in JSON files
(`users.json`, `prices.json`, `funds.json`, `fund_penalties.json`), run the one-time
migrator before starting the bot for the first time.

**Before migrating:** ensure `uv run alembic upgrade head` has been run so the schema exists.

```bash
uv run python -m friendex.adapters.persistence.migrate_json_to_sqlite \
  --source data/ \
  --target sqlite+aiosqlite:///data/friendex.db \
  --guild-id YOUR_DISCORD_GUILD_ID
```

Replace `YOUR_DISCORD_GUILD_ID` with your server's Discord ID (right-click the server
icon in Discord > Copy Server ID). This is required because the new schema stores data
per-guild.

**Flags:**

| Flag              | Description                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `--source <dir>`  | Directory containing the JSON files. Defaults to `data/`.                                                                |
| `--target <url>`  | SQLAlchemy async URL for the destination database.                                                                       |
| `--guild-id <id>` | Discord guild ID to tag all migrated rows with. Required.                                                                |
| `--dry-run`       | Parse and validate the JSON files without writing to the database. Useful for checking data integrity before committing. |
| `--report`        | Print a `<table>: <count>` summary after the run. Composes with `--dry-run`.                                             |

The migrator is **idempotent** — running it twice produces no duplicates. The original
JSON files are not deleted.

**Recommended workflow:**

```bash
# 1. Dry run to check for issues
uv run python -m friendex.adapters.persistence.migrate_json_to_sqlite \
  --source data/ \
  --target sqlite+aiosqlite:///data/friendex.db \
  --guild-id YOUR_GUILD_ID \
  --dry-run --report

# 2. Migrate for real
uv run python -m friendex.adapters.persistence.migrate_json_to_sqlite \
  --source data/ \
  --target sqlite+aiosqlite:///data/friendex.db \
  --guild-id YOUR_GUILD_ID \
  --report

# 3. Start the bot
uv run friendex
```

---

## 7. Running as a systemd service

For production, run the bot under systemd so it restarts automatically after crashes
or reboots.

Create `/etc/systemd/system/friendex.service`:

```ini
[Unit]
Description=Friendex Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=friendex
Group=friendex
WorkingDirectory=/opt/friendex
EnvironmentFile=/opt/friendex/.env
ExecStart=/opt/friendex/.venv/bin/friendex
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=friendex

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/opt/friendex/data

[Install]
WantedBy=multi-user.target
```

Adjust `WorkingDirectory`, `EnvironmentFile`, `ExecStart`, and `ReadWritePaths` to
match your install path. The `ExecStart` path uses the virtualenv's Python entry point
created by `uv sync`.

**Enable and start:**

```bash
sudo systemctl daemon-reload
sudo systemctl enable friendex
sudo systemctl start friendex
sudo systemctl status friendex
```

**View logs:**

```bash
sudo journalctl -u friendex -f
```

Since `LOG_FORMAT=json` is the default, each log line is a JSON object. Parse with
`jq` for filtering:

```bash
sudo journalctl -u friendex -f | jq 'select(.level == "error")'
sudo journalctl -u friendex -f | jq 'select(.event | startswith("trade."))'
```

---

## 8. Deploying to Railway

[Railway](https://railway.app) runs Friendex as a persistent worker process with
automatic deploys on every push to `main`. No Dockerfile is needed — Railway's Nixpacks
detects uv from `uv.lock` and builds the environment automatically. The `railway.toml`
at the repo root wires up the start command and restart policy.

### Prerequisites

- A [Railway](https://railway.app) account (free tier works; Hobby plan recommended for always-on uptime)
- The repo pushed to GitHub (already at `z0rd0n88/Friendex`)

### Step 1 — Create the Railway project

1. In the Railway dashboard click **New Project → Deploy from GitHub repo**.
2. Select `z0rd0n88/Friendex`.
3. Railway detects `railway.toml` and queues the first build. **Let it fail** — you
   haven't set the environment variables yet.

### Step 2 — Add a persistent volume for SQLite

Railway containers are ephemeral; the database must live on a volume.

1. In your service, go to **Settings → Volumes → Add Volume**.
2. Set the **Mount Path** to `/data`.
3. Railway provisions the volume and remounts it on every deploy.

### Step 3 — Set environment variables

In your service go to **Variables** and add the following. Values that match the
`.env.example` defaults can be left unset; Railway picks them up from `railway.toml`
only if you override `startCommand`, otherwise let the app defaults apply.

**Required:**

| Variable        | Value                                    |
| --------------- | ---------------------------------------- |
| `DISCORD_TOKEN` | Your bot token from Discord Developer Portal |
| `DATABASE_URL`  | `sqlite+aiosqlite:////data/friendex.db`  |

> The four slashes in `DATABASE_URL` are intentional: SQLAlchemy async SQLite uses
> `sqlite+aiosqlite:///` as the scheme prefix, then `/<absolute-path>` — so four
> slashes total for an absolute path starting at `/data`.

**Optional** (override only if you want non-default values):

| Variable      | Default | Notes                                          |
| ------------- | ------- | ---------------------------------------------- |
| `LOG_FORMAT`  | `json`  | Keep `json` in production for structured logs. |
| `LOG_LEVEL`   | `INFO`  | Set to `DEBUG` for troubleshooting.            |
| `DEV_GUILD_ID`| _(unset)_ | Leave unset in production.                  |

All game-tuning variables from §3 are optional and fall back to their defaults if unset.

### Step 4 — Deploy

Click **Deploy** (or push to `main`). Railway runs:

```
uv run alembic upgrade head && uv run friendex
```

Alembic creates `data/friendex.db` on the volume on the first deploy and is a no-op on
subsequent deploys when the schema is already up to date.

### Step 5 — View logs

In the Railway dashboard, select your service and click **Logs**. Since `LOG_FORMAT`
defaults to `json`, each line is a JSON object. You can filter in the Railway UI or
stream logs locally with the Railway CLI:

```bash
railway logs --follow
```

### Redeploys and rollbacks

Every push to `main` triggers a new deploy. Railway keeps the previous deploy available
for one-click rollback in the **Deployments** tab. The SQLite volume persists across
redeploys and rollbacks — it is never reset automatically.

---

## 9. Smoke-testing after deploy

After the bot is running, verify each command works as expected by following the
manual checklist in [docs/runbook-smoke-test.md](./runbook-smoke-test.md).

The smoke test script at `scripts/smoke_test_commands.py` prints the ordered list of
commands to execute with their expected outcomes:

```bash
uv run python scripts/smoke_test_commands.py
```


