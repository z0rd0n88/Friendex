# Command Reference

Full parameter reference for every Friendex slash command. This is the
canonical, exhaustive source — `README.md` and `CLAUDE.md` carry a summary
table for orientation, but **add new/renamed commands here first**, then
mirror the one-line summary into those two tables. `tests/e2e/test_coverage_matrix.py`
independently guards that every command listed below appears in at least one
e2e scenario — keep the counts in sync.

All commands are `@app_commands.guild_only()` (DMs rejected) except the
`/fund` subcommands, where `guild_only()` is applied once on the parent
`FundGroup` and propagates to every subcommand.

## Account — `account_cog.py`

| Command | Params | Visibility | Notes |
|---|---|---|---|
| `/balance` | — | ephemeral | Cash balance, net worth, and fund summary. |
| `/optin` | — | ephemeral | Consent to be a tradeable stock. First-time callers also receive a one-time "Welcome to Friendex" intro DM (falls back to inline embed if DMs are closed). |
| `/optout` | — | ephemeral | Remove yourself from active trading. |

## Trading — `trading_cog.py`

| Command | Params | Visibility | Notes |
|---|---|---|---|
| `/buy` | `user: Member`, `shares: int (≥1)` | public | Open or add to a long position. |
| `/sell` | `user: Member`, `shares: int (≥1)` | public | Close some/all of a long position. |
| `/short` | `user: Member`, `shares: int (≥1)` | public | Open a short position. 15-min cooldown between short/cover ops; 30-min freeze before manual cover is allowed. |
| `/cover` | `user: Member`, `shares: int (≥1)` | public | Close a short position. |

## Portfolio & stats — `portfolio_cog.py`, `stats_cog.py`

| Command | Params | Visibility | Notes |
|---|---|---|---|
| `/portfolio` | `user: Member \| None` (default: yourself) | ephemeral | Full long/short position view with P&L. |
| `/price` | `user: Member` | ephemeral | Current price + 24h stats for a member's stock. |
| `/mystock` | — | ephemeral | Your own stock's price + 24h stats. Distinct command from `/price`, not `/price` with an implicit self-target. |
| `/trending` | — | public | Top-movers leaderboard for the server. |
| `/mystats` | — | ephemeral | Your personal activity stats (engagement tier + score). |

## Daily — `daily_cog.py`

| Command | Params | Visibility | Notes |
|---|---|---|---|
| `/daily` | — | public | Claim the daily $500 reward. Streak bonus (+$500) every 7th consecutive day. |

## Hedge fund — `fund_cog.py` (`/fund` command group)

| Subcommand | Params | Visibility | Notes |
|---|---|---|---|
| `/fund create` | `name: str (1-32 chars) \| None` | public | Create your fund, or rename an existing one if you already have one. |
| `/fund info` | `user: Member \| None` (default: yourself) | ephemeral | Fund summary — balance, investors, manager. |
| `/fund invest` | `user: Member`, `amount: float` | public | Invest cash into another member's fund. Self-invest is blocked. |
| `/fund withdraw` | `amount: float` | public | Withdraw cash from your own fund back to trading cash. Subject to the early-withdrawal penalty if before month-end. |
| `/fund send_events` | `amount: float` | public | Donate fund cash to the server's events wallet. Exempt from the early-withdrawal penalty. |

## Admin — `admin_cog.py`

| Command | Params | Visibility | Notes |
|---|---|---|---|
| `/help` | — | ephemeral | Lists every slash command (built from `embeds.build_help_embed`). |
| `/game_intro` | — | public | Posts the static intro embed. Gated by `manage_guild` — Discord rejects the command for members without the *Manage Server* permission. |

## Command count

20 commands total (14 top-level + 5 `/fund` subcommands counted individually,
+ `/help`/`/game_intro`), matching the count asserted in
[`tests/e2e/README.md`](../tests/e2e/README.md) and enforced by
`tests/e2e/test_coverage_matrix.py`.
