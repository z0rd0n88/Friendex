# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

StockXchange is a Discord bot that simulates a stock exchange game. Each server member has their own "stock" that others can buy, sell, or short. Prices rise/fall based on real Discord activity (messages, voice time, reactions) tracked by the bot.

## Running the Bot

```bash
pip install discord.py python-dotenv
python bot.py
```

Requires a `.env` file with `DISCORD_TOKEN=<token>`. The `data/` directory is auto-created on first run.

## Architecture

**Single-file bot** — all logic lives in `bot.py`. The spec/skeleton is in `Slut Stock xXxchange [Overview + Dev Brief + Code Skeleton].md`.

### Data Layer

Four in-memory dicts, each backed by a JSON file in `data/`:

| Dict | File | Contents |
|------|------|----------|
| `users_data` | `users.json` | accounts, portfolios, activity metrics, streaks |
| `funds_data` | `funds.json` | hedge fund definitions, investor lists |
| `prices_data` | `prices.json` | current prices, price history per user |
| `fund_penalty_history` | `fund_penalties.json` | early-withdrawal APY penalty records |

`load_data()` / `save_data()` handle all persistence. Save after every mutation.

### Price Engine

Prices move through two independent mechanisms:
1. **Activity ticks** (every 15 min via `discord.ext.tasks`): accumulates text posts, media, reactions, replies, voice time; applies `ΔP = K * ln(1 + activity)` formula.
2. **Trade impact**: buy/sell/short/cover each shift the price immediately using `PRICE_IMPACT_K = 0.5`.

Inactivity decay (4% drop) fires when a user hasn't posted for 4+ hours. `MIN_PRICE = $70` is the floor; initial price is `$100`.

### Voice & Activity Tracking

Two in-memory session dicts (not persisted — reset on restart):
- `voice_sessions`: tracks active VC participants, start time, whether they joined via a ping.
- `voice_ping_sessions`: records VC ping messages and their first-10 / extra responders for bonus calculation.

VC ping roles are hardcoded in `VC_PING_ROLES`. Photo bonus channels in `PHOTO_BONUS_CHANNEL_IDS`.

### Background Tasks (`discord.ext.tasks`)

- Activity tick loop (15-min): compute price changes from accumulated activity.
- Liquidation check loop: auto-cover short positions at 150% of entry price.
- Hedge fund APY loop: accrue 15% nominal monthly APY for fund investors.
- Penalty decay loop: expire early-withdrawal penalties after 14 days.

### Discord Events Handled

`on_message` — text/media activity, reaction credit, reply credit, opt-in check.
`on_reaction_add` — reaction activity ticks.
`on_voice_state_update` — join/leave tracking, VC ping response timing.
`on_member_update` — timeout/ban discipline penalty (17% price drop).

### Bot Commands (prefix `$`)

| Command | Purpose |
|---------|---------|
| `$balance` / `$mb` | Cash + portfolio summary |
| `$daily` | Claim daily $500 reward (streak bonus on day 7) |
| `$price <@user>` / `$ticker` | Look up a stock price |
| `$my_stock` | View your own stock stats |
| `$buy <@user> <shares>` | Long position |
| `$sell <@user> <shares>` | Close long position |
| `$short <@user> <shares>` | Open short (15-min cooldown, 30-min freeze) |
| `$cover <@user> <shares>` | Close short |
| `$portfolio` / `$pf` / `$mp` | Full portfolio view |
| `$fund <subcommand>` | Hedge fund management (create/invest/withdraw/info) |
| `$trending` | Top movers leaderboard |
| `$mystats` | Personal activity stats |
| `$optin` / `$optout` | Consent to be a tradeable stock |

### Key Constants (all in `bot.py` header)

- Market hours: 06:30–04:30 next day, Mon–Sat (Sunday closed)
- `TIMEZONE_OFFSET_HOURS = 0` — adjust for server locale
- `INITIAL_CASH = $10,000`, `INITIAL_PRICE = $100`
- `TRADE_COOLDOWN_SECONDS = 900` — applies only to short/cover
- `LIQUIDATION_THRESHOLD = 1.5` — short auto-covers at 150% of entry
- `HEDGE_FUND_BASE_APY = 0.15`, `EARLY_WITHDRAW_PENALTY = 0.05`
